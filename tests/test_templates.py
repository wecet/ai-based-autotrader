from unittest.mock import Mock

import pytest
from core.contracts import Model, Trader


class RecordingTrader(Trader):
    def buy(self, context):
        self.executed.append(("buy", context))

    def sell(self, context):
        self.executed.append(("sell", context))


@pytest.mark.parametrize("buy,sell,action", [(True, False, "buy"), (False, True, "sell"),
                                           (False, False, "hold"), (True, True, None)])
def test_trade_dispatch(buy, sell, action):
    model = Mock(spec=Model)
    model.decide_to_buy.return_value = buy
    model.decide_to_sell.return_value = sell
    trader = RecordingTrader(model)
    trader.executed = []
    context = object()
    if action is None:
        with pytest.raises(ValueError, match="same time"):
            trader.trade(context)
    else:
        assert trader.trade(context) == action
    assert trader.executed == ([(action, context)] if action in ("buy", "sell") else [])
    model.decide_to_buy.assert_called_once_with(context)
    model.decide_to_sell.assert_called_once_with(context)
