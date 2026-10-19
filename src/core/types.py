"""Broker-independent data and portfolio contracts shared by strategies."""
from dataclasses import dataclass
import datetime


@dataclass(frozen=True)
class DailyBar:
    """A completed, split-adjusted daily OHLCV bar."""

    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeatureRow:
    date: datetime.date
    symbol: str
    sector: str
    close: float
    average_dollar_volume: float
    values: tuple[float, ...]
    future_return_5d: float | None = None
    target_rank: float | None = None


@dataclass(frozen=True)
class TradeCandidate:
    """One symbol's desired state after ranking and risk constraints."""

    symbol: str
    predicted_return: float | None
    percentile_rank: float
    price: float
    sector: str
    desired_weight: float
    current_weight: float = 0.0
    portfolio_value: float = 0.0
    estimated_spread: float = 0.0
    estimated_slippage: float = 0.0

    @property
    def order_notional(self) -> float:
        return abs(self.desired_weight - self.current_weight) * self.portfolio_value



@dataclass(frozen=True)
class MarketHistory:
    stocks: dict[str, list[DailyBar]]
    spy: list[DailyBar]
    sectors: dict[str, list[DailyBar]]


@dataclass(frozen=True)
class PortfolioLimits:
    maximum_total_exposure: float = .5
    maximum_sector_exposure: float = .2
    maximum_position: float = .1
    top_n: int = 5
