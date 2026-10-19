"""Nonlinear five-session ranking with purged chronological return calibration."""
import numpy as np

from core.registry import ModelDefinition
from trading.ranking import RankingModel
from trading.allocation import TargetSignal, allocate_targets
from trading.estimators import BoostedRanker


class BoostedRankingModel(RankingModel):
    def __init__(self):
        super().__init__(estimator=BoostedRanker())
        self.universe = {}
        self.training_end = None
        self.calibration_start = None

    def prepare(self, history, universe):
        super().prepare(history, universe)
        self.universe = dict(universe)

    def fit(self, rows):
        usable = [r for r in rows if r.target_rank is not None and r.future_return_5d is not None]
        dates = sorted({r.date for r in usable})
        if len(dates) < 40:
            raise ValueError("Boosted ranking requires at least 40 labeled sessions")
        # These labels use a five-session horizon. Remove five signal dates
        # between estimator fitting and calibration so outcome windows do not overlap.
        split = int(len(dates) * .8)
        train_dates = set(dates[:split - 5])
        calibration_dates = set(dates[split:])
        train = [r for r in usable if r.date in train_dates]
        calibration = [r for r in usable if r.date in calibration_dates]
        self.estimator.fit(np.asarray([r.values for r in train]), np.asarray([r.target_rank for r in train]))
        prediction = self.estimator.predict(np.asarray([r.values for r in calibration]))
        outcome = np.asarray([r.future_return_5d for r in calibration])
        design = np.column_stack((np.ones(len(prediction)), prediction))
        intercept, slope = np.linalg.lstsq(design, outcome, rcond=None)[0]
        # A negative/flat relationship provides no support for the learned rank.
        self.return_calibration = (float(intercept), float(max(0, slope)))
        self.training_end, self.calibration_start = max(train_dates), min(calibration_dates)
        return self

    def candidates(self, current_weights, portfolio_value, limits):
        threshold = .0005 + .0005 + self.safety_margin
        signals = [TargetSignal(row.symbol, row.close, row.sector, percentile, prediction,
                                1.0 if prediction > threshold and self.return_calibration[1] > 0 else 0.0)
                   for row, prediction, percentile in self.ranked]
        return allocate_targets(signals, current_weights, portfolio_value, limits, self.universe)


MODEL = ModelDefinition(
    name="Boosted return ranking",
    description="Learns nonlinear five-session return ranks with gradient-boosted trees. "
                "Converts ranks to return estimates on a later, purged calibration window; "
                "only estimated edges above costs receive targets inside your exposure limits.",
    model_type=BoostedRankingModel,
)
