from datetime import date, timedelta

import numpy as np
import pytest

from core.registry import ModelRegistry
from core.types import DailyBar, MarketHistory, PortfolioLimits
from models.residual_reversion import (FilteredResidualReversionModel, ResidualReversionModel,
                                       breakdown_examples)
from trading.features import residual_zscores, reversion_episode_age


def series(returns, start=100.0):
    return start * np.cumprod(np.r_[1, 1 + np.asarray(returns)])


def factor_paths(count=120, seed=1):
    generator = np.random.default_rng(seed)
    market = generator.normal(0, .01, count)
    group = market + generator.normal(0, .004, count)
    stock = 1.2 * market + generator.normal(0, .004, count)
    return market, group, stock


def test_residual_score_detects_weakness_unexplained_by_market_and_group():
    market, group, stock = factor_paths()
    shocked = stock.copy()
    shocked[-5:] -= .02
    calm = residual_zscores(series(stock), series(market), series(group))
    weak = residual_zscores(series(shocked), series(market), series(group))
    assert np.isnan(calm[:65]).all()
    assert abs(calm[-1]) < 2
    assert weak[-1] < -4


def test_market_wide_selloff_is_not_a_residual_signal():
    market, group, stock = factor_paths()
    market[-5:] -= .03
    group[-5:] -= .03
    stock[-5:] -= .036
    assert abs(residual_zscores(series(stock), series(market), series(group))[-1]) < 2


def test_residual_scores_never_use_later_closes():
    market, group, stock = factor_paths()
    full = residual_zscores(series(stock), series(market), series(group))
    truncated = residual_zscores(series(stock[:90]), series(market[:90]), series(group[:90]))
    assert np.allclose(full[:91], truncated, equal_nan=True)
    with pytest.raises(ValueError):
        residual_zscores([1, 2, 3], [1, 2], [1, 2, 3])


@pytest.mark.parametrize('scores, expected', [
    ([0, -2.5, -1.5, -1], 2),
    ([0, -2.5, 0], None),
    ([0, -2.5, -2.5, -2.5, -2.5], None),
    ([0, -2.5, -2.5, -2.5, -1.9, -2.1], 0),
    ([0, -2.5, float('nan'), -2.5], None),
    ([-2.5, -2.4], 1),
])
def test_episode_entry_exit_holding_limit_and_rearming(scores, expected):
    assert reversion_episode_age(scores, entry=-2, exit=-.5, maximum_sessions=2) == expected


def bars(closes, start=date(2023, 1, 2)):
    return [DailyBar(start + timedelta(days=i), c, c, c, c, 1_000_000) for i, c in enumerate(closes)]


def market_history(count=120, shock=.02):
    market, group, stock = factor_paths(count)
    weak = stock.copy()
    weak[-5:] -= shock
    calm = 1.1 * market + np.random.default_rng(9).normal(0, .004, count)
    spy = bars(series(market))
    return MarketHistory({'WEAK': bars(series(weak)), 'CALM': bars(series(calm))}, spy,
                         {'GROUP': bars(series(group)), 'SPY': spy})


UNIVERSE = {'WEAK': 'GROUP', 'CALM': 'SPY'}


def test_model_buys_residual_weakness_and_exits_recovered_holdings():
    model = ResidualReversionModel()
    model.prepare(market_history(), UNIVERSE)
    limits = PortfolioLimits(.5, .2, .1, 5)
    candidates = {c.symbol: c for c in model.candidates({'CALM': .1}, 10_000, limits)}
    assert candidates['WEAK'].desired_weight == pytest.approx(.1)
    assert candidates['WEAK'].predicted_return is None
    assert model.decide_to_buy(candidates['WEAK'])
    assert candidates['CALM'].desired_weight == 0
    assert model.decide_to_sell(candidates['CALM'])
    assert sum(c.desired_weight for c in candidates.values()) <= limits.maximum_total_exposure


def test_model_rejects_short_or_misaligned_history():
    history = market_history(60)
    with pytest.raises(ValueError, match='81 sessions'):
        ResidualReversionModel().prepare(history, UNIVERSE)


def test_entry_veto_never_blocks_exits_or_existing_holdings():
    class VetoAll(ResidualReversionModel):
        def reject_entries(self, history, universe):
            return {s.symbol for s in self.signals}
    model = VetoAll()
    model.prepare(market_history(), UNIVERSE)
    limits = PortfolioLimits(.5, .2, .1, 5)
    fresh = {c.symbol: c.desired_weight for c in model.candidates({}, 10_000, limits)}
    held = {c.symbol: c.desired_weight for c in model.candidates({'WEAK': .05, 'CALM': .1}, 10_000, limits)}
    assert fresh == {}
    assert held == pytest.approx({'WEAK': .1, 'CALM': 0})


def test_filter_without_enough_examples_vetoes_new_entries():
    model = FilteredResidualReversionModel()
    model.prepare(market_history(260), UNIVERSE)
    assert model.filter_status == 'insufficient_examples'
    assert model.rejected == {'WEAK'}


def test_breakdown_labels_only_use_completed_outcomes():
    history = market_history(260, shock=.0)
    model = ResidualReversionModel()
    model.prepare(history, UNIVERSE)
    last = len(history.spy) - 1
    scores = {s: np.full(last + 1, -3.0) for s in model.scores}
    features, labels, latest = breakdown_examples(history, UNIVERSE, scores)
    assert len(labels) == 2 * (last - 5 - 200 + 1)
    assert set(latest) == {'WEAK', 'CALM'}
    assert features.shape[1] == 17
    assert set(labels.tolist()) <= {0, 1}


def test_fitted_filter_rejects_high_breakdown_probability():
    model = FilteredResidualReversionModel()
    history = market_history(260)
    model.prepare(history, UNIVERSE)
    model.scores = {s: np.where(np.arange(len(history.spy)) % 2 == 0, -3.0, 0.0) for s in model.scores}
    model.breakdown_threshold = -1
    rejected = model.reject_entries(history, UNIVERSE)
    assert model.filter_status == 'fitted'
    assert rejected == {s.symbol for s in model.signals if s.allocation_weight > 0}


def test_reversion_model_is_discoverable():
    assert ModelRegistry().require('residual_reversion').model_type is ResidualReversionModel


def reference_zscores(closes, market, group, lookback=60, shock=5):
    returns = np.diff(closes) / closes[:-1]
    factors = np.column_stack((np.ones(len(returns)), np.diff(market) / market[:-1], np.diff(group) / group[:-1]))
    scores = np.full(len(closes), np.nan)
    for t in range(lookback + shock, len(closes)):
        fit = slice(t - shock - lookback, t - shock)
        coefficients = np.linalg.lstsq(factors[fit], returns[fit], rcond=None)[0]
        sigma = np.std(returns[fit] - factors[fit] @ coefficients, ddof=3)
        window = slice(t - shock, t)
        scores[t] = (returns[window] - factors[window, 1:] @ coefficients[1:]).sum() / (sigma * np.sqrt(shock))
    return scores


@pytest.mark.parametrize('collinear', [False, True])
def test_vectorized_residuals_match_per_date_regressions(collinear):
    market, group, stock = factor_paths(200)
    group_path = series(market) if collinear else series(group)
    expected = reference_zscores(series(stock), series(market), group_path)
    np.testing.assert_allclose(residual_zscores(series(stock), series(market), group_path), expected,
                               rtol=1e-7, atol=1e-9)
    assert np.isnan(residual_zscores(series(stock[:50]), series(market[:50]), series(group[:50]))).all()
