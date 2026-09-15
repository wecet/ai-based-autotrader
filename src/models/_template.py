"""Copy to a new lowercase filename, implement it, and export its MODEL definition.

Files beginning with an underscore are excluded from discovery. This scaffold
is deliberately abstract so an unfinished strategy cannot be selected.
"""
from abc import abstractmethod
from typing import Mapping

from core.contracts import Model
from core.registry import ModelDefinition
from core.types import MarketHistory, PortfolioLimits, TradeCandidate


class NewStrategy(Model):
    @abstractmethod
    def prepare(self, history: MarketHistory, universe: Mapping[str, str]) -> None:
        """Compute signals only from the supplied completed historical bars."""
        raise NotImplementedError

    @abstractmethod
    def candidates(self, current_weights: Mapping[str, float], portfolio_value: float,
                   limits: PortfolioLimits) -> list[TradeCandidate]:
        """Return target changes respecting limits and retained holdings."""
        raise NotImplementedError

    @abstractmethod
    def decide_to_buy(self, context: TradeCandidate) -> bool:
        raise NotImplementedError

    @abstractmethod
    def decide_to_sell(self, context: TradeCandidate) -> bool:
        raise NotImplementedError


# After implementing all four methods, remove their @abstractmethod decorators
# and uncomment/customize this definition. A no-argument constructor is required.
# MODEL = ModelDefinition(
#     name="Your strategy's display name",
#     description="Explain the actual signal, holding horizon, and selection rules.",
#     model_type=NewStrategy,
# )
