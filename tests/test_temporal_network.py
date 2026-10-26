from datetime import date, timedelta

import numpy as np
import pytest

from core.registry import ModelRegistry
from core.types import DailyBar, MarketHistory, PortfolioLimits
from models.temporal_network import TemporalNetworkModel
from trading.temporal import TemporalConvNet, TrainingBudget, build_sequences, sequence_channels

TINY = TrainingBudget(epochs=3, batch_size=64, hidden=4, dilations=(1, 2))


def test_network_is_causal_and_deterministic():
    generator = np.random.default_rng(2)
    x, y = generator.normal(size=(200, 12, 3)), generator.normal(size=200)
    first = TemporalConvNet(TINY).fit(x, y)
    second = TemporalConvNet(TINY).fit(x, y)
    np.testing.assert_array_equal(first.predict(x), second.predict(x))
    assert first.updates == 3 * 4
    probe = x[:3].copy()
    changed = probe.copy()
    changed[:, 8:] += 5
    np.testing.assert_array_equal(first.hidden(probe)[:, :8], first.hidden(changed)[:, :8])
    assert not np.array_equal(first.hidden(probe)[:, 8:], first.hidden(changed)[:, 8:])


def test_network_learns_a_simple_sequence_signal():
    generator = np.random.default_rng(3)
    x = generator.normal(size=(600, 10, 2))
    y = x[:, -3:, 0].sum(axis=1)
    network = TemporalConvNet(TrainingBudget(epochs=60, batch_size=64, learning_rate=.01, hidden=8)).fit(x, y)
    assert network.losses[-1] < .25 * network.losses[0]
    assert np.corrcoef(network.predict(x), y)[0, 1] > .9
    with pytest.raises(RuntimeError):
        TemporalConvNet().predict(x)


def history(count=260, seed=4):
    generator = np.random.default_rng(seed)
    days = [date(2023, 1, 2) + timedelta(days=i) for i in range(count)]
    def series(drift):
        closes = 100 * np.exp(np.cumsum(generator.normal(drift, .01, count)))
        volume = generator.uniform(5e5, 1.5e6, count)
        return [DailyBar(d, float(c), float(c), float(c), float(c), float(v)) for d, c, v in zip(days, closes, volume)]
    spy = series(.0002)
    return MarketHistory({s: series(d) for s, d in zip('ABCDEF', (.001, .0005, 0, -.0005, .0008, .0002))},
                         spy, {'GROUP': spy})


UNIVERSE = {s: 'GROUP' for s in 'ABCDEF'}


def test_sequences_are_point_in_time_with_within_date_ranks():
    data = history()
    labeled, latest = build_sequences(data.stocks, data.spy, data.sectors, UNIVERSE, length=32, horizon=5)
    truncated, _ = build_sequences({s: b[:200] for s, b in data.stocks.items()}, data.spy[:200],
                                   {'GROUP': data.spy[:200]}, UNIVERSE, length=32, horizon=5)
    early = [row for row in labeled if row[0] + 5 < 200]
    assert [(r[0], r[1]) for r in early] == [(r[0], r[1]) for r in truncated]
    assert all(np.array_equal(a[2], b[2]) and a[4] == b[4] for a, b in zip(early, truncated))
    assert max(row[0] for row in labeled) == 259 - 5
    assert set(latest) == set(UNIVERSE)
    assert {row[4] for row in labeled if row[0] == 100} == {0, .2, .4, .6, .8, 1}
    channels = sequence_channels(data.stocks['A'], data.spy, data.spy)
    assert np.isnan(channels[:20]).all() and (channels[20:, 2] == channels[20:, 1]).all()


def test_model_ranks_with_purged_calibration_and_logs_retraining_cost():
    log = []
    model = TemporalNetworkModel(budget=TINY, cost_log=log)
    model.prepare(history(), UNIVERSE)
    assert len(model.ranked) == 6
    assert log[0]['updates'] > 0 and log[0]['examples'] > 0
    assert model.return_calibration[1] >= 0
    candidates = model.candidates({'A': .05}, 100_000, PortfolioLimits(.5, .5, .1, 5))
    assert all(c.desired_weight <= .1 for c in candidates)
    assert all(c.predicted_return is not None for c in candidates)


def test_model_requires_enough_labeled_history():
    with pytest.raises(ValueError, match='120 labeled sessions'):
        TemporalNetworkModel(budget=TINY).prepare(history(150), UNIVERSE)


def test_temporal_network_is_discoverable():
    assert ModelRegistry().require('temporal_network').model_type is TemporalNetworkModel
