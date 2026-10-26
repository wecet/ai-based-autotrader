"""Broker-free shadow comparison and per-member attribution for the static ensemble."""
import numpy as np

from models.ensemble import StaticEnsembleModel
from research.backtest import run_backtest


def attribute(decisions, snapshots, history, initial_cash):
    """Split close-to-close holding P/L by each member's share of the prior target.

    Holdings without a target share are ``unattributed``; the remainder of the
    equity change (fills at opens, costs, dividends) is ``execution_and_timing``.
    """
    closes = {symbol: {b.date.isoformat(): b.close for b in bars} for symbol, bars in history.stocks.items()}
    ordered = sorted({d["date"]: d for d in decisions}.values(), key=lambda d: d["date"])
    totals, unattributed, timing = {}, 0.0, 0.0
    previous_equity, previous_positions, previous_date = initial_cash, {}, None
    for snapshot in snapshots:
        day = snapshot["date"]
        prior = [d for d in ordered if d["date"] < day]
        decision = prior[-1] if prior else None
        holding_pnl = 0.0
        for symbol, quantity in previous_positions.items():
            before, after = closes[symbol].get(previous_date), closes[symbol].get(day)
            if before is None or after is None:
                continue
            pnl = quantity * (after - before)
            holding_pnl += pnl
            shares = decision["shares"].get(symbol, {}) if decision else {}
            target = sum(shares.values())
            if target <= 0:
                unattributed += pnl
                continue
            for name, share in shares.items():
                totals[name] = totals.get(name, 0.0) + pnl * share / target
        timing += snapshot["equity"] - previous_equity - holding_pnl
        previous_equity, previous_positions, previous_date = snapshot["equity"], snapshot["positions"], day
    return {"members": totals, "unattributed": unattributed, "execution_and_timing": timing,
            "total": previous_equity - initial_cash}


def shadow_divergence(decisions):
    """Mean summed absolute difference between each member's own targets and the traded aggregate."""
    differences = {}
    for decision in decisions:
        aggregate = decision["targets"]
        for name, targets in decision["shadow_targets"].items():
            symbols = set(aggregate) | set(targets)
            differences.setdefault(name, []).append(sum(abs(targets.get(s, 0.0) - aggregate.get(s, 0.0))
                                                        for s in symbols))
    abstentions = {}
    for decision in decisions:
        for name in decision["abstentions"]:
            abstentions[name] = abstentions.get(name, 0) + 1
    return {"mean_target_divergence": {name: float(np.mean(values)) for name, values in differences.items()},
            "abstaining_decisions": abstentions}


def shadow_comparison(history, settings, config, *, runner=run_backtest):
    decisions = []
    report = runner(lambda: StaticEnsembleModel(decision_log=decisions), history,
                    settings.model_copy(update={"model_id": "ensemble"}), config)
    return {"attribution": attribute(decisions, report["snapshots"], history, config.initial_cash),
            **shadow_divergence(decisions)}
