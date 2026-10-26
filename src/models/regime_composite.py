"""Select momentum or residual reversion by causal market regime and scale exposure in stress."""
from dataclasses import replace

import numpy as np

from core.contracts import Model
from core.registry import ModelDefinition
from models.momentum_rotation import MomentumRotationModel
from models.residual_reversion import ResidualReversionModel
from trading.allocation import allocate_targets
from trading.features import regime_features
from trading.regimes import FilteredRegimeModel, dominant_regimes


class RegimeCompositeModel(Model):
    """Trend regimes use momentum, range regimes use reversion; stress probability cuts exposure.

    Regime probabilities are forward-filtered from trailing features with a model
    fitted on the supplied history only. Exposure becomes configured total limit
    times (1 - P(stress)); configured limits are never raised.
    """

    switch_margin = 0.0

    def __init__(self, decision_log=None):
        self.decision_log = decision_log
        self.components = {"trend": MomentumRotationModel(), "range": ResidualReversionModel()}
        self.active = None
        self.stress_probability = 1.0
        self.universe = {}

    def configure(self, settings):
        super().configure(settings)
        for component in self.components.values():
            component.configure(settings)

    def prepare(self, history, universe):
        features = regime_features(history.spy, {s: b for s, b in history.stocks.items() if s in universe})
        usable = np.isfinite(features).all(axis=1)
        if not usable[-1]:
            raise ValueError("Regime features need at least 201 sessions and two aligned stocks")
        start = int(np.flatnonzero(~usable)[-1]) + 1 if not usable.all() else 0
        regime = FilteredRegimeModel().fit(features[start:])
        probabilities = regime.filter(features[start:])
        path = dominant_regimes(probabilities, self.switch_margin)
        self.stress_probability = float(probabilities["stress"][-1])
        preference = [path[-1]] if path[-1] != "stress" else []
        preference += sorted(("trend", "range"), key=lambda name: -probabilities[name][-1])
        self.universe = dict(universe)
        self.active, failures = None, []
        for name in dict.fromkeys(preference):
            try:
                self.components[name].prepare(history, universe)
            except ValueError as error:
                failures.append(f"{name}: {error}")
                continue
            self.active = name
            break
        if self.active is None:
            raise ValueError("No regime component could be prepared (" + "; ".join(failures) + ")")
        decision = {"date": history.spy[-1].date.isoformat(), "regime": path[-1], "component": self.active,
                    **{f"p_{name}": float(values[-1]) for name, values in probabilities.items()}}
        self.decision = decision
        if self.decision_log is not None:
            self.decision_log.append(decision)

    def target_signals(self):
        return self.components[self.active].target_signals()

    def candidates(self, current_weights, portfolio_value, limits):
        scaled = replace(limits, maximum_total_exposure=limits.maximum_total_exposure * (1 - self.stress_probability))
        return allocate_targets(self.target_signals(), current_weights, portfolio_value, scaled, self.universe,
                                proportional=True)

    def decide_to_buy(self, context):
        return context.desired_weight > context.current_weight and context.order_notional >= self.minimum_order_notional

    def decide_to_sell(self, context):
        return context.desired_weight < context.current_weight and context.order_notional >= self.minimum_order_notional


MODEL = ModelDefinition(
    name="Regime-adaptive composite",
    description="Estimates trend, range and stress market regimes from trailing SPY trend, volatility, breadth "
                "and correlation, using only past data. Trend regimes run momentum rotation, range regimes run "
                "residual reversion, and total exposure shrinks with the stress probability inside your limits.",
    model_type=RegimeCompositeModel,
)
