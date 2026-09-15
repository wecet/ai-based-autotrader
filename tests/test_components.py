"""Exercise the extracted boundaries independently of the full service."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from configuration.legacy import TraderSettings
from infrastructure.journal import OrderJournal
from trading.runner import Autotrader
from brokers.account import Ticker


def test_ticker_reuses_account_without_login():
    account = Mock()
    ticker = Ticker(" aapl ", account)
    assert ticker.get_info() is account.trading_client.get_asset.return_value
    account.login.assert_not_called()
    account.trading_client.get_asset.assert_called_once_with("AAPL")


def test_journal_reservation_survives_new_instance(tmp_path):
    path = tmp_path / "orders.json"
    journal = OrderJournal(path)
    state = journal.read("2026-09-14")
    order_id = journal.reserve(state, "AAA:buy:0.10000000")
    assert order_id.startswith("autotrader-2026-09-14-")
    recovered = OrderJournal(path).read("2026-09-14")
    assert recovered == state
    with pytest.raises(ValueError, match="already attempted"):
        journal.reserve(recovered, "AAA:buy:0.10000000")


def test_journal_rejects_eleventh_attempt(tmp_path):
    journal = OrderJournal(tmp_path / "orders.json")
    state = journal.read("2026-09-14")
    for index in range(10):
        journal.reserve(state, f"{index}:buy:0.1")
    with pytest.raises(ValueError, match="Daily limit"):
        journal.reserve(state, "extra:buy:0.1")
    assert len(journal.read("2026-09-14")["attempts"]) == 10


def test_coordinator_accepts_independent_components(tmp_path):
    from datetime import datetime
    from data.historical import NY
    client = Mock()
    client.get_clock.return_value = SimpleNamespace(
        is_open=True, timestamp=datetime(2026, 9, 14, 10, tzinfo=NY))
    client.get_orders.return_value = []
    client.get_account.return_value = SimpleNamespace(
        trading_blocked=False, equity="1000", buying_power="500")
    client.get_all_positions.return_value = [SimpleNamespace(symbol="AAA", market_value="100")]
    signals, executor = Mock(), Mock()
    journal = OrderJournal(tmp_path / "orders.json")
    runner = Autotrader(SimpleNamespace(trading_client=client), None,
                       {"AAA": "XLK", "BBB": "XLF"}, tmp_path,
                       signals=signals, journal=journal, executor=executor)
    runner.run_once()
    signals.refresh.assert_called_once_with(client.get_clock.return_value.timestamp)
    signals.candidates.assert_called_once_with({"AAA": .1}, 1000)
    executor.submit_next.assert_called_once_with(
        signals.candidates.return_value, signals.model,
        {"day": "2026-09-14", "attempts": []}, 500)
    client.submit_order.assert_not_called()


@pytest.mark.parametrize("universe", ['[]', '{"AAA":1}', '{"AAA":"XLK","BBB":""}',
                                      '{"AAA":"XLK"," aaa ":"XLF"}'])
def test_settings_reject_invalid_universe(monkeypatch, universe):
    monkeypatch.setenv("TRADER_UNIVERSE", universe)
    with pytest.raises(ValueError):
        TraderSettings.from_environment()
