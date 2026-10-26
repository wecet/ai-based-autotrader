"""Five-session return ranks learned from return/volume sequences by a compact causal TCN."""
import time

import numpy as np

from core.contracts import Model
from core.registry import ModelDefinition
from trading.allocation import TargetSignal, allocate_targets
from trading.temporal import TemporalConvNet, TrainingBudget, build_sequences

SEQUENCE_SESSIONS = 32
HORIZON = 5
MINIMUM_LABELED_SESSIONS = 120


class TemporalNetworkModel(Model):
    """Trains on the earliest 80% of labeled dates minus a five-date purge, calibrates on the rest.

    All symbols on a date stay in the same fold. A nonpositive calibration slope
    disables new allocations, as in boosted ranking. ``training_seconds`` and
    ``updates`` record retraining cost.
    """

    safety_margin = .002

    def __init__(self, budget=TrainingBudget(), cost_log=None):
        self.budget = budget
        self.cost_log = cost_log
        self.universe = {}
        self.ranked = []
        self.return_calibration = (0.0, 0.0)

    def prepare(self, history, universe):
        labeled, latest = build_sequences(history.stocks, history.spy, history.sectors, universe,
                                          length=SEQUENCE_SESSIONS, horizon=HORIZON)
        dates = sorted({row[0] for row in labeled})
        if len(dates) < MINIMUM_LABELED_SESSIONS or len(latest) < 2:
            raise ValueError(f"Temporal network requires {MINIMUM_LABELED_SESSIONS} labeled sessions "
                             "and two current liquid symbols")
        split = int(len(dates) * .8)
        train_dates, calibration_dates = set(dates[:split - HORIZON]), set(dates[split:])
        train = [row for row in labeled if row[0] in train_dates]
        calibration = [row for row in labeled if row[0] in calibration_dates]
        started = time.perf_counter()
        network = TemporalConvNet(self.budget).fit(np.stack([r[2] for r in train]), [r[4] for r in train])
        self.training_seconds = time.perf_counter() - started
        self.updates, self.training_examples = network.updates, len(train)
        prediction = network.predict(np.stack([r[2] for r in calibration]))
        design = np.column_stack((np.ones(len(prediction)), prediction))
        intercept, slope = np.linalg.lstsq(design, np.asarray([r[3] for r in calibration]), rcond=None)[0]
        self.return_calibration = (float(intercept), float(max(0, slope)))
        symbols = sorted(latest)
        ranks = network.predict(np.stack([latest[s][0] for s in symbols]))
        expected = self.return_calibration[0] + self.return_calibration[1] * ranks
        order = np.argsort(ranks)
        percentiles = np.empty(len(symbols))
        percentiles[order] = np.linspace(0, 1, len(symbols))
        self.ranked = [(s, latest[s][1], universe[s], float(p), float(e))
                       for s, p, e in zip(symbols, percentiles, expected)]
        self.universe = dict(universe)
        if self.cost_log is not None:
            self.cost_log.append({"date": history.spy[-1].date.isoformat(), "seconds": self.training_seconds,
                                  "updates": self.updates, "examples": self.training_examples,
                                  "final_loss": network.losses[-1], "calibration_slope": self.return_calibration[1]})

    def target_signals(self):
        threshold = .0005 + .0005 + self.safety_margin
        return [TargetSignal(symbol, price, group, percentile, expected,
                             1.0 if expected > threshold and self.return_calibration[1] > 0 else 0.0)
                for symbol, price, group, percentile, expected in self.ranked]

    def candidates(self, current_weights, portfolio_value, limits):
        return allocate_targets(self.target_signals(), current_weights, portfolio_value, limits, self.universe)

    def decide_to_buy(self, context):
        required = context.estimated_spread + context.estimated_slippage + self.safety_margin
        return (context.desired_weight > context.current_weight and context.predicted_return is not None
                and context.predicted_return > required and context.order_notional >= self.minimum_order_notional)

    def decide_to_sell(self, context):
        return context.desired_weight < context.current_weight and context.order_notional >= self.minimum_order_notional


MODEL = ModelDefinition(
    name="Temporal network ranking",
    description="Learns five-session return ranks from the last 32 sessions of returns, market- and "
                "benchmark-relative returns and volume with a small causal convolutional network. "
                "Calibrates ranks to returns on a later, purged window; only edges above costs receive targets.",
    model_type=TemporalNetworkModel,
)
