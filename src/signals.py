"""Daily model training and cached rankings, independent of order execution."""
import logging
from market_data import NY
from model_loader import load_model


class DailySignals:
    def __init__(self, history, universe, model=None):
        self.history = history
        self.universe = universe
        self.cs = load_model()
        self.model = model if model is not None else self.cs.CrossSectionalModel()
        self.signal_date = None
        self.ranked = []

    def refresh(self, now):
        today = now.astimezone(NY).date()
        if self.signal_date == today:
            return
        history = self.history.load(now, self.universe)
        training = self.cs.build_ranked_training_set(
            history.stocks, history.spy, history.sectors, self.universe)
        rows = self.cs.build_feature_rows(
            history.stocks, history.spy, history.sectors, self.universe)
        if len(rows) < 2 or not training:
            raise ValueError("Insufficient liquid stocks or training rows")
        self.model.fit(training)
        self.ranked = self.model.rank(rows)
        self.signal_date = today
        logging.info("Refreshed daily model with %s rows; ranked %s stocks", len(training), len(rows))

    def candidates(self, weights, equity):
        eligible = {row.symbol for row, _, _ in self.ranked}
        retained = sum(weight for symbol, weight in weights.items() if symbol not in eligible)
        return self.model.construct_portfolio(
            self.ranked, current_weights=weights, portfolio_value=equity,
            maximum_total_exposure=max(0, .5 - retained))
