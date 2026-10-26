"""Point-in-time corporate actions, universe membership and instrument availability."""
import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.types import DailyBar, MarketHistory


class CorporateAction(BaseModel):
    """A split (``value`` = new shares per old share) or cash per share paid to holders at the ex-date."""

    model_config = ConfigDict(extra="forbid")
    symbol: str
    kind: Literal["split", "dividend", "distribution"]
    ex_date: datetime.date
    value: float = Field(gt=0, allow_inf_nan=False)
    pay_date: datetime.date | None = None
    published_at: datetime.datetime | None = None
    processed_on: datetime.date | None = None

    def known_by(self, cutoff: datetime.date) -> bool:
        published = self.published_at is None or self.published_at.date() <= cutoff
        return self.ex_date <= cutoff and published


class MembershipInterval(BaseModel):
    """Inclusive dates on which a symbol belonged to the research universe."""

    model_config = ConfigDict(extra="forbid")
    start: datetime.date
    end: datetime.date | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("Membership must end on or after its start")
        return self

    def contains(self, day):
        return self.start <= day and (self.end is None or day <= self.end)


class InstrumentNotes(BaseModel):
    """Provider availability and structural notes needed to interpret an instrument's returns.

    ``delisted_on`` is the first session the symbol no longer trades; holdings
    settle at the last available close. ``roll_exposure`` describes futures-based
    funds whose returns include roll yield.
    """

    model_config = ConfigDict(extra="forbid")
    kind: Literal["stock", "etf", "commodity_fund"] | None = None
    available_from: datetime.date | None = None
    delisted_on: datetime.date | None = None
    delisting_inferred: bool = False
    roll_exposure: str | None = None


class MarketEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corporate_actions: list[CorporateAction] = []
    membership: dict[str, list[MembershipInterval]] = {}
    instruments: dict[str, InstrumentNotes] = {}

    def is_empty(self):
        return not (self.corporate_actions or self.membership or self.instruments)

    def member(self, symbol, day):
        """Symbols without recorded intervals are treated as permanent members."""
        intervals = self.membership.get(symbol)
        notes = self.instruments.get(symbol)
        if notes is not None and notes.available_from is not None and day < notes.available_from:
            return False
        return intervals is None or any(interval.contains(day) for interval in intervals)

    def actions_on(self, day, kinds):
        return [a for a in self.corporate_actions if a.ex_date == day and a.kind in kinds]


def adjust_bars(bars, actions):
    """Back-adjust raw bars for splits and cash payments, giving total-return research prices.

    Bars before a split's ex-date are divided by its ratio (volume multiplied).
    Bars before a cash payment's ex-date are scaled by 1 - cash / prior close.
    """
    factors = [1.0] * len(bars)
    volume_factors = [1.0] * len(bars)
    for action in actions:
        before = [index for index, bar in enumerate(bars) if bar.date < action.ex_date]
        if not before:
            continue
        if action.kind == "split":
            price_factor, volume_factor = 1 / action.value, action.value
        else:
            prior_close = bars[before[-1]].close
            if action.value >= prior_close:
                raise ValueError("Cash distribution must be smaller than the prior close")
            price_factor, volume_factor = 1 - action.value / prior_close, 1.0
        for index in before:
            factors[index] *= price_factor
            volume_factors[index] *= volume_factor
    return [DailyBar(b.date, b.open * f, b.high * f, b.low * f, b.close * f, b.volume * v)
            for b, f, v in zip(bars, factors, volume_factors)]


def research_history(history: MarketHistory, events: MarketEvents, cutoff: datetime.date) -> MarketHistory:
    """Adjust every series with only the actions known by ``cutoff``."""
    known = {}
    for action in events.corporate_actions:
        if action.known_by(cutoff):
            known.setdefault(action.symbol, []).append(action)

    def adjusted(symbol, bars):
        return adjust_bars(bars, known[symbol]) if symbol in known else bars
    return MarketHistory({s: adjusted(s, b) for s, b in history.stocks.items()},
                         adjusted("SPY", history.spy),
                         {s: adjusted(s, b) for s, b in history.sectors.items()})


def validate_events(events: MarketEvents, history: MarketHistory, price_basis: str):
    symbols = {"SPY", *history.stocks, *history.sectors}
    for action in events.corporate_actions:
        if action.symbol not in symbols:
            raise ValueError(f"Corporate action references unknown symbol {action.symbol}")
        if action.kind == "split" and price_basis != "raw":
            raise ValueError("Split events require raw prices; split-adjusted bars would be adjusted twice")
        # Split shares are often distributed before the ex-date; only cash timing matters to the ledger.
        if action.kind != "split" and action.pay_date is not None and action.pay_date < action.ex_date:
            raise ValueError("Cash payment date must not precede the ex-date")
