"""Freeze raw daily bars and corporate actions from Alpaca into a research dataset (network required)."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys

from core.types import DailyBar, MarketHistory
from data.historical import NY
from research.datasets import Dataset
from research.events import CorporateAction, InstrumentNotes, MarketEvents, MembershipInterval

USAGE = "Usage: python -m research.build_dataset OUTPUT.json  (set RESEARCH_START=YYYY-MM-DD and TRADER_UNIVERSE)"
SPLIT_TYPES = ("forward_splits", "reverse_splits")


def corporate_actions(response_data, kinds):
    """Map provider splits and cash dividends; commodity funds' cash is labeled a distribution."""
    actions = []
    for category, items in sorted(response_data.items()):
        for item in items:
            if category in SPLIT_TYPES:
                value, kind = item.new_rate / item.old_rate, "split"
            elif category == "cash_dividends":
                value = item.rate
                kind = "distribution" if kinds.get(item.symbol) == "commodity_fund" else "dividend"
            else:
                continue
            actions.append(CorporateAction(symbol=item.symbol, kind=kind, ex_date=item.ex_date, value=value,
                                           pay_date=item.payable_date, processed_on=item.process_date))
    return sorted(actions, key=lambda a: (a.ex_date, a.symbol, a.kind))


def build_dataset(bars_client, actions_client, universe, start, end, *, feed, now,
                  membership=None, instrument_notes=None):
    """Request raw bars and actions for [start, end); sessions on or after ``now``'s date are excluded."""
    from alpaca.data.enums import Adjustment, CorporateActionsType, DataFeed
    from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    symbols = sorted(set(universe) | set(universe.values()) | {"SPY"})
    today = now.astimezone(NY).date()
    response = bars_client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame.Day, adjustment=Adjustment.RAW, feed=DataFeed(feed),
        start=datetime.datetime.combine(start, datetime.time(), NY),
        end=datetime.datetime.combine(end, datetime.time(), NY)))
    series = {symbol: sorted((DailyBar(b.timestamp.astimezone(NY).date(), b.open, b.high, b.low, b.close, b.volume)
                              for b in response.data.get(symbol, []) if b.timestamp.astimezone(NY).date() < today),
                             key=lambda bar: bar.date)
              for symbol in symbols}
    spy = series["SPY"]
    if not spy:
        raise ValueError("Provider returned no SPY sessions")
    notes = {symbol: InstrumentNotes(**values) for symbol, values in (instrument_notes or {}).items()}
    for symbol, bars in series.items():
        if not bars:
            continue
        inferred = {"available_from": bars[0].date}
        later = [b.date for b in spy if b.date > bars[-1].date]
        if later and (spy[-1].date - bars[-1].date).days > 5:
            inferred.update(delisted_on=later[0], delisting_inferred=True)
        explicit = notes[symbol].model_dump(exclude_unset=True) if symbol in notes else {}
        notes[symbol] = InstrumentNotes(**{**inferred, **explicit})
    actions = actions_client.get_corporate_actions(CorporateActionsRequest(
        symbols=symbols, start=start, end=end, limit=None,
        types=[CorporateActionsType.FORWARD_SPLIT, CorporateActionsType.REVERSE_SPLIT,
               CorporateActionsType.CASH_DIVIDEND]))
    kinds = {symbol: note.kind for symbol, note in notes.items()}
    events = MarketEvents(corporate_actions=corporate_actions(actions.data, kinds),
                          membership={s: [MembershipInterval(**i) for i in intervals]
                                      for s, intervals in (membership or {}).items()},
                          instruments=notes)
    stocks = {symbol: series[symbol] for symbol in universe if series[symbol]}
    sectors = {group: series[group] for group in set(universe.values()) if series[group]}
    return Dataset(
        universe=universe, history=MarketHistory(stocks, spy, sectors),
        source=f"Alpaca {feed} raw daily bars and corporate actions, {start.isoformat()} to {end.isoformat()}",
        selection_notes="Universe supplied by TRADER_UNIVERSE. Membership intervals come only from "
                        "RESEARCH_MEMBERSHIP_FILE; without them membership is static. Delistings are inferred "
                        "when bars stop more than five days before the last SPY session.",
        price_basis="raw", events=events, ingested_at=now,
        information_notes="Bars are timestamped by session; actions are usable from their ex-date. The provider "
                          "supplies process dates, not announcement times.")


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--help"]:
        print(USAGE)
        return 0
    if len(args) != 1 or not os.getenv("RESEARCH_START") or not os.getenv("TRADER_UNIVERSE"):
        print(USAGE, file=sys.stderr)
        return 2
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from configuration.settings import TradingSettings
    settings = TradingSettings.from_environment()
    now = datetime.datetime.now(datetime.timezone.utc)
    start = datetime.date.fromisoformat(os.environ["RESEARCH_START"])
    end = datetime.date.fromisoformat(os.getenv("RESEARCH_END", now.astimezone(NY).date().isoformat()))

    def optional_json(name):
        path = os.getenv(name)
        return json.loads(Path(path).read_text()) if path else None
    key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
    dataset = build_dataset(StockHistoricalDataClient(key, secret), CorporateActionsClient(key, secret),
                            settings.universe, start, end, feed=settings.feed, now=now,
                            membership=optional_json("RESEARCH_MEMBERSHIP_FILE"),
                            instrument_notes=optional_json("RESEARCH_INSTRUMENT_FILE"))
    content = dataset.model_dump_json().encode()
    Path(args[0]).write_bytes(content)
    print(f"Wrote {args[0]} sha256={hashlib.sha256(content).hexdigest()} "
          f"({len(dataset.history.spy)} sessions, {len(dataset.events.corporate_actions)} actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
