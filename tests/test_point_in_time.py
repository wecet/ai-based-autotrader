import datetime
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from configuration.settings import TradingSettings
from core.contracts import Model
from core.types import DailyBar, MarketHistory, TradeCandidate
from research.backtest import BacktestConfig, run_backtest
from research.build_dataset import build_dataset, corporate_actions, main as build_main
from research.datasets import Dataset, load_dataset
from research.events import (CorporateAction, InstrumentNotes, MarketEvents, MembershipInterval, adjust_bars,
                             research_history)

DAY = date(2024, 1, 1)


def bar(index, opening=100, close=100, volume=100_000):
    return DailyBar(DAY + timedelta(days=index), opening, max(opening, close), min(opening, close), close, volume)


def action(kind, index, value, **extra):
    return CorporateAction(symbol='AAA', kind=kind, ex_date=DAY + timedelta(days=index), value=value, **extra)


def test_split_and_dividend_back_adjustment_is_hand_checkable():
    bars = [bar(0, close=200), bar(1, close=200), bar(2, opening=100, close=100)]
    split = adjust_bars(bars, [action('split', 2, 2.0)])
    assert [b.close for b in split] == [100, 100, 100]
    assert split[0].volume == 200_000
    dividend = adjust_bars(bars, [action('dividend', 1, 2.0)])
    assert [b.close for b in dividend] == pytest.approx([198, 200, 100])
    with pytest.raises(ValueError):
        adjust_bars(bars, [action('dividend', 1, 500.0)])


def test_research_history_uses_only_actions_known_at_cutoff():
    history = MarketHistory({'AAA': [bar(0, close=200), bar(1, opening=100, close=100)]}, [bar(0), bar(1)], {})
    late = action('split', 1, 2.0, published_at=datetime.datetime(2024, 1, 5, tzinfo=datetime.timezone.utc))
    events = MarketEvents(corporate_actions=[late])
    assert research_history(history, events, DAY + timedelta(days=1)).stocks['AAA'][0].close == 200
    assert research_history(history, events, DAY + timedelta(days=4)).stocks['AAA'][0].close == 100
    assert research_history(history, events, DAY).stocks['AAA'][0].close == 200


class Hold(Model):
    seen = []

    def prepare(self, history, universe):
        Hold.seen.append((history.stocks.get('AAA', [None])[0], dict(universe)))

    def candidates(self, weights, equity, limits):
        return [TradeCandidate('AAA', .1, 1, 100, 'GROUP', .5, weights.get('AAA', 0), equity)]

    def decide_to_buy(self, candidate):
        return candidate.desired_weight > candidate.current_weight + .01

    def decide_to_sell(self, candidate):
        return False


@pytest.fixture
def settings():
    return TradingSettings(universe={'AAA': 'GROUP', 'BBB': 'GROUP'},
                           maximum_total_exposure=.5, maximum_sector_exposure=.5, maximum_position=.5)


@pytest.fixture
def config():
    return BacktestConfig(initial_cash=1000, warmup_sessions=1, spread_bps=0, slippage_bps=0)


def market(stock, length=None):
    length = length or len(stock)
    spy = [bar(i) for i in range(length)]
    return MarketHistory({'AAA': stock}, spy, {'GROUP': spy})


def test_split_keeps_equity_continuous_on_raw_prices(settings, config):
    history = market([bar(0), bar(1), bar(2, opening=50, close=50), bar(3, opening=50, close=50)])
    naive = run_backtest(Hold, history, settings, config)
    aware = run_backtest(Hold, history, settings, config,
                         market_events=MarketEvents(corporate_actions=[action('split', 2, 2.0)]))
    assert naive['snapshots'][-1]['equity'] == pytest.approx(750)
    assert [s['equity'] for s in aware['snapshots']] == pytest.approx([1000, 1000, 1000])
    assert aware['snapshots'][-1]['positions']['AAA'] == pytest.approx(10)
    assert any(e['reason'] == 'split_applied' for e in aware['events'])
    assert 'Ledger includes splits' in aware['limitations'][1]


def test_dividend_is_receivable_until_paid(settings, config):
    history = market([bar(0), bar(1), bar(2, opening=98, close=98), bar(3, opening=98, close=98)])
    dividend = action('dividend', 2, 2.0, pay_date=DAY + timedelta(days=3))
    report = run_backtest(Hold, history, settings, config, market_events=MarketEvents(corporate_actions=[dividend]))
    day2, day3 = report['snapshots'][1], report['snapshots'][2]
    assert day2['dividends_receivable'] == pytest.approx(10)
    assert day2['equity'] == pytest.approx(1000)
    assert day3['dividends_receivable'] == 0
    assert day3['cash'] == pytest.approx(510)
    assert report['metrics']['dividend_income'] == pytest.approx(10)


def test_delisting_settles_at_last_close(settings, config):
    history = market([bar(0), bar(1, close=80)], length=4)
    notes = {'AAA': InstrumentNotes(delisted_on=DAY + timedelta(days=2), delisting_inferred=True)}
    report = run_backtest(Hold, history, settings, config, market_events=MarketEvents(instruments=notes))
    settlement = [e for e in report['events'] if e['reason'] == 'delisting_settlement']
    assert settlement == [{'date': '2024-01-03', 'symbol': 'AAA', 'reason': 'delisting_settlement',
                           'proceeds': pytest.approx(400), 'inferred': True}]
    assert report['snapshots'][-1]['positions'] == {}
    assert report['snapshots'][-1]['equity'] == pytest.approx(900)


def test_models_see_point_in_time_membership_and_adjusted_prices(settings, config):
    Hold.seen = []
    history = market([bar(0, close=200), bar(1, opening=100, close=100), bar(2, opening=100, close=100)])
    history.stocks['BBB'] = list(history.stocks['AAA'])
    events = MarketEvents(corporate_actions=[action('split', 1, 2.0)],
                          membership={'BBB': [MembershipInterval(start=DAY + timedelta(days=2))]},
                          instruments={'AAA': InstrumentNotes(available_from=DAY)})
    run_backtest(Hold, history, settings, config, market_events=events)
    first, second = Hold.seen
    assert first[1] == {'AAA': 'GROUP'} and second[1] == {'AAA': 'GROUP', 'BBB': 'GROUP'}
    assert first[0].close == 200
    assert second[0].close == 100


def test_dataset_validation_rejects_double_adjustment_and_unknown_symbols(tmp_path):
    history = market([bar(0), bar(1)])
    base = dict(universe={'AAA': 'GROUP', 'BBB': 'GROUP'}, history=history, source='test', selection_notes='test')
    path = tmp_path / 'dataset.json'
    for events, basis, message in [
            (MarketEvents(corporate_actions=[action('split', 1, 2.0)]), 'split_adjusted', 'raw prices'),
            (MarketEvents(corporate_actions=[CorporateAction(symbol='ZZZ', kind='dividend', ex_date=DAY, value=1)]),
             'raw', 'unknown symbol'),
            (MarketEvents(corporate_actions=[action('dividend', 1, 1.0, pay_date=DAY)]), 'raw', 'Payment date')]:
        path.write_text(Dataset(**base, events=events, price_basis=basis).model_dump_json())
        with pytest.raises(ValueError, match=message):
            load_dataset(path)
    with pytest.raises(ValidationError):
        MembershipInterval(start=DAY, end=DAY - timedelta(days=1))
    with pytest.raises(ValidationError):
        CorporateAction(symbol='AAA', kind='split', ex_date=DAY, value=0)


def provider_bar(day, price, **extra):
    stamp = datetime.datetime.combine(day, datetime.time(5), datetime.timezone.utc)
    return SimpleNamespace(timestamp=stamp, open=price, high=price, low=price, close=price, volume=1_000_000)


class BarsClient:
    def __init__(self, data):
        self.data = data
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return SimpleNamespace(data=self.data)


class ActionsClient:
    def __init__(self, data):
        self.data = data
        self.requests = []

    def get_corporate_actions(self, request):
        self.requests.append(request)
        return SimpleNamespace(data=self.data)


def test_builder_freezes_raw_bars_actions_and_inferred_delistings():
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(12)]
    data = {'SPY': [provider_bar(d, 400) for d in days], 'GROUP': [provider_bar(d, 50) for d in days],
            'AAA': [provider_bar(d, 100) for d in days], 'GONE': [provider_bar(d, 20) for d in days[:3]],
            'GLD': [provider_bar(d, 180) for d in days]}
    split = SimpleNamespace(symbol='AAA', new_rate=4, old_rate=1, ex_date=days[5], payable_date=None,
                            process_date=days[5])
    reverse = SimpleNamespace(symbol='GONE', new_rate=1, old_rate=10, ex_date=days[1], payable_date=None,
                              process_date=days[1])
    cash = SimpleNamespace(symbol='GLD', rate=.5, ex_date=days[6], payable_date=days[8], process_date=days[6])
    actions = {'forward_splits': [split], 'reverse_splits': [reverse], 'cash_dividends': [cash],
               'spin_offs': [object()]}
    bars_client, actions_client = BarsClient(data), ActionsClient(actions)
    now = datetime.datetime.combine(days[-1], datetime.time(15), datetime.timezone.utc)
    dataset = build_dataset(bars_client, actions_client, {'AAA': 'GROUP', 'GONE': 'GROUP', 'GLD': 'GROUP'},
                            days[0], days[-1], feed='iex', now=now,
                            instrument_notes={'GLD': {'kind': 'commodity_fund', 'roll_exposure': 'none (physical)'}},
                            membership={'AAA': [{'start': '2024-01-01'}]})
    assert bars_client.requests[0].adjustment.value == 'raw'
    assert actions_client.requests[0].limit is None
    assert dataset.price_basis == 'raw' and dataset.ingested_at == now
    assert dataset.history.spy[-1].date == days[-2]
    kinds = [(a.symbol, a.kind, a.value) for a in dataset.events.corporate_actions]
    assert kinds == [('GONE', 'split', .1), ('AAA', 'split', 4), ('GLD', 'distribution', .5)]
    gone = dataset.events.instruments['GONE']
    assert gone.delisted_on == days[3] and gone.delisting_inferred
    assert dataset.events.instruments['GLD'].kind == 'commodity_fund'
    assert dataset.events.instruments['GLD'].available_from == days[0]
    assert dataset.events.member('AAA', days[0])
    assert corporate_actions({}, {}) == []


def test_builder_cli_requires_explicit_configuration(monkeypatch, capsys):
    monkeypatch.delenv('RESEARCH_START', raising=False)
    assert build_main(['out.json']) == 2
    assert build_main(['--help']) == 0
