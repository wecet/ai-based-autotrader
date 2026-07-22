from abc import ABC, abstractmethod
from typing import Any, Literal


TradeAction = Literal["buy", "sell", "hold"]


class Model(ABC):
    """Base class for models that produce trading decisions."""

    @abstractmethod
    def decide_to_buy(self, context: Any) -> bool:
        """Return whether the trader should buy for the given context."""
        raise NotImplementedError

    @abstractmethod
    def decide_to_sell(self, context: Any) -> bool:
        """Return whether the trader should sell for the given context."""
        raise NotImplementedError


class Trader(ABC):
    """Base trader that turns a model's decisions into trade actions."""

    def __init__(self, model: Model):
        self.model = model

    @abstractmethod
    def buy(self, context: Any) -> None:
        """Execute a buy using the supplied context."""
        raise NotImplementedError

    @abstractmethod
    def sell(self, context: Any) -> None:
        """Execute a sell using the supplied context."""
        raise NotImplementedError

    def trade(self, context: Any) -> TradeAction:
        """Ask the model for a decision and execute the matching action."""
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
