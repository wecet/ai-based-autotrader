"""Cross-sectional stock ranking experiment.

The module deliberately separates research from execution:

* :func:`build_feature_rows` turns completed daily bars into point-in-time features.
* :func:`build_ranked_training_set` creates five-day, within-date rank targets.
* :class:`RidgeRanker` is a small NumPy baseline that can later be replaced by any
  LightGBM/XGBoost-style estimator exposing ``fit`` and ``predict``.
* :class:`CrossSectionalModel` ranks the latest universe and emits trade decisions.
* :class:`CrossSectionalTrader` sends those decisions to an Alpaca paper account.

No order is submitted by importing this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
from typing import Mapping, Protocol, Sequence

import numpy as np
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import modules.templates as tmp
from utils import Account


FEATURE_NAMES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "distance_from_ma20",
    "distance_from_ma50",
    "distance_from_ma200",
    "rsi_14",
    "volatility_20d",
    "volume_ratio_20d",
    "overnight_gap",
    "market_beta_60d",
    "sector_relative_momentum_20d",
    "distance_from_high_60d",
    "spy_trend_20d",
    "spy_volatility_20d",
)


@dataclass(frozen=True)
class DailyBar:
    """A completed, split-adjusted daily OHLCV bar."""

    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeatureRow:
    date: datetime.date
    symbol: str
    sector: str
    close: float
    average_dollar_volume: float
    values: tuple[float, ...]
    future_return_5d: float | None = None
    target_rank: float | None = None


@dataclass(frozen=True)
class TradeCandidate:
    """One symbol's desired state after ranking and risk constraints."""

    symbol: str
    predicted_return: float
    percentile_rank: float
    price: float
    sector: str
    desired_weight: float
    current_weight: float = 0.0
    portfolio_value: float = 0.0
    estimated_spread: float = 0.0
    estimated_slippage: float = 0.0

    @property
    def order_notional(self) -> float:
        return abs(self.desired_weight - self.current_weight) * self.portfolio_value


class Estimator(Protocol):
    def fit(self, features: np.ndarray, targets: np.ndarray) -> object: ...

    def predict(self, features: np.ndarray) -> np.ndarray: ...


class RidgeRanker:
    """Simple, deterministic baseline rank regressor implemented with NumPy."""

    def __init__(self, regularization: float = 1.0) -> None:
        if regularization < 0:
            raise ValueError("regularization must be non-negative")
        self.regularization = regularization
        self.feature_mean: np.ndarray | None = None
        self.feature_scale: np.ndarray | None = None
        self.coefficients: np.ndarray | None = None

    def fit(self, features: np.ndarray, targets: np.ndarray) -> "RidgeRanker":
        x = np.asarray(features, dtype=float)
        y = np.asarray(targets, dtype=float)
        if x.ndim != 2 or y.ndim != 1 or len(x) != len(y):
            raise ValueError("features must be 2-D and targets must be a matching 1-D array")
        if len(x) == 0:
            raise ValueError("cannot fit an empty training set")

        self.feature_mean = x.mean(axis=0)
        self.feature_scale = x.std(axis=0)
        self.feature_scale[self.feature_scale == 0] = 1.0
        standardized = (x - self.feature_mean) / self.feature_scale
        design = np.column_stack((np.ones(len(standardized)), standardized))
        penalty = np.eye(design.shape[1]) * self.regularization
        penalty[0, 0] = 0.0
        self.coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        if self.coefficients is None or self.feature_mean is None or self.feature_scale is None:
            raise RuntimeError("the ranker must be fitted before prediction")
        x = np.asarray(features, dtype=float)
        standardized = (x - self.feature_mean) / self.feature_scale
        design = np.column_stack((np.ones(len(standardized)), standardized))
        return design @ self.coefficients


def _return(closes: np.ndarray, periods: int) -> float:
    return float(closes[-1] / closes[-periods - 1] - 1.0)


def _rsi(closes: np.ndarray, periods: int = 14) -> float:
    changes = np.diff(closes[-periods - 1:])
    gains = np.maximum(changes, 0.0).mean()
    losses = np.maximum(-changes, 0.0).mean()
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return float(100.0 - 100.0 / (1.0 + gains / losses))


def _beta(asset_returns: np.ndarray, market_returns: np.ndarray) -> float:
    market_variance = float(np.var(market_returns, ddof=1))
    if market_variance == 0:
        return 0.0
    covariance = float(np.cov(asset_returns, market_returns, ddof=1)[0, 1])
    return covariance / market_variance


def calculate_features(
    bars: Sequence[DailyBar],
    spy_bars: Sequence[DailyBar],
    sector_bars: Sequence[DailyBar],
) -> tuple[tuple[float, ...], float]:
    """Calculate all features from data available on the latest bar's date."""

    if len(bars) < 201 or len(spy_bars) < 61 or len(sector_bars) < 21:
        raise ValueError("at least 201 stock, 61 SPY, and 21 sector bars are required")

    closes = np.asarray([bar.close for bar in bars], dtype=float)
    spy_closes = np.asarray([bar.close for bar in spy_bars], dtype=float)
    sector_closes = np.asarray([bar.close for bar in sector_bars], dtype=float)
    volumes = np.asarray([bar.volume for bar in bars], dtype=float)
    stock_returns = np.diff(closes[-61:]) / closes[-61:-1]
    spy_returns = np.diff(spy_closes[-61:]) / spy_closes[-61:-1]
    average_volume = float(volumes[-20:].mean())
    average_dollar_volume = float(np.mean(closes[-20:] * volumes[-20:]))

    values = (
        _return(closes, 1),
        _return(closes, 5),
        _return(closes, 20),
        _return(closes, 60),
        float(closes[-1] / closes[-20:].mean() - 1.0),
        float(closes[-1] / closes[-50:].mean() - 1.0),
        float(closes[-1] / closes[-200:].mean() - 1.0),
        _rsi(closes),
        float(np.std(stock_returns[-20:], ddof=1) * np.sqrt(252.0)),
        float(volumes[-1] / average_volume) if average_volume else 0.0,
        float(bars[-1].open / bars[-2].close - 1.0),
        _beta(stock_returns, spy_returns),
        _return(closes, 20) - _return(sector_closes, 20),
        float(closes[-1] / max(bar.high for bar in bars[-60:]) - 1.0),
        _return(spy_closes, 20),
        float(np.std(spy_returns[-20:], ddof=1) * np.sqrt(252.0)),
    )
    return values, average_dollar_volume


def build_feature_rows(
    stock_bars: Mapping[str, Sequence[DailyBar]],
    spy_bars: Sequence[DailyBar],
    sector_bars: Mapping[str, Sequence[DailyBar]],
    sectors: Mapping[str, str],
    *,
    minimum_price: float = 10.0,
    minimum_average_dollar_volume: float = 5_000_000.0,
    as_of_index: int | None = None,
) -> list[FeatureRow]:
    """Build one point-in-time cross-section, excluding illiquid/stale symbols."""

    rows: list[FeatureRow] = []
    selected_spy = spy_bars if as_of_index is None else spy_bars[: as_of_index + 1]
    if not selected_spy:
        return rows
    as_of_date = selected_spy[-1].date

    for symbol, all_bars in stock_bars.items():
        bars = all_bars if as_of_index is None else all_bars[: as_of_index + 1]
        sector = sectors.get(symbol)
        all_sector_bars = sector_bars.get(sector or "")
        if not bars or not sector or not all_sector_bars or bars[-1].date != as_of_date:
            continue
        selected_sector = all_sector_bars if as_of_index is None else all_sector_bars[: as_of_index + 1]
        if not selected_sector or selected_sector[-1].date != as_of_date:
            continue
        try:
            values, dollar_volume = calculate_features(bars, selected_spy, selected_sector)
        except (ValueError, ZeroDivisionError, FloatingPointError):
            continue
        if bars[-1].close <= minimum_price or dollar_volume <= minimum_average_dollar_volume:
            continue
        if not np.all(np.isfinite(values)):
            continue
        rows.append(
            FeatureRow(as_of_date, symbol, sector, bars[-1].close, dollar_volume, values)
        )
    return rows


def build_ranked_training_set(
    stock_bars: Mapping[str, Sequence[DailyBar]],
    spy_bars: Sequence[DailyBar],
    sector_bars: Mapping[str, Sequence[DailyBar]],
    sectors: Mapping[str, str],
    *,
    horizon: int = 5,
) -> list[FeatureRow]:
    """Create point-in-time rows with percentile-ranked future returns."""

    rows: list[FeatureRow] = []
    for index in range(200, len(spy_bars) - horizon):
        daily_rows = build_feature_rows(
            stock_bars, spy_bars, sector_bars, sectors, as_of_index=index
        )
        labeled: list[FeatureRow] = []
        for row in daily_rows:
            bars = stock_bars[row.symbol]
            if index + horizon >= len(bars) or bars[index].date != row.date:
                continue
            future_return = bars[index + horizon].close / bars[index].close - 1.0
            labeled.append(
                FeatureRow(**{**row.__dict__, "future_return_5d": future_return})
            )
        ordered = sorted(labeled, key=lambda item: item.future_return_5d or 0.0)
        denominator = max(len(ordered) - 1, 1)
        for rank, row in enumerate(ordered):
            rows.append(FeatureRow(**{**row.__dict__, "target_rank": rank / denominator}))
    return rows


def chronological_split(
    rows: Sequence[FeatureRow],
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> tuple[list[FeatureRow], list[FeatureRow], list[FeatureRow]]:
    """Split on date boundaries, keeping the final test period untouched."""

    if train_fraction <= 0 or validation_fraction <= 0 or train_fraction + validation_fraction >= 1:
        raise ValueError("fractions must be positive and sum to less than one")
    dates = sorted({row.date for row in rows})
    train_end = max(1, int(len(dates) * train_fraction))
    validation_end = max(train_end + 1, int(len(dates) * (train_fraction + validation_fraction)))
    train_dates = set(dates[:train_end])
    validation_dates = set(dates[train_end:validation_end])
    return (
        [row for row in rows if row.date in train_dates],
        [row for row in rows if row.date in validation_dates],
        [row for row in rows if row.date not in train_dates | validation_dates],
    )


class CrossSectionalModel(tmp.Model):
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

    def fit(self, rows: Sequence[FeatureRow]) -> "CrossSectionalModel":
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


class CrossSectionalTrader(tmp.Trader):
    """Execute model-approved notional orders through an Alpaca paper account."""

    def __init__(self, model: CrossSectionalModel, account: Account) -> None:
        super().__init__(model)
        if account.trading_client is None:
            raise RuntimeError("Account.login() must be called before creating the trader")
        self.account = account

    def _submit(self, context: TradeCandidate, side: OrderSide) -> None:
        client = self.account.trading_client
        if client is None:
            raise RuntimeError("the Alpaca trading client is not connected")
        request = MarketOrderRequest(
            symbol=context.symbol,
            notional=round(context.order_notional, 2),
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        client.submit_order(order_data=request)

    def buy(self, context: TradeCandidate) -> None:
        self._submit(context, OrderSide.BUY)

    def sell(self, context: TradeCandidate) -> None:
        self._submit(context, OrderSide.SELL)

    def rebalance(self, candidates: Sequence[TradeCandidate]) -> dict[str, tmp.TradeAction]:
        """Sell reductions first, then submit approved buys."""

        client = self.account.trading_client
        if client is None:
            raise RuntimeError("the Alpaca trading client is not connected")
        if not client.get_clock().is_open:
            raise RuntimeError("the market is closed; no rebalance orders were submitted")
        account_details = client.get_account()
        if account_details.trading_blocked:
            raise RuntimeError("Alpaca has blocked trading on this account")

        actions: dict[str, tmp.TradeAction] = {}
        sells = [candidate for candidate in candidates if candidate.desired_weight < candidate.current_weight]
        buys = [candidate for candidate in candidates if candidate.desired_weight >= candidate.current_weight]
        for candidate in (*sells, *buys):
            actions[candidate.symbol] = self.trade(candidate)
        return actions
