"""Coordinate one trading cycle; lifecycle scheduling lives in runtime.py."""
import logging
import math
import os
from pathlib import Path
import time
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from config import TraderSettings
from live_settings import LiveSettingsStore
from runtime import write_json
from execution import OrderExecutor
from market_data import HistoricalData, NY
from order_journal import OrderJournal
from signals import DailySignals
from utils import Account


class Autotrader:
    def __init__(self, account, data_client, sectors, state_dir, *, feed=DataFeed.IEX,
                 signals=None, journal=None, executor=None, settings=None,
                 settings_store=None):
        if len(sectors) < 2 or any(not key or not value for key, value in sectors.items()):
            raise ValueError("TRADER_UNIVERSE must map at least two stock symbols to sector ETF symbols")
        self.settings_store = settings_store
        self.settings_revision = None
        self.state_dir = Path(state_dir)
        self.trading_enabled = True
        self.account = account
        self.client = account.trading_client
        self.signals = signals if signals is not None else DailySignals(
            HistoricalData(data_client, feed=feed), sectors)
        self.journal = journal if journal is not None else OrderJournal(state_dir / "orders.json")
        self.executor = executor if executor is not None else OrderExecutor(account, self.journal)
        if settings is not None:
            self.configure(settings)

    @classmethod
    def from_environment(cls, state_dir):
        store = LiveSettingsStore(Path(os.getenv("TRADER_CONTROL_DIR", str(state_dir / "control"))))
        settings = store.read().settings
        account = Account().login()
        return cls(account, StockHistoricalDataClient(account.api_key, account.secret_key),
                   settings.universe, state_dir, feed=DataFeed(settings.feed),
                   settings=settings, settings_store=store)

    def configure(self, settings):
        self.signals.configure(settings)
        self.executor.configure(settings)
        self.journal.limit = settings.max_daily_trades
        self.trading_enabled = settings.trading_enabled

    def reload_settings(self):
        if self.settings_store is None:
            return
        current = self.settings_store.read()
        if current.revision != self.settings_revision:
            self.configure(current.settings)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            write_json(self.state_dir / "applied-settings.json", {
                "revision": current.revision, "time": time.time(),
                "trading_enabled": self.trading_enabled,
            })
            self.settings_revision = current.revision
            logging.info("Applied trading settings %s", current.revision)

    def run_once(self):
        self.reload_settings()
        if not self.trading_enabled:
            logging.info("Trading paused by dashboard settings")
            return
        clock = self.client.get_clock()
        if not clock.is_open:
            logging.info("Market closed; waiting for next session")
            return
        day = clock.timestamp.astimezone(NY).date().isoformat()
        state = self.journal.read(day)
        if len(state["attempts"]) >= self.journal.limit:
            logging.info("Daily limit of %s order attempts reached", self.journal.limit)
            return
        if self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)):
            logging.info("Waiting for open orders to settle")
            return
        details = self.client.get_account()
        if details.trading_blocked:
            raise RuntimeError("Account trading is blocked")
        equity = float(details.equity)
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("Account equity must be finite and positive")
        positions = self.client.get_all_positions()
        weights = {p.symbol: float(p.market_value) / equity for p in positions}
        if any(not math.isfinite(w) or w < 0 for w in weights.values()):
            raise ValueError("This strategy requires finite, long-only positions")
        self.signals.refresh(clock.timestamp)
        candidates = self.signals.candidates(weights, equity)
        self.executor.submit_next(candidates, self.signals.model, state, float(details.buying_power))
