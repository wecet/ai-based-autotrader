from core.types import FeatureRow, TradeCandidate
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alpaca.trading.enums import OrderSide
from trading.runner import Autotrader, NY
from infrastructure.storage import write_json


@pytest.fixture
def service(tmp_path):
    client = Mock()
    client.get_clock.return_value = SimpleNamespace(is_open=True, timestamp=datetime(2026, 9, 14, 10, tzinfo=NY))
    client.get_orders.return_value = []
    client.get_account.return_value = SimpleNamespace(trading_blocked=False, equity="10000", buying_power="10000")
    client.get_all_positions.return_value = []
    client.get_asset.return_value = SimpleNamespace(tradable=True, fractionable=True)
    runner = Autotrader(SimpleNamespace(trading_client=client), Mock(), {"AAA": "XLK", "BBB": "XLF"}, tmp_path)
    runner.signals.refresh = Mock()
    row = FeatureRow(datetime(2026, 9, 11).date(), "AAA", "XLK", 100, 10_000_000, (0,) * 16)
    runner.signals.model.ranked = [(row, .02, 1)]
    return runner


def test_repeated_cycles_and_restart_do_not_duplicate(service):
    service.run_once()
    service.run_once()
    assert service.client.submit_order.call_count == 1
    request = service.client.submit_order.call_args.kwargs["order_data"]
    assert request.side == OrderSide.BUY
    assert request.notional == 1000
    restarted = Autotrader(service.account, service.signals.history.data_client, service.signals.universe, service.journal.state_path.parent)
    restarted.signals.refresh = Mock()
    restarted.signals.model.ranked = service.signals.model.ranked
    restarted.run_once()
    assert service.client.submit_order.call_count == 1


def test_limit_survives_restart_and_resets_next_day(service):
    write_json(service.journal.state_path, {"day": "2026-09-14", "attempts": [str(i) for i in range(9)]})
    service.run_once()
    assert len(service.journal.read("2026-09-14")["attempts"]) == 10
    service.run_once()
    assert service.client.submit_order.call_count == 1
    service.client.get_clock.return_value.timestamp = datetime(2026, 9, 15, 10, tzinfo=NY)
    service.run_once()
    assert service.client.submit_order.call_count == 2
    assert len(service.journal.read("2026-09-15")["attempts"]) == 1


def test_ambiguous_submission_is_not_retried(service):
    service.client.submit_order.side_effect = TimeoutError("unknown submission result")
    with pytest.raises(TimeoutError):
        service.run_once()
    service.run_once()
    assert service.client.submit_order.call_count == 1
    assert len(service.journal.read("2026-09-14")["attempts"]) == 1


@pytest.mark.parametrize("guard", ["closed", "pending", "blocked", "equity", "short"])
def test_broker_guards(service, guard):
    if guard == "closed":
        service.client.get_clock.return_value.is_open = False
    elif guard == "pending":
        service.client.get_orders.return_value = [object()]
    elif guard == "blocked":
        service.client.get_account.return_value.trading_blocked = True
    elif guard == "equity":
        service.client.get_account.return_value.equity = "nan"
    else:
        service.client.get_all_positions.return_value = [SimpleNamespace(symbol="AAA", market_value="-100")]
    if guard in ("blocked", "equity", "short"):
        with pytest.raises((RuntimeError, ValueError)):
            service.run_once()
    else:
        service.run_once()
    service.client.submit_order.assert_not_called()


def test_sell_before_buy(service):
    service.client.get_all_positions.return_value = [SimpleNamespace(symbol="AAA", market_value="2000")]
    service.run_once()
    assert service.client.submit_order.call_args.kwargs["order_data"].side == OrderSide.SELL


def test_corrupt_state_fails_closed(service):
    service.journal.state_path.write_text('{"unexpected":true}')
    with pytest.raises(ValueError):
        service.run_once()
    service.client.submit_order.assert_not_called()


def test_daily_data_pipeline(tmp_path):
    from datetime import timedelta
    runner = Autotrader(SimpleNamespace(trading_client=Mock()), Mock(), {"AAA": "XLK", "BBB": "XLF"}, tmp_path)
    today = datetime(2026, 9, 14, tzinfo=NY)
    data = {}
    for symbol, growth in [("AAA", .001), ("BBB", .002), ("XLK", .001), ("XLF", .001), ("SPY", .001)]:
        data[symbol] = [SimpleNamespace(timestamp=today - timedelta(days=211-i),
            open=100 * (1+growth)**i, high=101 * (1+growth)**i,
            low=99 * (1+growth)**i, close=100 * (1+growth)**i, volume=100000)
            for i in range(211)]
    runner.signals.history.data_client.get_stock_bars.return_value = SimpleNamespace(data=data)
    runner.signals.refresh(today)
    assert len(runner.signals.model.ranked) == 2
    assert runner.signals.model.return_calibration is not None
    request = runner.signals.history.data_client.get_stock_bars.call_args.args[0]
    assert request.end.replace(tzinfo=timezone.utc) < today
    runner.signals.refresh(today)
    assert runner.signals.history.data_client.get_stock_bars.call_count == 1


def test_missing_universe_never_logs_in(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADER_UNIVERSE", raising=False)
    with pytest.raises(ValueError, match="TRADER_UNIVERSE"):
        Autotrader.from_environment(tmp_path)


def test_never_submits_eleventh_order(service):
    service.signals.model.construct_portfolio = Mock(return_value=[
        TradeCandidate(str(i), .02, 1, 100, "tech", .1, portfolio_value=10000)
        for i in range(15)])
    for _ in range(20):
        service.run_once()
    assert service.client.submit_order.call_count == 10
    assert len(service.journal.read("2026-09-14")["attempts"]) == 10


def test_environment_factory_uses_paper_account(monkeypatch, tmp_path):
    import trading.runner as trader
    monkeypatch.setenv("TRADER_UNIVERSE", '{"aaa":"xlk","bbb":"xlf"}')
    account = Mock()
    account.login.return_value = account
    factory = Mock(return_value=account)
    data = Mock()
    original_account = trader.Account
    original_data_client = trader.StockHistoricalDataClient
    trader.Account = factory
    trader.StockHistoricalDataClient = data
    try:
        result = Autotrader.from_environment(tmp_path)
    finally:
        trader.Account = original_account
        trader.StockHistoricalDataClient = original_data_client
    account.login.assert_called_once()
    assert result.signals.universe == {"AAA": "XLK", "BBB": "XLF"}
    data.assert_called_once_with(account.api_key, account.secret_key)
