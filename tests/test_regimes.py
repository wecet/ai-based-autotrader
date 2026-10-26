from datetime import date, timedelta

import numpy as np
import pytest

from core.registry import ModelRegistry
from core.types import DailyBar, MarketHistory, PortfolioLimits
from models.regime_composite import RegimeCompositeModel
from research.regime_analysis import margin_variant, regime_stability
from trading.features import regime_features
from trading.regimes import FilteredRegimeModel, dominant_regimes


def market(count=320, seed=4):
    generator = np.random.default_rng(seed)
    days = [date(2022, 1, 3) + timedelta(days=i) for i in range(count)]
    common = generator.normal(.0003, .01, count)
    def series(beta, drift):
        closes = 100 * np.exp(np.cumsum(beta * common + drift + generator.normal(0, .006, count)))
        return [DailyBar(d, float(c), float(c), float(c), float(c), 1_000_000) for d, c in zip(days, closes)]
    spy = series(1, 0)
    return MarketHistory({'AAA': series(1.2, .001), 'BBB': series(.8, 0), 'CCC': series(1, -.0005)},
                         spy, {'GROUP': series(1, .0002), 'SPY': spy})


UNIVERSE = {'AAA': 'GROUP', 'BBB': 'GROUP', 'CCC': 'SPY'}


def test_regime_features_are_trailing_and_bounded():
    history = market()
    full = regime_features(history.spy, history.stocks)
    truncated = regime_features(history.spy[:250], {s: b[:250] for s, b in history.stocks.items()})
    np.testing.assert_array_equal(full[:250], truncated)
    assert np.isnan(full[:200]).all() and np.isfinite(full[200:]).all()
    assert ((full[200:, 2] >= 0) & (full[200:, 2] <= 1)).all()
    assert (full[200:, 3] > 0).all()


def clustered(count_per_state=40, seed=0):
    generator = np.random.default_rng(seed)
    centers = {'trend': (.08, .10, .8, .2), 'range': (0, .15, .5, .3), 'stress': (-.10, .40, .2, .7)}
    blocks = [generator.normal(center, .01, (count_per_state, 4)) for center in centers.values()]
    return np.vstack(blocks)


def test_states_are_named_from_centroids_and_filtered_causally():
    features = clustered()
    model = FilteredRegimeModel().fit(features)
    probabilities = model.filter(features)
    assert probabilities['trend'][:40].mean() > .9
    assert probabilities['range'][40:80].mean() > .9
    assert probabilities['stress'][80:].mean() > .9
    prefix = model.filter(features[:60])
    for name in prefix:
        np.testing.assert_allclose(prefix[name], probabilities[name][:60])
    total = sum(probabilities.values())
    np.testing.assert_allclose(total, 1)


def test_regime_model_requires_enough_finite_rows():
    with pytest.raises(ValueError):
        FilteredRegimeModel().fit(np.ones((10, 4)))


def test_switch_margin_adds_hysteresis():
    probabilities = {'trend': [.6, .45, .3, .6], 'range': [.3, .5, .65, .3], 'stress': [.1, .05, .05, .1]}
    assert dominant_regimes(probabilities) == ['trend', 'range', 'range', 'trend']
    assert dominant_regimes(probabilities, margin=.2) == ['trend', 'trend', 'range', 'trend']


def test_composite_scales_exposure_by_stress_and_logs_decisions():
    log = []
    model = RegimeCompositeModel(decision_log=log)
    model.prepare(market(), UNIVERSE)
    assert model.active in {'trend', 'range'}
    assert log[-1]['date'] == market().spy[-1].date.isoformat()
    assert sum(log[-1][f'p_{name}'] for name in ('trend', 'range', 'stress')) == pytest.approx(1)
    limits = PortfolioLimits(.5, .5, .5, 5)
    model.stress_probability = .6
    model.active = 'trend'
    candidates = model.candidates({}, 100_000, limits)
    assert sum(c.desired_weight for c in candidates) <= .2 + 1e-12
    model.stress_probability = 1.0
    assert all(c.desired_weight == 0 for c in model.candidates({}, 100_000, limits))


def test_composite_falls_back_when_preferred_component_is_unavailable():
    class NoTrend(RegimeCompositeModel):
        def __init__(self):
            super().__init__()
            failing = self.components['trend']
            def fail(history, universe):
                raise ValueError('trend unavailable')
            failing.prepare = fail
    model = NoTrend()
    model.prepare(market(), UNIVERSE)
    assert model.active == 'range'


def test_composite_requires_regime_history():
    with pytest.raises(ValueError, match='201 sessions'):
        RegimeCompositeModel().prepare(market(150), UNIVERSE)


def test_stability_counts_switches_and_spells():
    decisions = [{'date': f'2024-01-0{i}', 'regime': r, 'component': c, 'p_trend': .7, 'p_range': .2, 'p_stress': .1}
                 for i, (r, c) in enumerate([('trend', 'trend'), ('trend', 'trend'), ('stress', 'trend'),
                                             ('range', 'range')], 1)]
    summary = regime_stability(decisions)
    assert summary['regime_switches'] == 2
    assert summary['component_switches'] == 1
    assert summary['mean_spell_sessions'] == pytest.approx(4 / 3)
    assert summary['regime_share'] == {'range': .25, 'stress': .25, 'trend': .5}
    assert margin_variant(.2).switch_margin == .2


def test_regime_composite_is_discoverable():
    assert ModelRegistry().require('regime_composite').model_type is RegimeCompositeModel
