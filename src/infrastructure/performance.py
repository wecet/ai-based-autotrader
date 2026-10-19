"""Read-only broker reporting, published atomically for the credential-free UI."""
from datetime import datetime, timedelta, timezone
import logging
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.storage import write_json

NY = ZoneInfo("America/New_York")


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Fill(ReportModel):
    id: str
    time: datetime
    symbol: str
    side: Literal["buy", "sell", "sell_short", "buy_to_cover"]
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)


class PerformanceDay(ReportModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    pnl: float | None
    trades: list[Fill]


class PerformanceReport(ReportModel):
    updated_at: datetime
    timezone: Literal["America/New_York"] = "America/New_York"
    totals: dict[Literal["24h", "week", "month", "all"], float | None]
    days: list[PerformanceDay]


def read_performance(path):
    return PerformanceReport.model_validate_json(path.read_text())


def history_points(history):
    """Daily broker P/L is cumulative; never sum the raw values."""
    stamps, values = history["timestamp"], history["profit_loss"]
    if len(stamps) != len(values):
        raise ValueError("Incomplete portfolio history")
    points = []
    for stamp, value in zip(stamps, values):
        if value is not None:
            points.append((datetime.fromtimestamp(stamp, timezone.utc), float(value)))
    return sorted(points)


class PerformanceRecorder:
    def __init__(self, client, path):
        self.client = client
        self.path = path
        self.next_refresh = datetime.min.replace(tzinfo=timezone.utc)

    def refresh(self, now=None):
        now = now or datetime.now(timezone.utc)
        if now < self.next_refresh:
            return
        self.next_refresh = now + timedelta(minutes=5)
        try:
            report = self.collect(now)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, report.model_dump(mode="json"))
        except Exception:
            # Reporting must not interrupt trading or replace a valid cache.
            logging.exception("Performance refresh failed; retaining the previous report")

    def collect(self, now):
        account = self.client.get_account()
        start = account.created_at.isoformat()
        history = self.client.get("/account/portfolio/history", {
            "start": start, "end": now.isoformat(), "timeframe": "1D",
            "cashflow_types": "ALL",
        })
        points = history_points(history)
        days = {}
        previous = 0.0
        for stamp, cumulative in points:
            day = stamp.astimezone(NY).date().isoformat()
            days[day] = {"date": day, "pnl": round(cumulative - previous, 2), "trades": []}
            previous = cumulative

        # Intraday P/L uses one baseline across the requested window, so its
        # difference measures a rolling 24 hours even across midnight/weekends.
        intraday = history_points(self.client.get("/account/portfolio/history", {
            "start": (now - timedelta(days=2)).isoformat(), "end": now.isoformat(),
            "timeframe": "5Min", "intraday_reporting": "continuous",
            "pnl_reset": "no_reset", "cashflow_types": "ALL",
        }))
        cutoff = now - timedelta(hours=24)
        baseline = [value for stamp, value in intraday if stamp <= cutoff]
        rolling = round(intraday[-1][1] - baseline[-1], 2) if baseline and intraday else None

        today = now.astimezone(NY).date()
        midnight = datetime.combine(today, datetime.min.time(), tzinfo=NY)
        closing = [value for stamp, value in intraday if stamp <= midnight]
        if intraday and closing and intraday[-1][0] >= midnight:
            day = today.isoformat()
            days[day] = {"date": day, "pnl": round(intraday[-1][1] - closing[-1], 2), "trades": []}

        token = None
        seen = set()
        for _ in range(1000):
            params = {"direction": "asc", "page_size": 100, "until": now.isoformat()}
            if token:
                params["page_token"] = token
            fills = self.client.get("/account/activities/FILL", params)
            for item in fills:
                if item["id"] in seen:
                    raise ValueError("Repeated fill in broker pagination")
                seen.add(item["id"])
                fill = Fill(id=item["id"], time=item["transaction_time"],
                            symbol=item["symbol"], side=item["side"],
                            quantity=item["qty"], price=item["price"])
                if fill.time.tzinfo is None:
                    raise ValueError("Fill timestamp must include a timezone")
                day = fill.time.astimezone(NY).date().isoformat()
                days.setdefault(day, {"date": day, "pnl": None, "trades": []})["trades"].append(fill)
            if len(fills) < 100:
                break
            token = fills[-1]["id"]
        else:
            raise ValueError("Broker fill history exceeded the reporting page limit")

        daily_values = [day["pnl"] for day in days.values() if day["pnl"] is not None]
        totals = {"24h": rolling, "all": round(sum(daily_values), 2) if daily_values else None}
        for name, length in (("week", 7), ("month", 30)):
            cutoff_day = (today - timedelta(days=length - 1)).isoformat()
            values = [day["pnl"] for day in days.values()
                      if cutoff_day <= day["date"] <= today.isoformat() and day["pnl"] is not None]
            totals[name] = round(sum(values), 2) if daily_values else None
        return PerformanceReport(updated_at=now, totals=totals,
                                 days=[days[day] for day in sorted(days)])
