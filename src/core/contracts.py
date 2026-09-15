"""Stable interfaces implemented by discoverable models and broker adapters."""
from abc import ABC, abstractmethod
from typing import Any, Literal, Mapping

from core.types import MarketHistory, PortfolioLimits, TradeCandidate

TradeAction = Literal["buy", "sell", "hold"]


class Model(ABC):
    """A strategy owns preparation and target generation, but never submits orders."""

    minimum_order_notional: float = 10.0

    def configure(self, settings) -> None:
        """Apply common controls; overrides should call super().configure(settings)."""
        self.minimum_order_notional = settings.minimum_order_notional

    @abstractmethod
    def prepare(self, history: MarketHistory, universe: Mapping[str, str]) -> None:
        """Prepare a fresh snapshot from completed daily bars (fit if needed)."""
        raise NotImplementedError

    @abstractmethod
    def candidates(self, current_weights: Mapping[str, float], portfolio_value: float,
                   limits: PortfolioLimits) -> list[TradeCandidate]:
        """Return desired portfolio changes respecting the supplied limits."""
        raise NotImplementedError

    @abstractmethod
    def decide_to_buy(self, context: TradeCandidate) -> bool:
        raise NotImplementedError

    @abstractmethod
    def decide_to_sell(self, context: TradeCandidate) -> bool:
        raise NotImplementedError


class Trader(ABC):
    """A broker adapter executes one unambiguous model decision."""

    def __init__(self, model: Model | None = None):
        self.model = model

    @abstractmethod
    def buy(self, context: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def sell(self, context: Any) -> None:
        raise NotImplementedError

    def trade(self, context: Any) -> TradeAction:
        if self.model is None:
            raise RuntimeError("A model must be selected before trading")
        should_buy = self.model.decide_to_buy(context)
        should_sell = self.model.decide_to_sell(context)
        if should_buy and should_sell:
            raise ValueError("Model cannot decide to buy and sell at the same time")
        if should_buy:
            self.buy(context)
            return "buy"
        if should_sell:
            self.sell(context)
            return "sell"
        return "hold"
