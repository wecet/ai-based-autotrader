from datetime import date, timedelta

import numpy as np
import pytest

from core.registry import ModelRegistry
from core.types import DailyBar, MarketHistory, PortfolioLimits
from models.meta_momentum import MetaMomentumModel, label_opportunities
from models.momentum_rotation import MomentumRotationModel
from research.meta_labeling import decision_quality
from trading.features import momentum_panel, next_open_outcome


class Constant:
    def __init__(self, probability):
        self.probability = probability
        self.fitted = None

    def fit(self, features, labels):
        self.fitted = (features.shape, set(labels.tolist()))
        return self

    def predict_proba(self, features):
        return np.column_stack([np.full(len(features), 1 - self.probability), np.full(len(features), self.probability)])


def trending(count=320, seed=2):
    generator = np.random.default_rng(seed)
    days = [date(2022, 1, 3) + timedelta(days=i) for i in range(count)]
    def series(drift, scale=.01):
        closes = 100 * np.exp(np.cumsum(generator.normal(drift, scale, count)))
        opens = np.r_[closes[0], closes[:-1]] * (1 + generator.normal(0, .002, count))
        return [DailyBar(d, float(o), float(max(o, c)), float(min(o, c)), float(c), 1_000_000)
                for d, o, c in zip(days, opens, closes)]
    spy = series(.0003)
    return MarketHistory({'UP': series(.002), 'MID': series(.001), 'DOWN': series(-.002)},
                         spy, {'GROUP': series(.0005), 'SPY': spy})


UNIVERSE = {'UP': 'GROUP', 'MID': 'GROUP', 'DOWN': 'SPY'}


def test_panel_reproduces_momentum_rotation_on_the_latest_session():
    history = trending()
    momentum = MomentumRotationModel()
    momentum.prepare(history, UNIVERSE)
    for signal in momentum.signals:
        eligible, weights, features = momentum_panel(history.stocks[signal.symbol], history.spy,
                                                     history.sectors[UNIVERSE[signal.symbol]])
        assert features[-1, 0] == pytest.approx(signal.score)
        assert weights[-1] == pytest.approx(signal.allocation_weight)
        assert eligible[-1] == (signal.allocation_weight > 0)


def test_panel_rows_never_use_later_bars():
    history = trending()
    args = (history.stocks['UP'], history.spy, history.sectors['GROUP'])
    full = momentum_panel(*args)
    partial = momentum_panel(*(bars[:280] for bars in args))
    for complete, truncated in zip(full, partial):
        np.testing.assert_array_equal(complete[:280], truncated)
    assert not full[0][:252].any() and np.isnan(full[2][:252]).all()


def test_next_open_outcome_is_hand_checkable():
    bars = [DailyBar(date(2024, 1, i + 1), o, o, o, o, 1) for i, o in enumerate([1, 100, 105, 110])]
    assert next_open_outcome(bars, 0, 2, .002) == pytest.approx(.098)
    assert next_open_outcome(bars, 1, 2, .002) is None


def test_labels_include_every_completed_rule_opportunity():
    history = trending()
    panels = {s: momentum_panel(history.stocks[s], history.spy, history.sectors[g]) for s, g in UNIVERSE.items()}
    features, labels = label_opportunities(panels, history)
    expected = sum(int(panel[0][:len(history.spy) - 22].sum()) for panel in panels.values())
    assert len(labels) == expected > 0
    assert features.shape == (expected, 9)


@pytest.mark.parametrize('probability, entries', [(.9, {'UP', 'MID'}), (.1, set())])
def test_classifier_only_vetoes_new_entries(probability, entries):
    model = MetaMomentumModel(classifier_factory=lambda: Constant(probability))
    model.prepare(trending(), UNIVERSE)
    assert model.filter_status == 'fitted'
    limits = PortfolioLimits(.5, .3, .2, 5)
    fresh = {c.symbol for c in model.candidates({}, 100_000, limits) if c.desired_weight > 0}
    held = {c.symbol: c.desired_weight for c in model.candidates({'UP': .1, 'DOWN': .1}, 100_000, limits)}
    assert fresh == entries
    assert held['UP'] > 0
    assert held['DOWN'] == 0
    assert all(d['probability'] == probability for d in model.decisions)


def test_insufficient_outcomes_accept_no_new_entries():
    history = trending(count=275)
    log = []
    model = MetaMomentumModel(decision_log=log)
    model.prepare(history, UNIVERSE)
    assert model.filter_status == 'insufficient_examples'
    assert model.rejected == {s.symbol for s in model.signals if s.allocation_weight > 0}
    assert log and all(d['probability'] is None and not d['accepted'] for d in log)


def test_meta_momentum_requires_long_aligned_history():
    with pytest.raises(ValueError, match='253 sessions'):
        MetaMomentumModel().prepare(trending(count=200), UNIVERSE)


def test_decision_quality_reports_missed_upside_and_calibration():
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(4)]
    def bars(opens):
        return [DailyBar(d, o, o, o, o, 1) for d, o in zip(days, opens)]
    history = MarketHistory({'WIN': bars([1, 100, 110, 1]), 'LOSE': bars([1, 100, 90, 1])},
                            bars([1, 1, 1, 1]), {})
    decisions = [{'date': days[0].isoformat(), 'symbol': 'WIN', 'probability': .2, 'accepted': False},
                 {'date': days[0].isoformat(), 'symbol': 'LOSE', 'probability': .8, 'accepted': True},
                 {'date': days[3].isoformat(), 'symbol': 'WIN', 'probability': None, 'accepted': False}]
    report = decision_quality(decisions, history, horizon=1, cost=0)
    assert report['unresolved'] == 1
    assert report['accepted_mean_net_return'] == pytest.approx(-.1)
    assert report['rejected_mean_net_return'] == pytest.approx(.1)
    assert report['missed_upside'] == pytest.approx(.1)
    assert report['avoided_losses'] == 0
    assert report['brier_score'] == pytest.approx(.64)
    assert [b['count'] for b in report['calibration']] == [1, 1]


def test_meta_momentum_is_discoverable():
    assert ModelRegistry().require('meta_momentum').model_type is MetaMomentumModel
