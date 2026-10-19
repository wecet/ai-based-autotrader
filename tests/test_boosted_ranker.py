from datetime import date, timedelta

import numpy as np

from configuration.settings import TradingSettings
from core.registry import ModelRegistry
from core.types import FeatureRow, PortfolioLimits
from models.boosted_ranker import BoostedRankingModel
from trading.estimators import BoostedRanker


def test_new_models_are_discovered_without_import_time_training():
    catalog = ModelRegistry().catalog()
    assert not catalog['unavailable']
    assert {'cross_sectional', 'momentum_rotation', 'boosted_ranker'} <= {m['id'] for m in catalog['models']}


def test_boosting_learns_nonlinear_relationship_deterministically():
    x = np.linspace(-2, 2, 300).reshape(-1, 1)
    y = x[:, 0] ** 2
    first, second = BoostedRanker(), BoostedRanker()
    first.fit(x, y)
    second.fit(x, y)
    prediction = first.predict(x)
    assert np.mean((prediction - y) ** 2) < .05
    np.testing.assert_allclose(prediction, second.predict(x))
    assert first.regressor.early_stopping is False


def test_boosted_calibration_is_later_than_training_with_purged_horizon():
    class RecordingEstimator:
        def fit(self, x, y):
            self.seen = x[:, 0].copy()
        def predict(self, x):
            return x[:, 0] / 100
    start = date(2024, 1, 1)
    rows = [FeatureRow(start + timedelta(days=i), 'AAA', 'GROUP', 100, 1e7,
                       (float(i),), i / 1000, i / 100) for i in range(60)]
    model = BoostedRankingModel()
    model.estimator = RecordingEstimator()
    model.fit(rows)
    assert model.training_end + timedelta(days=5) < model.calibration_start
    assert max(model.estimator.seen) == 42
    assert model.calibration_start == start + timedelta(days=48)
    np.testing.assert_allclose(model.return_calibration, [0, .1], atol=1e-12)


def test_boosted_model_runs_with_shared_features_and_configured_limits(strategy_dataset):
    dataset = strategy_dataset(270)
    model = BoostedRankingModel()
    model.configure(TradingSettings(universe=dataset.universe))
    model.prepare(dataset.history, dataset.universe)
    candidates = model.candidates({}, 10000, PortfolioLimits(.3, .15, .1, 3))
    assert model.training_end < model.calibration_start
    assert sum(c.desired_weight for c in candidates) <= .3 + 1e-12
    assert all(c.desired_weight <= .1 for c in candidates)
    assert all(c.predicted_return is not None for c in candidates)


def test_boosted_model_abstains_when_ranks_have_negative_calibration(strategy_dataset):
    dataset = strategy_dataset(270)
    model = BoostedRankingModel()
    model.prepare(dataset.history, dataset.universe)
    model.return_calibration = (.02, 0)
    candidates = model.candidates({'AAA': .1}, 1000, PortfolioLimits())
    assert all(c.desired_weight == 0 for c in candidates)
