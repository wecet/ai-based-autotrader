"""Long-only residual mean reversion with explicit exits and an optional breakdown filter."""
import math

import numpy as np

from core.contracts import Model
from core.registry import ModelDefinition
from trading.allocation import TargetSignal, allocate_targets, trailing_returns
from trading.features import calculate_features, residual_zscores, reversion_episode_age

ENTRY_SCORE = -2.0
EXIT_SCORE = -.5
MAXIMUM_HOLDING_SESSIONS = 10
OUTCOME_SESSIONS = 5
ROUND_TRIP_COST = .002


class ResidualReversionModel(Model):
    """Buy unusually weak residual moves versus SPY and the group benchmark.

    Scores are standardized residuals, not forecasts. Positions exit when the
    residual recovers, after the holding limit, or when the score is unavailable.
    """

    minimum_history = 81

    def __init__(self):
        self.signals = []
        self.universe = {}
        self.rejected = set()

    def prepare(self, history, universe):
        signals = []
        self.scores = {}
        dates = [b.date for b in history.spy]
        for symbol, bars in sorted(history.stocks.items()):
            group = history.sectors.get(universe.get(symbol, ""), [])
            if len(bars) < self.minimum_history or [b.date for b in bars] != dates or [b.date for b in group] != dates:
                continue
            closes = np.asarray([b.close for b in bars], dtype=float)
            if not np.isfinite(closes).all() or np.any(closes <= 0):
                continue
            liquidity = np.mean([b.close * b.volume for b in bars[-20:]])
            if closes[-1] <= 10 or not math.isfinite(liquidity) or liquidity <= 5_000_000:
                continue
            scores = residual_zscores(closes, [b.close for b in history.spy], [b.close for b in group])
            returns = trailing_returns(closes)
            if not np.isfinite(scores[-1]) or returns is None:
                continue
            self.scores[symbol] = scores
            age = reversion_episode_age(scores, entry=ENTRY_SCORE, exit=EXIT_SCORE,
                                        maximum_sessions=MAXIMUM_HOLDING_SESSIONS)
            weight = 1 / float(np.std(returns, ddof=1)) if age is not None else 0.0
            signals.append(TargetSignal(symbol, float(closes[-1]), universe[symbol], -float(scores[-1]),
                                        allocation_weight=weight))
        if not signals:
            raise ValueError(f"Residual reversion requires liquid, aligned histories of at least "
                             f"{self.minimum_history} sessions")
        self.signals, self.universe = signals, dict(universe)
        self.rejected = self.reject_entries(history, universe)

    def reject_entries(self, history, universe):
        """Symbols whose new entries are vetoed; existing holdings still follow exit rules."""
        return set()

    def target_signals(self):
        return list(self.signals)

    def candidates(self, current_weights, portfolio_value, limits):
        signals = [TargetSignal(s.symbol, s.price, s.group, s.score, s.expected_return,
                                0.0 if s.symbol in self.rejected and current_weights.get(s.symbol, 0) <= 0
                                else s.allocation_weight)
                   for s in self.signals]
        return allocate_targets(signals, current_weights, portfolio_value, limits, self.universe, proportional=True)

    def decide_to_buy(self, context):
        return context.desired_weight > context.current_weight and context.order_notional >= self.minimum_order_notional

    def decide_to_sell(self, context):
        return context.desired_weight < context.current_weight and context.order_notional >= self.minimum_order_notional


def breakdown_examples(history, universe, scores):
    """Past entry signals with features at the signal date and a completed outcome label.

    Label 1 means the next ``OUTCOME_SESSIONS`` close-to-close return failed to cover
    a round trip. Only outcomes observed by the latest completed bar are used.
    """
    features, labels, latest = [], [], {}
    last = len(history.spy) - 1
    for symbol, series in scores.items():
        bars, group = history.stocks[symbol], history.sectors[universe[symbol]]
        for day in range(200, last + 1):
            if not series[day] <= ENTRY_SCORE:
                continue
            try:
                values, _ = calculate_features(bars[:day + 1], history.spy[:day + 1], group[:day + 1])
            except (ValueError, ZeroDivisionError, FloatingPointError):
                continue
            row = (*values, float(series[day]))
            if not np.all(np.isfinite(row)):
                continue
            if day == last:
                latest[symbol] = row
            elif day + OUTCOME_SESSIONS <= last:
                outcome = bars[day + OUTCOME_SESSIONS].close / bars[day].close - 1
                features.append(row)
                labels.append(int(outcome < ROUND_TRIP_COST))
    return np.asarray(features, dtype=float), np.asarray(labels, dtype=int), latest


class FilteredResidualReversionModel(ResidualReversionModel):
    """Adds a logistic breakdown classifier fitted on past entry signals only.

    Without at least 40 labeled signals of both outcomes the filter cannot be
    trained and vetoes every new entry; held positions still exit normally.
    """

    minimum_history = 206
    breakdown_threshold = .5

    def reject_entries(self, history, universe):
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        entries = {s.symbol for s in self.signals if s.allocation_weight > 0}
        features, labels, latest = breakdown_examples(history, universe, self.scores)
        self.filter_examples = len(labels)
        if len(labels) < 40 or len(set(labels.tolist())) < 2:
            self.filter_status = "insufficient_examples"
            return entries
        classifier = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=500))
        classifier.fit(features, labels)
        self.filter_status = "fitted"
        # Episodes that began earlier have no signal row today; judge them by today's state.
        rejected = set()
        for symbol in entries:
            row = latest.get(symbol)
            if row is None:
                bars, group = history.stocks[symbol], history.sectors[universe[symbol]]
                values, _ = calculate_features(bars, history.spy, group)
                row = (*values, float(self.scores[symbol][-1]))
            if classifier.predict_proba(np.asarray([row]))[0, 1] > self.breakdown_threshold:
                rejected.add(symbol)
        return rejected


MODEL = ModelDefinition(
    name="Residual mean reversion",
    description="Buys stocks whose last five sessions fell unusually far below what SPY and their "
                "benchmark explain (standardized residual of -2 or lower). Exits when the residual "
                "recovers to -0.5, after ten sessions, or when data is unavailable. Long-only, "
                "inverse-volatility sized inside your exposure limits.",
    model_type=ResidualReversionModel,
)
