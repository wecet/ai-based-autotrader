"""Standalone paper adapter; the service uses the journal-backed OrderExecutor."""
from typing import Sequence
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
import core.contracts as tmp
from core.types import TradeCandidate
from brokers.account import Account


class PaperTrader(tmp.Trader):
    """Execute model-approved notional orders through an Alpaca paper account."""

    def __init__(self, model: tmp.Model, account: Account) -> None:
        super().__init__(model)
        if account.trading_client is None:
            raise RuntimeError("Account.login() must be called before creating the trader")
        self.account = account

    def _submit(self, context: TradeCandidate, side: OrderSide) -> None:
        client = self.account.trading_client
        if client is None:
            raise RuntimeError("the Alpaca trading client is not connected")
        request = MarketOrderRequest(
            symbol=context.symbol,
            notional=round(context.order_notional, 2),
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        client.submit_order(order_data=request)

    def buy(self, context: TradeCandidate) -> None:
        self._submit(context, OrderSide.BUY)

    def sell(self, context: TradeCandidate) -> None:
        self._submit(context, OrderSide.SELL)

    def rebalance(self, candidates: Sequence[TradeCandidate]) -> dict[str, tmp.TradeAction]:
        """Sell reductions first, then submit approved buys."""

        client = self.account.trading_client
        if client is None:
            raise RuntimeError("the Alpaca trading client is not connected")
        if not client.get_clock().is_open:
            raise RuntimeError("the market is closed; no rebalance orders were submitted")
        account_details = client.get_account()
        if account_details.trading_blocked:
            raise RuntimeError("Alpaca has blocked trading on this account")

        actions: dict[str, tmp.TradeAction] = {}
        sells = [candidate for candidate in candidates if candidate.desired_weight < candidate.current_weight]
        buys = [candidate for candidate in candidates if candidate.desired_weight >= candidate.current_weight]
        for candidate in (*sells, *buys):
            actions[candidate.symbol] = self.trade(candidate)
        return actions
