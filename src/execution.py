"""Model-approved order execution with broker eligibility and journal guards."""
import logging
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from utils import Ticker


class OrderExecutor:
    def __init__(self, account, journal):
        self.account = account
        self.client = account.trading_client
        self.journal = journal

    def submit_next(self, candidates, model, state, buying_power):
        for candidate in sorted(candidates, key=lambda c: (c.desired_weight >= c.current_weight, c.symbol)):
            side = (OrderSide.SELL if model.decide_to_sell(candidate)
                    else OrderSide.BUY if model.decide_to_buy(candidate) else None)
            if side is None:
                continue
            fingerprint = f"{candidate.symbol}:{side.value}:{candidate.desired_weight:.8f}"
            if fingerprint in state["attempts"]:
                continue
            asset = Ticker(candidate.symbol, self.account).get_info()
            if not (asset.tradable and asset.fractionable):
                continue
            notional = round(candidate.order_notional, 2)
            if side == OrderSide.BUY and notional > buying_power:
                continue
            # Persist BEFORE sending. An ambiguous timeout consumes a slot and is never
            # retried for this target today, even after the container restarts.
            order_id = self.journal.reserve(state, fingerprint)
            self.client.submit_order(order_data=MarketOrderRequest(
                symbol=candidate.symbol, notional=notional, side=side,
                time_in_force=TimeInForce.DAY, client_order_id=order_id))
            logging.info("Submitted paper %s %s $%.2f (%s/10)", side.value,
                         candidate.symbol, notional, len(state["attempts"]))
            # Reconcile broker positions and orders next cycle before another submission.
            return
        logging.info("No new model-approved trade")
