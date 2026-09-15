"""Completed and aligned equity histories from Alpaca."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from core.types import DailyBar, MarketHistory

NY = ZoneInfo("America/New_York")


class HistoricalData:
    def __init__(self, data_client, *, feed=DataFeed.IEX):
        self.data_client = data_client
        self.feed = feed

    def load(self, now, universe):
        today = now.astimezone(NY).date()
        end = datetime.combine(today, datetime.min.time(), NY)
        symbols = sorted(set(universe) | set(universe.values()) | {"SPY"})
        response = self.data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
            start=end - timedelta(days=730), end=end - timedelta(microseconds=1),
            adjustment=Adjustment.SPLIT, feed=self.feed))
        converted = {}
        for symbol in symbols:
            converted[symbol] = sorted([
                DailyBar(b.timestamp.astimezone(NY).date(), b.open, b.high,
                                 b.low, b.close, b.volume)
                for b in response.data.get(symbol, []) if b.timestamp.astimezone(NY).date() < today
            ], key=lambda b: b.date)
        spy = converted["SPY"]
        if len(spy) < 206 or (today - spy[-1].date).days > 4:
            raise ValueError("Insufficient or stale SPY daily history")
        # The research model indexes histories positionally: require identical sessions.
        dates = [b.date for b in spy]
        stocks = {s: converted[s] for s in universe
                  if [b.date for b in converted[s]] == dates
                  and [b.date for b in converted[universe[s]]] == dates}
        if len(stocks) < 2:
            raise ValueError("Need at least two stocks with complete, aligned daily history")
        sectors = {s: converted[s] for s in set(universe.values())}
        return MarketHistory(stocks, spy, sectors)
