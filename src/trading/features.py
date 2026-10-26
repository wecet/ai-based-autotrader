"""Point-in-time daily features and labels shared by ranking strategies."""
from typing import Mapping, Sequence

import numpy as np

from core.types import DailyBar, FeatureRow


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


def residual_zscores(closes: Sequence[float], market_closes: Sequence[float], group_closes: Sequence[float],
                     *, lookback: int = 60, shock: int = 5) -> np.ndarray:
    """Standardized cumulative residual return over the latest ``shock`` sessions, per date.

    For each date t, regress daily returns on market and group returns over the
    ``lookback`` sessions before the shock window, then sum the shock window's
    residuals (excluding the intercept) and divide by sigma * sqrt(shock). Only
    closes up to t are used. Dates without enough history are NaN.
    """
    closes, market, group = (np.asarray(values, dtype=float) for values in (closes, market_closes, group_closes))
    if not len(closes) == len(market) == len(group):
        raise ValueError("Residual inputs must be aligned")
    returns = np.diff(closes) / closes[:-1]
    factors = np.column_stack((np.ones(len(returns)), np.diff(market) / market[:-1], np.diff(group) / group[:-1]))
    scores = np.full(len(closes), np.nan)
    for t in range(lookback + shock, len(closes)):
        fit = slice(t - shock - lookback, t - shock)
        coefficients = np.linalg.lstsq(factors[fit], returns[fit], rcond=None)[0]
        residuals = returns[fit] - factors[fit] @ coefficients
        sigma = float(np.std(residuals, ddof=factors.shape[1]))
        if sigma > 0:
            window = slice(t - shock, t)
            residual = returns[window] - factors[window, 1:] @ coefficients[1:]
            scores[t] = residual.sum() / (sigma * np.sqrt(shock))
    return scores


def reversion_episode_age(scores: Sequence[float], *, entry: float, exit: float,
                          maximum_sessions: int) -> int | None:
    """Sessions since an open reversion episode began, or None when flat.

    An episode opens when the score falls to ``entry`` and closes once it recovers
    to ``exit``, exceeds the holding limit, or becomes unavailable. A new episode
    requires the score to rise above ``entry`` first, so a persistent decline
    cannot re-qualify immediately after a forced exit.
    """
    age, armed = None, True
    for score in scores:
        if age is not None:
            age += 1
            if not np.isfinite(score) or score >= exit or age > maximum_sessions:
                age, armed = None, False
        if not np.isfinite(score):
            continue
        if score > entry:
            armed = True
        elif age is None and armed:
            age, armed = 0, False
    return age


MOMENTUM_FEATURE_NAMES = (
    "momentum_12_1",
    "volatility_60d",
    "distance_from_ma200",
    "return_20d",
    "return_5d",
    "volume_ratio_20d",
    "group_relative_return_20d",
    "spy_trend_20d",
    "spy_volatility_20d",
)


def _rolling(values, window):
    from numpy.lib.stride_tricks import sliding_window_view
    return sliding_window_view(values, window)


def momentum_panel(bars: Sequence[DailyBar], spy_bars: Sequence[DailyBar], group_bars: Sequence[DailyBar],
                   *, minimum_price: float = 10.0, minimum_average_dollar_volume: float = 5_000_000.0):
    """Momentum-rule eligibility, inverse-volatility weights and features for every session.

    Row t uses closes up to t only and reproduces the momentum rotation rule:
    positive 12-minus-1-month return, close above its 200-session average,
    positive 60-session volatility and the price/liquidity filters. Rows before
    253 sessions are ineligible with NaN features.
    """
    closes = np.asarray([b.close for b in bars], dtype=float)
    volumes = np.asarray([b.volume for b in bars], dtype=float)
    spy = np.asarray([b.close for b in spy_bars], dtype=float)
    group = np.asarray([b.close for b in group_bars], dtype=float)
    count = len(closes)
    if not count == len(spy) == len(group):
        raise ValueError("Momentum inputs must be aligned")
    features = np.full((count, len(MOMENTUM_FEATURE_NAMES)), np.nan)
    eligible = np.zeros(count, dtype=bool)
    weights = np.zeros(count)
    if count < 253:
        return eligible, weights, features
    returns = np.diff(closes) / closes[:-1]
    spy_returns = np.diff(spy) / spy[:-1]
    t = np.arange(252, count)
    score = closes[t - 21] / closes[t - 252] - 1
    average_200 = _rolling(closes, 200).mean(axis=1)[t - 199]
    volatility = _rolling(returns, 60).std(axis=1, ddof=1)[t - 60]
    liquidity = _rolling(closes * volumes, 20).mean(axis=1)[t - 19]
    average_volume = _rolling(volumes, 20).mean(axis=1)[t - 19]
    spy_volatility = _rolling(spy_returns, 20).std(axis=1, ddof=1)[t - 20] * np.sqrt(252.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        features[t] = np.column_stack((
            score, volatility, closes[t] / average_200 - 1, closes[t] / closes[t - 20] - 1,
            closes[t] / closes[t - 5] - 1, np.where(average_volume > 0, volumes[t] / average_volume, 0.0),
            closes[t] / closes[t - 20] - group[t] / group[t - 20], spy[t] / spy[t - 20] - 1, spy_volatility))
        eligible[t] = ((score > 0) & (closes[t] > average_200) & (volatility > 0) & (closes[t] > minimum_price)
                       & (liquidity > minimum_average_dollar_volume) & np.isfinite(features[t]).all(axis=1))
        weights[t] = np.where(eligible[t], 1 / volatility, 0.0)
    return eligible, weights, features


def next_open_outcome(bars: Sequence[DailyBar], day: int, horizon: int, round_trip_cost: float) -> float | None:
    """Net return from the next session's open to the open ``horizon`` sessions later."""
    if day + 1 + horizon >= len(bars):
        return None
    return bars[day + 1 + horizon].open / bars[day + 1].open - 1 - round_trip_cost


REGIME_FEATURE_NAMES = ("spy_trend_60d", "spy_volatility_20d", "breadth_above_ma50", "average_correlation_60d")


def regime_features(spy_bars: Sequence[DailyBar], stock_bars: Mapping[str, Sequence[DailyBar]]) -> np.ndarray:
    """Market-state features per session from trailing data only; NaN before 200 sessions.

    Breadth is the fraction of aligned stocks above their 50-session average and
    correlation the mean pairwise correlation of their last 60 daily returns.
    """
    spy = np.asarray([b.close for b in spy_bars], dtype=float)
    dates = [b.date for b in spy_bars]
    aligned = [np.asarray([b.close for b in bars], dtype=float) for _, bars in sorted(stock_bars.items())
               if [b.date for b in bars] == dates]
    rows = np.full((len(spy), len(REGIME_FEATURE_NAMES)), np.nan)
    if len(spy) < 201 or len(aligned) < 2:
        return rows
    closes = np.column_stack(aligned)
    returns = np.diff(closes, axis=0) / closes[:-1]
    spy_returns = np.diff(spy) / spy[:-1]
    for t in range(200, len(spy)):
        with np.errstate(divide="ignore", invalid="ignore"):
            correlation = np.corrcoef(returns[t - 60:t], rowvar=False)
        upper = correlation[np.triu_indices(len(aligned), 1)]
        rows[t] = (spy[t] / spy[t - 60] - 1,
                   np.std(spy_returns[t - 20:t], ddof=1) * np.sqrt(252.0),
                   np.mean(closes[t] > closes[t - 49:t + 1].mean(axis=0)),
                   np.nanmean(upper) if np.isfinite(upper).any() else np.nan)
    return rows
