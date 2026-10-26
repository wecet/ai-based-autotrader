"""Broker-free target allocation shared by daily strategies."""
from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import numpy as np

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


def veto_new_entries(signals: Sequence[TargetSignal], rejected, current_weights: Mapping[str, float]):
    """Zero vetoed signals unless already held; holdings keep their own exit rules."""
    return [replace(s, allocation_weight=0.0) if s.symbol in rejected and current_weights.get(s.symbol, 0) <= 0
            else s for s in signals]


ALLOCATION_METHODS = ("capped", "equal", "inverse_volatility", "hierarchical_risk")


def trailing_returns(closes: Sequence[float], sessions: int = 60):
    """Simple daily returns over the latest window; None when unusable."""
    values = np.asarray(closes[-sessions - 1:], dtype=float)
    if len(values) < sessions + 1 or not np.isfinite(values).all() or np.any(values <= 0):
        return None
    returns = np.diff(values) / values[:-1]
    return returns if np.std(returns, ddof=1) > 0 else None


def inverse_volatility_weights(returns: np.ndarray) -> np.ndarray:
    """Weights proportional to 1/sigma for a (sessions, assets) return matrix."""
    volatility = np.std(returns, axis=0, ddof=1)
    if not np.all(np.isfinite(volatility)) or np.any(volatility <= 0):
        raise ValueError("Volatility must be finite and positive")
    weights = 1 / volatility
    return weights / weights.sum()


def _cluster_variance(covariance, members):
    sub = covariance[np.ix_(members, members)]
    inverse = 1 / np.diag(sub)
    inverse /= inverse.sum()
    return float(inverse @ sub @ inverse)


def _single_linkage_order(distance):
    """Dendrogram leaf order from deterministic single-linkage agglomeration."""
    clusters = [[index] for index in range(len(distance))]
    while len(clusters) > 1:
        pairs = ((min(distance[a, b] for a in clusters[i] for b in clusters[j]), i, j)
                 for i in range(len(clusters)) for j in range(i + 1, len(clusters)))
        _, left, right = min(pairs)
        merged = clusters[left] + clusters[right]
        clusters = [c for k, c in enumerate(clusters) if k not in (left, right)] + [merged]
    return clusters[0]


def hierarchical_risk_weights(returns: np.ndarray) -> np.ndarray:
    """Hierarchical risk parity: cluster on correlation distance, then bisect by variance.

    Uses correlation distance sqrt((1 - rho) / 2) directly for single linkage.
    Weights are long-only, sum to one and require no covariance inversion.
    """
    returns = np.asarray(returns, dtype=float)
    count = returns.shape[1]
    if count == 1:
        return np.ones(1)
    covariance = np.cov(returns, rowvar=False, ddof=1)
    volatility = np.sqrt(np.diag(covariance))
    if not np.all(np.isfinite(covariance)) or np.any(volatility <= 0):
        raise ValueError("Covariance must be finite with positive variances")
    correlation = np.clip(covariance / np.outer(volatility, volatility), -1, 1)
    order = _single_linkage_order(np.sqrt((1 - correlation) / 2))
    weights = np.ones(count)
    clusters = [order]
    while clusters:
        clusters = [cluster[start:stop] for cluster in clusters
                    for start, stop in ((0, len(cluster) // 2), (len(cluster) // 2, len(cluster)))
                    if len(cluster) > 1]
        for index in range(0, len(clusters), 2):
            left, right = clusters[index], clusters[index + 1]
            left_variance = _cluster_variance(covariance, left)
            right_variance = _cluster_variance(covariance, right)
            alpha = 1 - left_variance / (left_variance + right_variance)
            weights[left] *= alpha
            weights[right] *= 1 - alpha
    return weights / weights.sum()


def reallocate(signals: Sequence[TargetSignal], returns: Mapping[str, np.ndarray], method: str,
               top_n: int) -> tuple[list[TargetSignal], bool]:
    """Resize the same selected signals; return (signals, proportional) for allocate_targets.

    Selection order and eligibility are unchanged. Selected symbols without a
    usable return window receive zero weight rather than an invented estimate.
    """
    if method not in ALLOCATION_METHODS:
        raise ValueError("Unknown allocation method")
    eligible = sorted((s for s in signals if s.allocation_weight > 0), key=lambda s: (-s.score, s.symbol))
    if method == "capped":
        return [replace(s, allocation_weight=1.0 if s.allocation_weight > 0 else 0.0) for s in signals], False
    selected = [s.symbol for s in eligible[:top_n] if returns.get(s.symbol) is not None]
    weights = {}
    if selected:
        matrix = np.column_stack([returns[symbol] for symbol in selected])
        if method == "equal":
            values = np.full(len(selected), 1 / len(selected))
        elif method == "inverse_volatility":
            values = inverse_volatility_weights(matrix)
        else:
            values = hierarchical_risk_weights(matrix)
        weights = dict(zip(selected, values.tolist()))
    return [replace(s, allocation_weight=weights.get(s.symbol, 0.0)) for s in signals], True
