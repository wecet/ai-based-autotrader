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


