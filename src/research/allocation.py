"""Compare allocation methods on identical, frozen strategy signals."""
from dataclasses import replace
import itertools

import numpy as np

from core.contracts import Model
from research.backtest import run_backtest
from trading.allocation import ALLOCATION_METHODS, allocate_targets, reallocate, trailing_returns

RISK_WINDOW_SESSIONS = 60


class ReallocatedModel(Model):
    """Delegates signals and decisions to a base model; only target sizes change."""

    def __init__(self, base, method, diagnostics=None):
        self.base = base
        self.method = method
        self.diagnostics = diagnostics if diagnostics is not None else []
        self.returns = {}
        self.universe = {}
        self.signal_date = None

    def configure(self, settings):
        super().configure(settings)
        self.base.configure(settings)

    def prepare(self, history, universe):
        self.base.prepare(history, universe)
        self.universe = dict(universe)
        self.signal_date = history.spy[-1].date.isoformat()
        self.returns = {symbol: trailing_returns([b.close for b in bars], RISK_WINDOW_SESSIONS)
                        for symbol, bars in history.stocks.items()}

    def candidates(self, current_weights, portfolio_value, limits):
        signals, proportional = reallocate(self.base.target_signals(), self.returns, self.method, limits.top_n)
        candidates = allocate_targets(signals, current_weights, portfolio_value, limits, self.universe,
                                      proportional=proportional)
        self.record(candidates)
        return candidates

    def record(self, candidates):
        targets = {c.symbol: c.desired_weight for c in candidates if c.desired_weight > 0}
        held = [s for s in sorted(targets) if self.returns.get(s) is not None]
        correlations = []
        if len(held) > 1:
            matrix = np.corrcoef(np.column_stack([self.returns[s] for s in held]), rowvar=False)
            correlations = [float(matrix[i, j]) for i, j in itertools.combinations(range(len(held)), 2)]
        groups = {}
        for candidate in candidates:
            groups[candidate.sector] = groups.get(candidate.sector, 0.0) + candidate.desired_weight
        self.diagnostics.append({
            "date": self.signal_date, "targets": targets,
            "correlations": dict(zip(itertools.combinations(held, 2), correlations)),
            "largest_group_weight": max(groups.values(), default=0.0)})

    def decide_to_buy(self, context):
        return self.base.decide_to_buy(context)

    def decide_to_sell(self, context):
        return self.base.decide_to_sell(context)


def stability(diagnostics):
    """Summarize target and correlation drift between consecutive rebalances."""
    daily = {}
    for entry in diagnostics:
        daily[entry["date"]] = entry
    entries = [daily[day] for day in sorted(daily)]
    weight_changes, correlation_changes, average_correlations = [], [], []
    for previous, current in zip(entries, entries[1:]):
        symbols = previous["targets"].keys() | current["targets"].keys()
        weight_changes.append(sum(abs(current["targets"].get(s, 0) - previous["targets"].get(s, 0))
                                  for s in symbols))
        shared = previous["correlations"].keys() & current["correlations"].keys()
        correlation_changes.extend(abs(current["correlations"][p] - previous["correlations"][p]) for p in shared)
    for entry in entries:
        if entry["correlations"]:
            average_correlations.append(np.mean(list(entry["correlations"].values())))
    def mean(values):
        return float(np.mean(values)) if values else None
    return {"rebalances": len(entries),
            "mean_target_weight_change": mean(weight_changes),
            "mean_pairwise_correlation_change": mean(correlation_changes),
            "mean_holding_correlation": mean(average_correlations),
            "mean_largest_group_weight": mean([e["largest_group_weight"] for e in entries]),
            "maximum_total_target": max((sum(e["targets"].values()) for e in entries), default=0.0)}


def compare_allocations(model_types, history, settings, config, *, runner=run_backtest):
    """Backtest every method per signal at base and double costs; failures are reported, not hidden."""
    results = {}
    for name, model_type in model_types.items():
        strategy_settings = settings.model_copy(update={"model_id": name})
        for method in ALLOCATION_METHODS:
            entry = {}
            for label, run_config in (("base_costs", config),
                                      ("double_costs", replace(config, spread_bps=config.spread_bps * 2,
                                                               slippage_bps=config.slippage_bps * 2))):
                diagnostics = []
                def factory(model_type=model_type, method=method, diagnostics=diagnostics):
                    return ReallocatedModel(model_type(), method, diagnostics)
                try:
                    report = runner(factory, history, strategy_settings, run_config)
                except ValueError as error:
                    entry[label] = {"failed": str(error)}
                    continue
                entry[label] = {"metrics": report["metrics"], "stability": stability(diagnostics)}
            results.setdefault(name, {})[method] = entry
    return results
