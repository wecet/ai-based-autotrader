"""Static-weight ensemble of independently validated strategies with shadow member targets."""
import logging

from core.contracts import Model
from core.registry import ModelDefinition
from core.types import TradeCandidate
from models.boosted_ranker import BoostedRankingModel
from models.momentum_rotation import MomentumRotationModel
from models.residual_reversion import ResidualReversionModel

# Scheduling policy: members are prepared in this order on the same snapshot, once
# per preparation. Weights are fixed in code, never fitted to evaluation results.
STATIC_WEIGHTS = {"momentum_rotation": 1 / 3, "boosted_ranker": 1 / 3, "residual_reversion": 1 / 3}
MEMBER_TYPES = {"momentum_rotation": MomentumRotationModel, "boosted_ranker": BoostedRankingModel,
                "residual_reversion": ResidualReversionModel}


def combine_targets(member_candidates, weights, current_weights, top_n):
    """Weighted sum of member targets; members without a view keep the holding's current weight.

    A convex combination of targets that each respect position, group and total
    limits respects them too. Only the ``top_n`` largest aggregate targets are
    kept; trimmed names become exits. Returns (targets, member shares per symbol).
    """
    symbols = set().union(*(c.keys() for c in member_candidates.values())) if member_candidates else set()
    targets, shares = {}, {}
    for symbol in symbols:
        contributions = {}
        for name, candidates in member_candidates.items():
            candidate = candidates.get(symbol)
            target = candidate.desired_weight if candidate is not None else current_weights.get(symbol, 0.0)
            contributions[name] = weights[name] * target
        targets[symbol] = sum(contributions.values())
        shares[symbol] = contributions
    kept = set(sorted((s for s in targets if targets[s] > 0), key=lambda s: (-targets[s], s))[:top_n])
    for symbol in targets:
        if symbol not in kept:
            targets[symbol] = 0.0
            shares[symbol] = {name: 0.0 for name in shares[symbol]}
    return targets, shares


class StaticEnsembleModel(Model):
    """Combine member target weights with fixed weights into one target set for execution.

    Members never submit orders. A member that cannot prepare abstains and its
    weight stays in cash; weights are not renormalized. ``shadow_targets`` keeps
    every member's own targets for broker-free comparison and attribution.
    """

    weights = STATIC_WEIGHTS

    def __init__(self, decision_log=None):
        self.decision_log = decision_log
        self.members = {name: MEMBER_TYPES[name]() for name in self.weights}
        self.prepared = {}
        self.abstentions = {}
        self.shadow_targets = {}
        self.universe = {}

    def configure(self, settings):
        super().configure(settings)
        for member in self.members.values():
            member.configure(settings)

    def prepare(self, history, universe):
        self.prepared, self.abstentions = {}, {}
        for name, member in self.members.items():
            try:
                member.prepare(history, universe)
            except ValueError as error:
                self.abstentions[name] = str(error)
                continue
            self.prepared[name] = member
        if not self.prepared:
            raise ValueError("Every ensemble member abstained: " + "; ".join(self.abstentions.values()))
        self.universe = dict(universe)
        self.signal_date = history.spy[-1].date.isoformat()

    def candidates(self, current_weights, portfolio_value, limits):
        member_candidates = {name: {c.symbol: c for c in member.candidates(current_weights, portfolio_value, limits)}
                             for name, member in self.prepared.items()}
        self.shadow_targets = {name: {s: c.desired_weight for s, c in candidates.items()}
                               for name, candidates in member_candidates.items()}
        weights = {name: self.weights[name] for name in self.prepared}
        targets, shares = combine_targets(member_candidates, weights, current_weights, limits.top_n)
        reference = {}
        for candidates in member_candidates.values():
            for symbol, candidate in candidates.items():
                reference.setdefault(symbol, candidate)
        result = [TradeCandidate(symbol=s, predicted_return=None, percentile_rank=reference[s].percentile_rank,
                                 price=reference[s].price, sector=reference[s].sector, desired_weight=targets[s],
                                 current_weight=current_weights.get(s, 0.0), portfolio_value=portfolio_value,
                                 estimated_spread=reference[s].estimated_spread,
                                 estimated_slippage=reference[s].estimated_slippage)
                  for s in sorted(targets) if targets[s] > 0 or current_weights.get(s, 0.0) > 0]
        logging.info("Ensemble shadow targets %s; abstained %s", self.shadow_targets, sorted(self.abstentions))
        if self.decision_log is not None:
            self.decision_log.append({"date": self.signal_date, "targets": targets, "shares": shares,
                                      "shadow_targets": self.shadow_targets, "abstentions": dict(self.abstentions)})
        return result

    def decide_to_buy(self, context):
        return context.desired_weight > context.current_weight and context.order_notional >= self.minimum_order_notional

    def decide_to_sell(self, context):
        return context.desired_weight < context.current_weight and context.order_notional >= self.minimum_order_notional


MODEL = ModelDefinition(
    name="Static strategy ensemble",
    description="Averages the target weights of momentum rotation, boosted ranking and residual reversion "
                "with fixed equal weights, then keeps the largest positions up to your position count. "
                "Only the combined targets are traded; a member without data leaves its share in cash.",
    model_type=StaticEnsembleModel,
)
