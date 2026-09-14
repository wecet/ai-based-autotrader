from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from alpaca.trading.enums import OrderSide, TimeInForce


@pytest.fixture
def bars(cs):
    def make(count=211, price=100.0, growth=0.001, volume=100_000):
        return [cs.DailyBar(date(2024, 1, 1) + timedelta(days=i),
                            price * (1 + growth) ** i,
                            price * (1 + growth) ** i,
                            price * (1 + growth) ** i,
                            price * (1 + growth) ** i, volume)
                for i in range(count)]
    return make


@pytest.fixture
def row(cs):
    return cs.FeatureRow(date(2024, 1, 1), "AAA", "tech", 100, 10_000_000,
                         (0.0,) * len(cs.FEATURE_NAMES))


@pytest.fixture
def candidate(cs):
    return cs.TradeCandidate("AAA", 0.02, 1.0, 100, "tech", 0.1,
                             portfolio_value=1000)


def test_features_known_growth(cs, bars):
    data = bars()
    values, liquidity = cs.calculate_features(data, data, data)
    features = dict(zip(cs.FEATURE_NAMES, values))
    assert len(values) == 16
    assert np.isfinite(values).all()
    for days in (1, 5, 20, 60):
        assert features[f"return_{days}d"] == pytest.approx(1.001 ** days - 1)
    assert features["rsi_14"] == 100
    assert features["volume_ratio_20d"] == 1
    assert features["sector_relative_momentum_20d"] == 0
    assert features["distance_from_high_60d"] == 0
    assert liquidity == pytest.approx(np.mean([b.close * b.volume for b in data[-20:]]))


@pytest.mark.parametrize("counts", [(200, 61, 21), (201, 60, 21), (201, 61, 20)])
def test_features_require_history(cs, bars, counts):
    with pytest.raises(ValueError, match="bars are required"):
        cs.calculate_features(*(bars(count=n) for n in counts))


def test_flat_features(cs, bars):
    data = bars(growth=0, volume=0)
    values, liquidity = cs.calculate_features(data, data, data)
    features = dict(zip(cs.FEATURE_NAMES, values))
    assert features["rsi_14"] == 50
    assert features["market_beta_60d"] == 0
    assert features["volume_ratio_20d"] == 0
    assert liquidity == 0
    assert np.isfinite(values).all()


def test_universe_filters_and_point_in_time(cs, bars):
    data = bars()
    stocks = {"OK": data, "CHEAP": bars(price=1), "ILLIQUID": bars(volume=1),
              "STALE": data[:-1], "SHORT": data[-100:], "UNKNOWN": data}
    sectors = {symbol: "tech" for symbol in stocks if symbol != "UNKNOWN"}
    rows = cs.build_feature_rows(stocks, data, {"tech": data}, sectors)
    assert [r.symbol for r in rows] == ["OK"]
    historical = cs.build_feature_rows({"OK": data}, data, {"tech": data}, sectors, as_of_index=200)
    truncated = cs.build_feature_rows({"OK": data[:201]}, data[:201], {"tech": data[:201]}, sectors)
    assert historical == truncated
    assert cs.build_feature_rows(stocks, [], {"tech": data}, sectors) == []
    assert cs.build_feature_rows(stocks, data, {"tech": data[:-1]}, sectors) == []


def test_training_labels_and_no_future_features(cs, bars):
    slow, fast = bars(growth=0.001), bars(growth=0.002)
    rows = cs.build_ranked_training_set({"SLOW": slow, "FAST": fast}, slow,
                                       {"tech": slow}, {"SLOW": "tech", "FAST": "tech"})
    assert len(rows) == 12
    for r in rows:
        growth = 0.001 if r.symbol == "SLOW" else 0.002
        assert r.future_return_5d == pytest.approx((1 + growth) ** 5 - 1)
        assert r.target_rank == (0 if r.symbol == "SLOW" else 1)
        assert r.date <= slow[-6].date
    altered = fast[:201] + [replace(b, close=b.close * 2) for b in fast[201:]]
    changed = cs.build_ranked_training_set({"FAST": altered}, slow, {"tech": slow}, {"FAST": "tech"})
    original = next(r for r in rows if r.symbol == "FAST")
    assert changed[0].values == original.values
    assert changed[0].future_return_5d != original.future_return_5d


def test_split_keeps_dates_together(cs, row):
    rows = [replace(row, date=row.date + timedelta(days=i), symbol=s)
            for i in range(20) for s in ("AAA", "BBB")]
    train, validation, test = cs.chronological_split(list(reversed(rows)))
    assert (len(train), len(validation), len(test)) == (28, 6, 6)
    assert max(r.date for r in train) < min(r.date for r in validation)
    assert max(r.date for r in validation) < min(r.date for r in test)
    assert cs.chronological_split([]) == ([], [], [])


@pytest.mark.parametrize("fractions", [(0, .1), (.7, 0), (.9, .1), (-1, .1)])
def test_split_rejects_invalid_fractions(cs, fractions):
    with pytest.raises(ValueError):
        cs.chronological_split([], *fractions)


def test_ridge_fits_linear_signal_with_constant_column(cs):
    x = np.column_stack((np.arange(10), np.ones(10)))
    model = cs.RidgeRanker(regularization=0.000001).fit(x, 2 * np.arange(10) + 3)
    np.testing.assert_allclose(model.predict([[10, 1], [11, 1]]), [23, 25], rtol=1e-6)


@pytest.mark.parametrize("x,y", [([], []), ([[1]], []), ([1], [1]), ([[1]], [[1]])])
def test_ridge_rejects_invalid_training(cs, x, y):
    with pytest.raises(ValueError):
        cs.RidgeRanker().fit(x, y)


def test_unfitted_models_and_empty_training(cs, row):
    with pytest.raises(ValueError):
        cs.RidgeRanker(-1)
    with pytest.raises(RuntimeError):
        cs.RidgeRanker().predict([[1]])
    model = cs.CrossSectionalModel()
    with pytest.raises(ValueError):
        model.fit([row])
    with pytest.raises(RuntimeError):
        model.rank([row])
    assert model.rank([]) == []


def test_model_fit_calibrates_returns_and_ranks(cs, row):
    rows = [replace(row, symbol=str(i), values=(float(i),),
                    target_rank=i / 2, future_return_5d=.01 * i) for i in range(3)]
    model = cs.CrossSectionalModel().fit(rows + [row])
    ranked = model.rank(rows)
    assert [r.symbol for r, _, _ in ranked] == ["2", "1", "0"]
    assert [p for _, _, p in ranked] == [1, .5, 0]
    np.testing.assert_allclose([v for _, v, _ in ranked], [.02, .01, 0], atol=1e-10)


@pytest.mark.parametrize("changes,buy,sell", [({}, True, False),
    ({"predicted_return": .002}, False, False),
    ({"estimated_spread": .02}, False, False),
    ({"estimated_slippage": .02}, False, False),
    ({"portfolio_value": 99}, False, False),
    ({"portfolio_value": 100}, True, False),
    ({"current_weight": .1}, False, False),
    ({"current_weight": .2}, False, True)])
def test_trade_decisions(cs, candidate, changes, buy, sell):
    model = cs.CrossSectionalModel()
    context = replace(candidate, **changes)
    assert model.decide_to_buy(context) is buy
    assert model.decide_to_sell(context) is sell


def test_portfolio_limits_and_exits(cs, row):
    ranked = [(replace(row, symbol=str(i), sector="tech" if i < 3 else "energy"), .02, 1-i/10)
              for i in range(6)]
    ranked.append((replace(row, symbol="EXIT"), -.01, 0))
    candidates = cs.CrossSectionalModel().construct_portfolio(
        ranked, current_weights={"EXIT": .1, "MISSING": .1}, portfolio_value=1000,
        maximum_total_exposure=.35)
    weights = {c.symbol: c.desired_weight for c in candidates}
    assert weights == pytest.approx({"0": .1, "1": .1, "3": .1, "4": .05, "EXIT": 0})
    assert sum(weights.values()) == pytest.approx(.35)
    limited = cs.CrossSectionalModel().construct_portfolio(
        ranked, current_weights={}, portfolio_value=1000, top_n=1)
    assert len(limited) == 1


def test_rebalance_sells_first_and_builds_orders(cs, candidate):
    client = Mock()
    client.get_clock.return_value.is_open = True
    client.get_account.return_value.trading_blocked = False
    trader = cs.CrossSectionalTrader(cs.CrossSectionalModel(), SimpleNamespace(trading_client=client))
    sell = replace(candidate, symbol="SELL", current_weight=.2, desired_weight=0)
    hold = replace(candidate, symbol="HOLD", desired_weight=0)
    assert trader.rebalance([candidate, hold, sell]) == {"SELL": "sell", "AAA": "buy", "HOLD": "hold"}
    orders = [call.kwargs["order_data"] for call in client.submit_order.call_args_list]
    assert [(o.symbol, o.side, o.notional) for o in orders] == [
        ("SELL", OrderSide.SELL, 200), ("AAA", OrderSide.BUY, 100)]
    assert all(o.time_in_force == TimeInForce.DAY for o in orders)


@pytest.mark.parametrize("is_open,blocked", [(False, False), (True, True)])
def test_rebalance_guards(cs, candidate, is_open, blocked):
    client = Mock()
    client.get_clock.return_value.is_open = is_open
    client.get_account.return_value.trading_blocked = blocked
    trader = cs.CrossSectionalTrader(cs.CrossSectionalModel(), SimpleNamespace(trading_client=client))
    with pytest.raises(RuntimeError):
        trader.rebalance([candidate])
    client.submit_order.assert_not_called()


def test_trader_requires_connection(cs, candidate):
    account = SimpleNamespace(trading_client=None)
    with pytest.raises(RuntimeError):
        cs.CrossSectionalTrader(cs.CrossSectionalModel(), account)
    account.trading_client = Mock()
    trader = cs.CrossSectionalTrader(cs.CrossSectionalModel(), account)
    account.trading_client = None
    with pytest.raises(RuntimeError):
        trader.rebalance([candidate])
    with pytest.raises(RuntimeError):
        trader.buy(candidate)
