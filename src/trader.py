"""Daily cross-sectional signals evaluated throughout the open paper session."""
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from runtime import write_json
from utils import Account


NY = ZoneInfo("America/New_York")


def load_model():
    name = "cross_sectional"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "models/cross-sectional.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class Autotrader:
    def __init__(self, account, data_client, sectors, state_dir, *, feed=DataFeed.IEX):
        if len(sectors) < 2 or any(not key or not value for key, value in sectors.items()):
            raise ValueError("TRADER_UNIVERSE must map at least two stock symbols to sector ETF symbols")
        self.account = account
        self.client = account.trading_client
        self.data_client = data_client
        self.sectors = sectors
        self.state_path = state_dir / "orders.json"
        self.feed = feed
        self.cs = load_model()
        self.model = self.cs.CrossSectionalModel()
        self.signal_date = None
        self.ranked = []

    @classmethod
    def from_environment(cls, state_dir):
        sectors = json.loads(os.environ.get("TRADER_UNIVERSE", "{}"))
        if not isinstance(sectors, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                   for k, v in sectors.items()):
            raise ValueError("TRADER_UNIVERSE must be a JSON object of stock:sector-ETF pairs")
        sectors = {k.strip().upper(): v.strip().upper() for k, v in sectors.items()}
        if len(sectors) < 2:
            raise ValueError("Set TRADER_UNIVERSE to at least two stock:sector-ETF pairs")
        feed = DataFeed(os.getenv("TRADER_DATA_FEED", "iex"))
        account = Account().login()  # Account always selects Alpaca paper trading.
        return cls(account, StockHistoricalDataClient(account.api_key, account.secret_key),
                   sectors, state_dir, feed=feed)

    def refresh_signals(self, now):
        today = now.astimezone(NY).date()
        if self.signal_date == today:
            return
        end = datetime.combine(today, datetime.min.time(), NY)
        symbols = sorted(set(self.sectors) | set(self.sectors.values()) | {"SPY"})
        response = self.data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
            start=end - timedelta(days=730), end=end - timedelta(microseconds=1),
            adjustment=Adjustment.SPLIT, feed=self.feed))
        converted = {}
        for symbol in symbols:
            converted[symbol] = sorted([
                self.cs.DailyBar(b.timestamp.astimezone(NY).date(), b.open, b.high,
                                 b.low, b.close, b.volume)
                for b in response.data.get(symbol, []) if b.timestamp.astimezone(NY).date() < today
            ], key=lambda b: b.date)
        spy = converted["SPY"]
        if len(spy) < 206 or (today - spy[-1].date).days > 4:
            raise ValueError("Insufficient or stale SPY daily history")
        # The research model indexes histories positionally: require identical sessions.
        dates = [b.date for b in spy]
        stocks = {s: converted[s] for s in self.sectors
                  if [b.date for b in converted[s]] == dates
                  and [b.date for b in converted[self.sectors[s]]] == dates}
        if len(stocks) < 2:
            raise ValueError("Need at least two stocks with complete, aligned daily history")
        sectors = {s: converted[s] for s in set(self.sectors.values())}
        training = self.cs.build_ranked_training_set(stocks, spy, sectors, self.sectors)
        rows = self.cs.build_feature_rows(stocks, spy, sectors, self.sectors)
        if len(rows) < 2 or not training:
            raise ValueError("Insufficient liquid stocks or training rows")
        self.model.fit(training)
        self.ranked = self.model.rank(rows)
        self.signal_date = today
        logging.info("Refreshed daily model with %s rows; ranked %s stocks", len(training), len(rows))

    def read_state(self, day):
        if not self.state_path.exists():
            return {"day": day, "attempts": []}
        state = json.loads(self.state_path.read_text())
        if (not isinstance(state, dict) or not isinstance(state.get("day"), str)
                or not isinstance(state.get("attempts"), list)
                or not all(isinstance(item, str) for item in state["attempts"])):
            raise ValueError("Invalid order state; refusing to reset the daily limit")
        if state["day"] > day:
            raise ValueError("Order state is ahead of broker clock")
        return state if state["day"] == day else {"day": day, "attempts": []}

    def run_once(self):
        clock = self.client.get_clock()
        if not clock.is_open:
            logging.info("Market closed; waiting for next session")
            return
        day = clock.timestamp.astimezone(NY).date().isoformat()
        state = self.read_state(day)
        if len(state["attempts"]) >= 10:
            logging.info("Daily limit of 10 order attempts reached")
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
        self.refresh_signals(clock.timestamp)
        # Existing holdings missing from the eligible universe consume exposure too.
        held_outside = sum(w for s, w in weights.items() if s not in {r.symbol for r, _, _ in self.ranked})
        candidates = self.model.construct_portfolio(
            self.ranked, current_weights=weights, portfolio_value=equity,
            maximum_total_exposure=max(0, .5 - held_outside))
        for candidate in sorted(candidates, key=lambda c: (c.desired_weight >= c.current_weight, c.symbol)):
            side = (OrderSide.SELL if self.model.decide_to_sell(candidate)
                    else OrderSide.BUY if self.model.decide_to_buy(candidate) else None)
            if side is None:
                continue
            fingerprint = f"{candidate.symbol}:{side.value}:{candidate.desired_weight:.8f}"
            if fingerprint in state["attempts"]:
                continue
            asset = self.client.get_asset(candidate.symbol)
            if not (asset.tradable and asset.fractionable):
                continue
            notional = round(candidate.order_notional, 2)
            if side == OrderSide.BUY and notional > float(details.buying_power):
                continue
            # Persist BEFORE sending. An ambiguous timeout consumes a slot and is never
            # retried for this target today, even after the container restarts.
            state["attempts"].append(fingerprint)
            write_json(self.state_path, state)
            digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
            self.client.submit_order(order_data=MarketOrderRequest(
                symbol=candidate.symbol, notional=notional, side=side,
                time_in_force=TimeInForce.DAY, client_order_id=f"autotrader-{day}-{digest}"))
            logging.info("Submitted paper %s %s $%.2f (%s/10)", side.value,
                         candidate.symbol, notional, len(state["attempts"]))
            # Reconcile broker positions and orders next cycle before another submission.
            return
        logging.info("No new model-approved trade")
