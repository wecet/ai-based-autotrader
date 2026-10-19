"""Shared daily rank-model lifecycle and legacy allocation compatibility."""
from typing import Mapping, Sequence

import numpy as np

from core.contracts import Model
from core.types import FeatureRow, TradeCandidate, MarketHistory, PortfolioLimits
from trading.estimators import Estimator, RidgeRanker
from trading.features import build_ranked_training_set, build_feature_rows


class RankingModel(Model):
    """Rank stocks and approve trades only when their edge exceeds costs."""

    def __init__(
        self,
        estimator: Estimator | None = None,
        *,
        safety_margin: float = 0.002,
        minimum_order_notional: float = 10.0,
    ) -> None:
        self.estimator = estimator or RidgeRanker()
        self.safety_margin = safety_margin
        self.minimum_order_notional = minimum_order_notional
        self.return_calibration: tuple[float, float] | None = None
        self.ranked: list[tuple[FeatureRow, float, float]] = []

    def prepare(self, history: MarketHistory, universe: Mapping[str, str]) -> None:
        training = build_ranked_training_set(history.stocks, history.spy, history.sectors, universe)
        rows = build_feature_rows(history.stocks, history.spy, history.sectors, universe)
        if len(rows) < 2 or not training:
            raise ValueError("Insufficient liquid stocks or training rows")
        self.fit(training)
        self.ranked = self.rank(rows)

    def candidates(self, current_weights: Mapping[str, float], portfolio_value: float,
                   limits: PortfolioLimits) -> list[TradeCandidate]:
        eligible = {row.symbol for row, _, _ in self.ranked}
        retained = sum(weight for symbol, weight in current_weights.items() if symbol not in eligible)
        return self.construct_portfolio(
            self.ranked, current_weights=current_weights, portfolio_value=portfolio_value,
            maximum_total_exposure=max(0, limits.maximum_total_exposure - retained),
            maximum_sector_exposure=limits.maximum_sector_exposure,
            maximum_position=limits.maximum_position, top_n=limits.top_n)

    def fit(self, rows: Sequence[FeatureRow]) -> "RankingModel":
        usable = [
            row
            for row in rows
            if row.target_rank is not None and row.future_return_5d is not None
        ]
        if not usable:
            raise ValueError("no labeled rows were supplied for training")
        features = np.asarray([row.values for row in usable], dtype=float)
        targets = np.asarray([row.target_rank for row in usable], dtype=float)
        self.estimator.fit(features, targets)
        rank_predictions = np.asarray(self.estimator.predict(features), dtype=float)
        future_returns = np.asarray([row.future_return_5d for row in usable], dtype=float)
        calibration_design = np.column_stack((np.ones(len(rank_predictions)), rank_predictions))
        intercept, slope = np.linalg.lstsq(
            calibration_design, future_returns, rcond=None
        )[0]
        self.return_calibration = float(intercept), float(slope)
        return self

    def rank(self, rows: Sequence[FeatureRow]) -> list[tuple[FeatureRow, float, float]]:
        if not rows:
            return []
        if self.return_calibration is None:
            raise RuntimeError("the cross-sectional model must be fitted before ranking")
        rank_predictions = np.asarray(
            self.estimator.predict(np.asarray([row.values for row in rows], dtype=float)),
            dtype=float,
        )
        intercept, slope = self.return_calibration
        expected_returns = intercept + slope * rank_predictions
        order = np.argsort(rank_predictions)
        percentile_ranks = np.empty(len(rows), dtype=float)
        percentile_ranks[order] = np.linspace(0.0, 1.0, len(rows)) if len(rows) > 1 else 1.0
        ranked = list(zip(rows, expected_returns.tolist(), percentile_ranks.tolist()))
        return sorted(ranked, key=lambda item: item[2], reverse=True)

    def decide_to_buy(self, context: TradeCandidate) -> bool:
        required_edge = context.estimated_spread + context.estimated_slippage + self.safety_margin
        return (
            context.desired_weight > context.current_weight
            and context.predicted_return is not None
            and context.predicted_return > required_edge
            and context.order_notional >= self.minimum_order_notional
        )

    def decide_to_sell(self, context: TradeCandidate) -> bool:
        return (
            context.desired_weight < context.current_weight
            and context.order_notional >= self.minimum_order_notional
        )

    def construct_portfolio(
        self,
        ranked: Sequence[tuple[FeatureRow, float, float]],
        *,
        current_weights: Mapping[str, float],
        portfolio_value: float,
        top_n: int = 5,
        maximum_total_exposure: float = 0.50,
        maximum_sector_exposure: float = 0.20,
        maximum_position: float = 0.10,
        estimated_spread: float = 0.0005,
        estimated_slippage: float = 0.0005,
    ) -> list[TradeCandidate]:
        """Convert rankings to constrained, equal-risk target weights."""

        targets: dict[str, tuple[FeatureRow, float, float, float]] = {}
        sector_exposure: dict[str, float] = {}
        total_exposure = 0.0
        for row, prediction, percentile in ranked:
            if len(targets) >= top_n or total_exposure >= maximum_total_exposure:
                break
            required_edge = estimated_spread + estimated_slippage + self.safety_margin
            if prediction <= required_edge:
                continue
            available_sector = maximum_sector_exposure - sector_exposure.get(row.sector, 0.0)
            weight = min(maximum_position, available_sector, maximum_total_exposure - total_exposure)
            if weight <= 0:
                continue
            targets[row.symbol] = (row, prediction, percentile, weight)
            sector_exposure[row.sector] = sector_exposure.get(row.sector, 0.0) + weight
            total_exposure += weight

        candidates: list[TradeCandidate] = []
        latest_rows = {row.symbol: row for row, _, _ in ranked}
        for symbol in set(current_weights) | set(targets):
            if symbol in targets:
                row, prediction, percentile, desired_weight = targets[symbol]
            else:
                row = latest_rows.get(symbol)
                if row is None:
                    # A stale/missing holding is intentionally not liquidated automatically.
                    continue
                prediction, percentile, desired_weight = 0.0, 0.0, 0.0
            candidates.append(
                TradeCandidate(
                    symbol=symbol,
                    predicted_return=prediction,
                    percentile_rank=percentile,
                    price=row.close,
                    sector=row.sector,
                    desired_weight=desired_weight,
                    current_weight=current_weights.get(symbol, 0.0),
                    portfolio_value=portfolio_value,
                    estimated_spread=estimated_spread,
                    estimated_slippage=estimated_slippage,
                )
            )
        return candidates

