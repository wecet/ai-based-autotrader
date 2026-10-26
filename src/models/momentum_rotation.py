"""Daily long-only momentum rotation with inverse-volatility target sizing."""
import math

import numpy as np

from core.contracts import Model
from core.registry import ModelDefinition
from trading.allocation import TargetSignal, allocate_targets


class MomentumRotationModel(Model):
    """Rank 12-minus-1 month momentum, require a positive 200-session trend.

    Scores are historical returns, not forecasts. The candidate return estimate
    is deliberately None; entry decisions use trend eligibility and target size.
    """
    def __init__(self):
        self.signals = []
        self.universe = {}

    def prepare(self, history, universe):
        signals = []
        for symbol, bars in sorted(history.stocks.items()):
            if len(bars) < 253 or symbol not in universe:
                continue
            if [b.date for b in bars] != [b.date for b in history.spy]:
                continue
            closes = np.asarray([b.close for b in bars], dtype=float)
            if not np.isfinite(closes).all() or np.any(closes <= 0):
                continue
            liquidity = np.mean([b.close * b.volume for b in bars[-20:]])
            if closes[-1] <= 10 or not math.isfinite(liquidity) or liquidity <= 5_000_000:
                continue
            momentum = float(closes[-22] / closes[-253] - 1)
            returns = np.diff(closes[-61:]) / closes[-61:-1]
            volatility = float(np.std(returns, ddof=1))
            eligible = momentum > 0 and closes[-1] > np.mean(closes[-200:]) and volatility > 0
            signals.append(TargetSignal(symbol, float(closes[-1]), universe[symbol], momentum,
                                        allocation_weight=1 / volatility if eligible else 0))
        if not signals:
            raise ValueError("Momentum requires liquid, aligned histories of at least 253 sessions")
        self.signals, self.universe = signals, dict(universe)

    def target_signals(self):
        return list(self.signals)

    def candidates(self, current_weights, portfolio_value, limits):
        return allocate_targets(self.target_signals(), current_weights, portfolio_value, limits,
                                self.universe, proportional=True)

    def decide_to_buy(self, context):
        return context.desired_weight > context.current_weight and context.order_notional >= self.minimum_order_notional

    def decide_to_sell(self, context):
        return context.desired_weight < context.current_weight and context.order_notional >= self.minimum_order_notional


MODEL = ModelDefinition(
    name="Momentum rotation",
    description="Ranks positive 12-minus-1-month momentum above a 200-session moving average. "
                "Uses inverse-volatility targets inside your exposure limits; weak trends exit to cash. "
                "Historical momentum is a score, not an expected return.",
    model_type=MomentumRotationModel,
)
