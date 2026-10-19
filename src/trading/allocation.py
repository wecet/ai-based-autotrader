"""Broker-free target allocation shared by daily strategies."""
from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from core.types import PortfolioLimits, TradeCandidate


@dataclass(frozen=True)
class TargetSignal:
    symbol: str
    price: float
    group: str
    score: float
    expected_return: float | None = None
    allocation_weight: float = 1.0


def allocate_targets(signals: Sequence[TargetSignal], current_weights: Mapping[str, float],
                     portfolio_value: float, limits: PortfolioLimits, universe: Mapping[str, str],
                     *, proportional=False, estimated_spread=.0005, estimated_slippage=.0005):
    """Reserve retained holdings first; unavailable symbols are never liquidated.

    Zero allocation weights request exits for symbols with valid current data.
    Proportional weights allow volatility allocation; unused cap room stays cash.
    """
    if not math.isfinite(portfolio_value) or portfolio_value <= 0:
        raise ValueError("Portfolio value must be finite and positive")
    if any(not math.isfinite(w) or w < 0 for w in current_weights.values()):
        raise ValueError("Current weights must be finite and long-only")
    available = {signal.symbol: signal for signal in signals}
    if len(available) != len(signals):
        raise ValueError("Duplicate target symbol")
    for signal in signals:
        values = (signal.price, signal.score, signal.allocation_weight)
        if not all(math.isfinite(v) for v in values) or signal.price <= 0 or signal.allocation_weight < 0:
            raise ValueError("Invalid target signal")
        if signal.expected_return is not None and not math.isfinite(signal.expected_return):
            raise ValueError("Invalid return estimate")
    retained = {symbol: weight for symbol, weight in current_weights.items() if symbol not in available}
    room = max(0, limits.maximum_total_exposure - sum(retained.values()))
    groups = {}
    for symbol, weight in retained.items():
        group = universe.get(symbol)
        if group:
            groups[group] = groups.get(group, 0.0) + weight
    ranks = {}
    for index, signal in enumerate(sorted(signals, key=lambda s: s.score)):
        ranks.setdefault(signal.score, []).append(index)
    percentiles = {score: (sum(indices) / len(indices)) / (len(signals) - 1) if len(signals) > 1 else 1.0
                   for score, indices in ranks.items()}
    selected = sorted((s for s in signals if s.allocation_weight > 0), key=lambda s: (-s.score, s.symbol))
    selected = selected[:limits.top_n]
    denominator = sum(s.allocation_weight for s in selected)
    budget = room
    targets = {}
    for signal in selected:
        group_room = max(0, limits.maximum_sector_exposure - groups.get(signal.group, 0.0))
        requested = budget * signal.allocation_weight / denominator if proportional else limits.maximum_position
        weight = min(requested, limits.maximum_position, group_room, room)
        targets[signal.symbol] = weight
        groups[signal.group] = groups.get(signal.group, 0.0) + weight
        room -= weight
    return [TradeCandidate(
        symbol=s.symbol, predicted_return=s.expected_return, percentile_rank=percentiles[s.score],
        price=s.price, sector=s.group, desired_weight=targets.get(s.symbol, 0.0),
        current_weight=current_weights.get(s.symbol, 0.0), portfolio_value=portfolio_value,
        estimated_spread=estimated_spread, estimated_slippage=estimated_slippage,
    ) for s in sorted(signals, key=lambda s: s.symbol)
      if s.symbol in targets or current_weights.get(s.symbol, 0.0) > 0]
