"""Shared Trader dispatch with broker eligibility, notional bounds and journal guards."""
import logging
from decimal import Decimal, ROUND_DOWN
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from brokers.account import Ticker
from core.contracts import Trader


class OrderExecutor(Trader):
    def __init__(self, account, journal):
        super().__init__()
        self.account = account
        self.client = account.trading_client
        self.journal = journal
        self.minimum_order_notional = 10.0
        self.maximum_order_notional = None
        self._state = None
        self._buying_power = 0.0
        self._submitted = False

    def configure(self, settings):
        self.minimum_order_notional = settings.minimum_order_notional
        self.maximum_order_notional = settings.maximum_order_notional

    def buy(self, context):
        self._submit(context, OrderSide.BUY)

    def sell(self, context):
        self._submit(context, OrderSide.SELL)

    def _submit(self, candidate, side):
        if self._state is None:
            raise RuntimeError("Use submit_next() to supply the journal and account context")
        fingerprint = f"{candidate.symbol}:{side.value}:{candidate.desired_weight:.8f}"
        if fingerprint in self._state["attempts"]:
            return
        asset = Ticker(candidate.symbol, self.account).get_info()
        if not (asset.tradable and asset.fractionable):
            return
        amount = candidate.order_notional
        if self.maximum_order_notional is not None:
            amount = min(amount, self.maximum_order_notional)
        notional = float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        if notional < self.minimum_order_notional:
            return
        if side == OrderSide.BUY and notional > self._buying_power:
            return
        # The strategy is intentionally absent from this fingerprint: switching
        # strategies must not reset deduplication or consume a fresh daily budget.
        order_id = self.journal.reserve(self._state, fingerprint)
        self.client.submit_order(order_data=MarketOrderRequest(
            symbol=candidate.symbol, notional=notional, side=side,
            time_in_force=TimeInForce.DAY, client_order_id=order_id))
        self._submitted = True
        logging.info("Submitted paper %s %s $%.2f (%s/%s)", side.value,
                     candidate.symbol, notional, len(self._state["attempts"]), self.journal.limit)

    def submit_next(self, candidates, model, state, buying_power):
        self.model = model
        self._state, self._buying_power, self._submitted = state, buying_power, False
        try:
            for candidate in sorted(candidates, key=lambda c: (c.desired_weight >= c.current_weight, c.symbol)):
                # Trader.trade evaluates buy AND sell exactly once, rejecting conflict.
                self.trade(candidate)
                if self._submitted:
                    return
            logging.info("No new model-approved trade")
        finally:
            self._state = None
            self._buying_power = 0.0
