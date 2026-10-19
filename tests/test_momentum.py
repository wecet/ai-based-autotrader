import pytest

from core.types import DailyBar, MarketHistory, PortfolioLimits
from models.momentum_rotation import MomentumRotationModel


def test_momentum_selects_positive_trends_and_exits_negative_trends(strategy_dataset):
    dataset = strategy_dataset(300)
    def series(growth):
        return [DailyBar(b.date, 100 * (1 + growth) ** i,
                         101 * (1 + growth) ** i, 99 * (1 + growth) ** i,
                         100 * (1 + growth) ** i, 1_000_000)
                for i, b in enumerate(dataset.history.spy)]
    up, down = series(.002), series(-.001)
    history = MarketHistory({'AAA': up, 'BBB': down}, dataset.history.spy, dataset.history.sectors)
    model = MomentumRotationModel()
    model.prepare(history, {'AAA': 'GROUP', 'BBB': 'GROUP'})
    candidates = model.candidates({'BBB': .1}, 1000, PortfolioLimits())
    targets = {c.symbol: c.desired_weight for c in candidates}
    assert targets == pytest.approx({'AAA': .1, 'BBB': 0})
    assert all(c.predicted_return is None for c in candidates)
    assert all(not (model.decide_to_buy(c) and model.decide_to_sell(c)) for c in candidates)


def test_momentum_never_liquidates_missing_or_illiquid_histories(strategy_dataset):
    dataset = strategy_dataset(300)
    model = MomentumRotationModel()
    dataset.history.stocks['SHORT'] = dataset.history.spy[-20:]
    model.prepare(dataset.history, {**dataset.universe, 'SHORT': 'GROUP'})
    assert 'SHORT' not in {s.symbol for s in model.signals}
    candidates = model.candidates({'SHORT': .3}, 1000, PortfolioLimits())
    assert sum(c.desired_weight for c in candidates) <= .2 + 1e-10


def test_momentum_requires_sufficient_history(strategy_dataset):
    dataset = strategy_dataset(200)
    with pytest.raises(ValueError, match='253'):
        MomentumRotationModel().prepare(dataset.history, dataset.universe)
