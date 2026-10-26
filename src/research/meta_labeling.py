"""Out-of-sample quality of meta-labelling decisions against realized outcomes."""
import numpy as np

from core.types import MarketHistory
from models.meta_momentum import HORIZON_SESSIONS, ROUND_TRIP_COST, MetaMomentumModel
from research.backtest import run_backtest
from trading.features import next_open_outcome


def decision_quality(decisions, history: MarketHistory, *, horizon=HORIZON_SESSIONS, cost=ROUND_TRIP_COST,
                     bins=5):
    """Score each decision with the outcome that followed it; unresolved decisions are counted, not dropped."""
    positions = {b.date.isoformat(): index for index, b in enumerate(history.spy)}
    resolved, unresolved = [], 0
    for decision in decisions:
        outcome = next_open_outcome(history.stocks[decision["symbol"]], positions[decision["date"]], horizon, cost)
        if outcome is None:
            unresolved += 1
        else:
            resolved.append((decision, outcome))
    accepted = [o for d, o in resolved if d["accepted"]]
    rejected = [o for d, o in resolved if not d["accepted"]]
    scored = [(d["probability"], o > 0) for d, o in resolved if d["probability"] is not None]

    def mean(values):
        return float(np.mean(values)) if values else None
    calibration = []
    if scored:
        probabilities = np.asarray([p for p, _ in scored])
        outcomes = np.asarray([o for _, o in scored], dtype=float)
        edges = np.linspace(0, 1, bins + 1)
        for low, high in zip(edges, edges[1:]):
            mask = (probabilities >= low) & ((probabilities < high) | (high == 1))
            if mask.any():
                calibration.append({"probability_range": [float(low), float(high)], "count": int(mask.sum()),
                                    "mean_probability": float(probabilities[mask].mean()),
                                    "observed_frequency": float(outcomes[mask].mean())})
    return {
        "horizon_sessions": horizon, "round_trip_cost": cost,
        "opportunities": len(decisions), "unresolved": unresolved,
        "acceptance_rate": mean([d["accepted"] for d, _ in resolved]),
        "unfiltered_mean_net_return": mean([o for _, o in resolved]),
        "accepted_mean_net_return": mean(accepted),
        "rejected_mean_net_return": mean(rejected),
        "missed_upside": float(sum(o for o in rejected if o > 0)),
        "avoided_losses": float(-sum(o for o in rejected if o < 0)),
        "brier_score": mean([(p - o) ** 2 for p, o in scored]),
        "calibration": calibration,
    }


def meta_decision_quality(history, settings, config, *, runner=run_backtest):
    decisions = []
    runner(lambda: MetaMomentumModel(decision_log=decisions), history,
           settings.model_copy(update={"model_id": "meta_momentum"}), config)
    by_key = {(d["date"], d["symbol"]): d for d in decisions}
    return decision_quality(list(by_key.values()), history)
