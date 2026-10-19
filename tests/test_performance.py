"""Broker-free reporting checks: cumulative P/L, dates, pagination and failures."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from infrastructure.performance import PerformanceRecorder, read_performance

UTC = timezone.utc
NOW = datetime(2026, 10, 19, 18, tzinfo=UTC)


def history(points):
    return {"timestamp": [int(stamp.timestamp()) for stamp, _ in points],
            "profit_loss": [value for _, value in points]}


def fill(identifier="one", stamp="2026-10-19T01:00:00Z"):
    return {"id": identifier, "transaction_time": stamp, "symbol": "AAA",
            "side": "buy", "qty": "0.125", "price": "100.50"}


class Broker:
    def __init__(self):
        self.calls = []
        self.pages = [[fill()]]
        self.daily = history([
            (datetime(2026, 9, 1, 20, tzinfo=UTC), 10),
            (datetime(2026, 10, 16, 20, tzinfo=UTC), 35),
            (datetime(2026, 10, 19, 18, tzinfo=UTC), 999),
        ])
        self.intraday = history([
            (NOW - timedelta(hours=25), 10),
            (NOW - timedelta(hours=24), 12),
            (datetime(2026, 10, 19, 4, tzinfo=UTC), 15),
            (NOW, 5),
        ])

    def get_account(self):
        return SimpleNamespace(created_at=datetime(2026, 9, 1, tzinfo=UTC))

    def get(self, path, params):
        self.calls.append((path, params.copy()))
        if path.endswith("/history"):
            return self.daily if params["timeframe"] == "1D" else self.intraday
        return self.pages[1] if "page_token" in params else self.pages[0]


def test_daily_cumulative_values_are_differenced_and_current_day_updated(tmp_path):
    report = PerformanceRecorder(Broker(), tmp_path / "performance.json").collect(NOW)
    assert report.totals == {"24h": -7, "week": 15, "month": 15, "all": 25}
    days = {day.date: day for day in report.days}
    assert days["2026-09-01"].pnl == 10
    assert days["2026-10-16"].pnl == 25
    assert days["2026-10-19"].pnl == -10
    # 01:00 UTC falls on the preceding New York date, even on a weekend.
    assert days["2026-10-18"].pnl is None
    assert days["2026-10-18"].trades[0].quantity == .125


def test_all_fill_pages_are_read_and_safe_fields_are_published(tmp_path):
    broker = Broker()
    broker.pages = [[fill(str(index)) for index in range(100)], [fill("last")]]
    recorder = PerformanceRecorder(broker, tmp_path / "performance.json")
    recorder.refresh(NOW)
    report = read_performance(recorder.path)
    assert sum(len(day.trades) for day in report.days) == 101
    assert broker.calls[-1][1]["page_token"] == "99"
    assert "order_id" not in recorder.path.read_text()
    recorder.refresh(NOW + timedelta(minutes=1))
    assert len(broker.calls) == 4


def test_failed_refresh_keeps_last_valid_report_and_does_not_raise(tmp_path):
    broker = Broker()
    recorder = PerformanceRecorder(broker, tmp_path / "performance.json")
    recorder.refresh(NOW)
    previous = recorder.path.read_text()
    broker.daily["profit_loss"] = [float("nan")] * 3
    recorder.refresh(NOW + timedelta(minutes=6))
    assert recorder.path.read_text() == previous


def test_repeated_pagination_is_rejected(tmp_path):
    broker = Broker()
    broker.pages = [[fill(str(index)) for index in range(100)], [fill("99")]]
    with pytest.raises(ValueError, match="Repeated fill"):
        PerformanceRecorder(broker, tmp_path / "performance.json").collect(NOW)


def test_empty_history_is_unavailable_instead_of_fabricated_zero(tmp_path):
    broker = Broker()
    broker.daily = history([])
    broker.intraday = history([])
    broker.pages = [[]]
    report = PerformanceRecorder(broker, tmp_path / "performance.json").collect(NOW)
    assert all(value is None for value in report.totals.values())
    assert report.days == []


def test_reporting_continues_when_trading_is_paused(tmp_path):
    from unittest.mock import Mock
    from trading.runner import Autotrader
    broker = Broker()
    recorder = PerformanceRecorder(broker, tmp_path / "performance.json")
    runner = Autotrader(SimpleNamespace(trading_client=broker), Mock(),
                        {"AAA": "XLK", "BBB": "XLF"}, tmp_path, performance=recorder)
    runner.trading_enabled = False
    runner.run_once()
    assert recorder.path.exists()
