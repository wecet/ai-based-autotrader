"""Prepare the selected strategy on a daily cadence, independent of its algorithm."""
import logging
from alpaca.data.enums import DataFeed
from core.registry import DEFAULT_MODEL_ID, ModelRegistry
from core.types import PortfolioLimits
from data.historical import NY


class DailySignals:
    def __init__(self, history, universe, model=None, *, registry=None, model_id=DEFAULT_MODEL_ID):
        self.history = history
        self.universe = universe
        self.registry = registry if registry is not None else ModelRegistry()
        self.model_id = model_id
        self.model = model if model is not None else self.registry.create(model_id)
        self.signal_date = None
        self.limits = PortfolioLimits()

    def configure(self, settings):
        changed = (self.model_id != settings.model_id or self.universe != settings.universe
                   or self.history.feed != DataFeed(settings.feed))
        # Build/configure the replacement before retiring the previous strategy.
        replacement = self.registry.create(settings.model_id) if changed else self.model
        replacement.configure(settings)
        if changed:
            self.model = replacement
            self.model_id = settings.model_id
            self.universe = dict(settings.universe)
            self.history.feed = DataFeed(settings.feed)
            self.signal_date = None
        self.limits = PortfolioLimits(
            maximum_total_exposure=settings.maximum_total_exposure,
            maximum_sector_exposure=settings.maximum_sector_exposure,
            maximum_position=settings.maximum_position, top_n=settings.top_n)

    def refresh(self, now):
        today = now.astimezone(NY).date()
        if self.signal_date == today:
            return
        history = self.history.load(now, self.universe)
        self.model.prepare(history, self.universe)
        self.signal_date = today
        logging.info("Prepared strategy %s for %s", self.model_id, today)

    def candidates(self, weights, equity):
        return self.model.candidates(weights, equity, self.limits)
