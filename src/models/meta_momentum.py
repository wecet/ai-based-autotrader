"""Momentum rotation whose new entries are filtered by a meta-labelling classifier."""
from typing import Protocol

import numpy as np

from core.contracts import Model
from core.registry import ModelDefinition
from trading.allocation import TargetSignal, allocate_targets, veto_new_entries
from trading.features import momentum_panel, next_open_outcome

HORIZON_SESSIONS = 21
ROUND_TRIP_COST = .002
MINIMUM_EXAMPLES = 60


class OpportunityClassifier(Protocol):
    def fit(self, features: np.ndarray, labels: np.ndarray) -> object: ...

    def predict_proba(self, features: np.ndarray) -> np.ndarray: ...


def logistic_classifier():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=500))


def label_opportunities(panels, history, *, horizon=HORIZON_SESSIONS, cost=ROUND_TRIP_COST):
    """Every past rule opportunity whose next-open outcome completed by the latest bar.

    Labels are 1 when the net return beats zero. Selection by ``top_n`` or the
    classifier is ignored, so opportunities the strategy did not take are labeled too.
    """
    features, labels = [], []
    for symbol, (eligible, _, rows) in panels.items():
        bars = history.stocks[symbol]
        for day in np.flatnonzero(eligible):
            outcome = next_open_outcome(bars, int(day), horizon, cost)
            if outcome is not None:
                features.append(rows[day])
                labels.append(int(outcome > 0))
    return np.asarray(features, dtype=float), np.asarray(labels, dtype=int)


class MetaMomentumModel(Model):
    """Momentum rule supplies opportunities; a classifier decides which new ones to accept.

    The classifier never sizes positions or delays exits. Without enough labeled
    outcomes of both classes it accepts no new entries.
    """

    threshold = .5

    def __init__(self, classifier_factory=logistic_classifier, decision_log=None):
        self.classifier_factory = classifier_factory
        self.decision_log = decision_log
        self.signals = []
        self.universe = {}
        self.rejected = set()
        self.decisions = []
        self.filter_status = "unfitted"

    def prepare(self, history, universe):
        dates = [b.date for b in history.spy]
        panels = {}
        for symbol, bars in sorted(history.stocks.items()):
            group = history.sectors.get(universe.get(symbol, ""), [])
            if symbol not in universe or [b.date for b in bars] != dates or [b.date for b in group] != dates:
                continue
            closes = np.asarray([b.close for b in bars], dtype=float)
            if len(bars) < 253 or not np.isfinite(closes).all() or np.any(closes <= 0):
                continue
            # Match momentum rotation: illiquid symbols get no signal, so holdings are retained.
            liquidity = np.mean([b.close * b.volume for b in bars[-20:]])
            if closes[-1] <= 10 or not np.isfinite(liquidity) or liquidity <= 5_000_000:
                continue
            panels[symbol] = momentum_panel(bars, history.spy, group)
        signals = [TargetSignal(symbol, history.stocks[symbol][-1].close, universe[symbol],
                                float(features[-1, 0]), allocation_weight=float(weights[-1]))
                   for symbol, (eligible, weights, features) in panels.items() if np.isfinite(features[-1]).all()]
        if not signals:
            raise ValueError("Meta momentum requires liquid, aligned histories of at least 253 sessions")
        self.signals, self.universe = signals, dict(universe)
        self.rejected = self.classify(panels, history)

    def classify(self, panels, history):
        entries = [s.symbol for s in self.signals if s.allocation_weight > 0]
        features, labels = label_opportunities(panels, history)
        self.training_examples = len(labels)
        self.decisions = []
        if len(labels) < MINIMUM_EXAMPLES or len(set(labels.tolist())) < 2:
            self.filter_status = "insufficient_examples"
            probabilities = [None] * len(entries)
        else:
            classifier = self.classifier_factory()
            classifier.fit(features, labels)
            self.filter_status = "fitted"
            rows = np.asarray([panels[symbol][2][-1] for symbol in entries]).reshape(len(entries), -1)
            probabilities = classifier.predict_proba(rows)[:, 1].tolist() if entries else []
        signal_date = history.spy[-1].date.isoformat()
        for symbol, probability in zip(entries, probabilities):
            accepted = probability is not None and probability >= self.threshold
            self.decisions.append({"date": signal_date, "symbol": symbol,
                                   "probability": probability, "accepted": accepted})
        if self.decision_log is not None:
            self.decision_log.extend(self.decisions)
        return {d["symbol"] for d in self.decisions if not d["accepted"]}

    def target_signals(self):
        return list(self.signals)

    def candidates(self, current_weights, portfolio_value, limits):
        signals = veto_new_entries(self.signals, self.rejected, current_weights)
        return allocate_targets(signals, current_weights, portfolio_value, limits, self.universe, proportional=True)

    def decide_to_buy(self, context):
        return context.desired_weight > context.current_weight and context.order_notional >= self.minimum_order_notional

    def decide_to_sell(self, context):
        return context.desired_weight < context.current_weight and context.order_notional >= self.minimum_order_notional


MODEL = ModelDefinition(
    name="Meta-labelled momentum",
    description="Uses the momentum rotation rule to propose entries, then a logistic classifier trained on "
                "every past rule opportunity's 21-session net outcome rejects those unlikely to beat costs. "
                "Exits and inverse-volatility sizing follow momentum; no new entries until enough outcomes exist.",
    model_type=MetaMomentumModel,
)
