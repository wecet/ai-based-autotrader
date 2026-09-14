from unittest.mock import Mock

import pytest
import utils


@pytest.mark.parametrize("missing", ["APCA_API_KEY_ID", "APCA_API_SECRET_KEY"])
def test_account_requires_credentials(monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match="credentials were not found"):
        utils.Account()


def test_login_uses_paper_account(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(utils, "TradingClient", factory)
    account = utils.Account()
    assert account.trading_client is None
    assert account.login() is account
    factory.assert_called_once_with(api_key="test-key", secret_key="test-secret", paper=True)
    assert account.account_details is factory.return_value.get_account.return_value


def test_login_propagates_failure(monkeypatch):
    factory = Mock()
    factory.return_value.get_account.side_effect = RuntimeError("authentication failed")
    monkeypatch.setattr(utils, "TradingClient", factory)
    with pytest.raises(RuntimeError, match="authentication failed"):
        utils.Account().login()


@pytest.mark.parametrize("tradable", [True, False])
def test_ticker_normalizes_symbol_and_returns_asset(monkeypatch, tradable):
    client = Mock()
    client.get_asset.return_value.tradable = tradable
    monkeypatch.setattr(utils, "TradingClient", Mock(return_value=client))
    ticker = utils.Ticker(" aapl ")
    assert ticker.get_info() is client.get_asset.return_value
    client.get_asset.assert_called_once_with("AAPL")


def test_ticker_failure_and_disconnection(monkeypatch):
    client = Mock()
    monkeypatch.setattr(utils, "TradingClient", Mock(return_value=client))
    ticker = utils.Ticker("AAPL")
    client.get_asset.side_effect = ValueError("unknown symbol")
    with pytest.raises(ValueError, match="unknown symbol"):
        ticker.get_info()
    ticker.account.trading_client = None
    with pytest.raises(RuntimeError, match="not connected"):
        ticker.get_info()
