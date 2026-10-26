from dataclasses import replace
from datetime import date, timedelta
import json

import pytest

from configuration.settings import TradingSettings
from core.contracts import Model
from core.types import DailyBar, MarketHistory, TradeCandidate
from research.backtest import BacktestConfig, run_backtest
from research.datasets import load_dataset, synthetic_dataset


def bar(index, opening=100, close=100, volume=100000):
    return DailyBar(date(2024, 1, 1) + timedelta(days=index), opening,
                    max(opening, close) + 1, min(opening, close) - 1, close, volume)


def market(stock=None):
    stock = stock or [bar(0, close=90), bar(1, close=110), bar(2, opening=120, close=120)]
    spy = [bar(i) for i in range(3)]
    return MarketHistory({'AAA': stock}, spy, {'GROUP': spy})


class RoundTrip(Model):
    def prepare(self, history, universe):
        self.length = len(history.spy)
        self.latest = history.spy[-1].date
    def candidates(self, weights, equity, limits):
        target = limits.maximum_position if self.length < 2 else 0
        return [TradeCandidate('AAA', .1, 1, 100, 'GROUP', target, weights.get('AAA', 0), equity)]
    def decide_to_buy(self, candidate):
        return candidate.desired_weight > candidate.current_weight
    def decide_to_sell(self, candidate):
        return candidate.desired_weight < candidate.current_weight


@pytest.fixture
def settings():
    return TradingSettings(universe={'AAA': 'GROUP', 'BBB': 'GROUP'},
                           maximum_total_exposure=.5, maximum_sector_exposure=.5, maximum_position=.5)


@pytest.fixture
def config():
    return BacktestConfig(initial_cash=1000, warmup_sessions=1, spread_bps=0, slippage_bps=0)


def test_hand_checkable_round_trip_and_next_open_timing(settings, config):
    report = run_backtest(RoundTrip, market(), settings, config)
    assert [(f['side'], f['price']) for f in report['fills']] == [('buy', 100), ('sell', 120)]
    assert report['fills'][0]['quantity'] == 5
    assert [s['equity'] for s in report['snapshots']] == [1050, 1100]
    assert report['snapshots'][-1]['cash'] == 1100
    assert report['metrics']['net_return'] == pytest.approx(.1)
    assert report['metrics']['turnover_over_initial_equity'] == pytest.approx(1.1)
    assert report['metrics']['maximum_drawdown'] == 0


def test_future_bars_cannot_change_earlier_decisions(settings, config):
    original = market()
    changed = market([*original.stocks['AAA'][:2], bar(2, opening=1000, close=1000)])
    first = run_backtest(RoundTrip, original, settings, config)
    second = run_backtest(RoundTrip, changed, settings, config)
    assert first['fills'][0] == second['fills'][0]
    assert first['snapshots'][0] == second['snapshots'][0]


def test_fees_and_costs_reconcile_cash_and_reduce_returns(settings, config):
    base = run_backtest(RoundTrip, market(), settings, config)
    costly = run_backtest(RoundTrip, market(), settings, replace(config, spread_bps=10, fee_bps=10))
    expected = config.initial_cash
    for fill in costly['fills']:
        expected += (-fill['notional'] if fill['side'] == 'buy' else fill['notional']) - fill['fee']
    assert costly['snapshots'][-1]['cash'] == pytest.approx(expected)
    assert costly['metrics']['net_return'] < base['metrics']['net_return']
    assert costly['metrics']['fees'] > 0
    assert costly['metrics']['execution_impact'] > 0


def test_partial_fills_use_prior_volume_not_future_volume(settings, config):
    history = market([bar(0, volume=100), bar(1, volume=10000000), bar(2)])
    report = run_backtest(RoundTrip, history, settings, config)
    assert report['fills'][0]['quantity'] == 1
    assert report['fills'][0]['partial']


def test_missing_execution_bar_does_not_fill_or_mark_to_zero(settings, config):
    history = market([bar(0), bar(1, close=110)])
    report = run_backtest(RoundTrip, history, settings, config)
    assert len(report['fills']) == 1
    assert report['snapshots'][-1]['equity'] == 1050
    assert report['snapshots'][-1]['stale_marks'] == ['AAA']
    assert any(e['reason'] == 'missing_execution_bar' for e in report['events'])


def test_rejections_consume_attempt_and_do_not_change_positions(settings, config):
    report = run_backtest(RoundTrip, market(), settings, config, order_allowed=lambda candidate, day: False)
    assert report['fills'] == []
    assert report['snapshots'][-1]['cash'] == 1000
    assert any(e['reason'] == 'rejected_order' for e in report['events'])


def test_order_caps_and_disabled_trading(settings, config):
    limited = settings.model_copy(update={'maximum_order_notional': 20.0})
    report = run_backtest(RoundTrip, market(), limited, config)
    assert all(f['notional'] <= 20 for f in report['fills'])
    for change in ({'max_daily_trades': 0}, {'trading_enabled': False}):
        report = run_backtest(RoundTrip, market(), settings.model_copy(update=change), config)
        assert not report['fills']


def test_delayed_execution_uses_older_snapshot(settings, config):
    report = run_backtest(RoundTrip, market(), settings, replace(config, delay_sessions=1))
    assert report['fills'][0]['price'] == 120
    assert report['fills'][0]['signal_cutoff'] == '2024-01-02'


def test_unavailable_preparation_is_reported_without_fabricating_trades(settings, config):
    class MissingHistory(RoundTrip):
        def prepare(self, history, universe):
            raise ValueError('Insufficient test history')
    report = run_backtest(MissingHistory, market(), settings, config)
    assert not report['fills']
    assert report['metrics']['net_return'] == 0
    assert report['events'][0]['reason'] == 'prepare_unavailable'


def test_dataset_round_trip_and_hash(tmp_path):
    dataset = synthetic_dataset()
    path = tmp_path / 'dataset.json'
    path.write_text(dataset.model_dump_json())
    loaded, digest = load_dataset(path)
    assert loaded == dataset
    assert len(digest) == 64
    path.write_text(dataset.model_dump_json(indent=2))
    assert load_dataset(path)[1] != digest


@pytest.mark.parametrize('change', [{'initial_cash': -1}, {'spread_bps': float('nan')},
                                    {'volume_participation': 0}, {'delay_sessions': -1}])
def test_invalid_research_configuration(change):
    with pytest.raises(ValueError):
        BacktestConfig(**change)


def test_bad_history_is_rejected(settings, config):
    history = market([bar(0), bar(1), bar(1)])
    with pytest.raises(ValueError, match='unique'):
        run_backtest(RoundTrip, history, settings, config)


def test_rejected_attempt_uses_daily_budget_before_next_symbol(settings, config):
    class TwoTargets(RoundTrip):
        def candidates(self, weights, equity, limits):
            return [TradeCandidate(s, .1, 1, 100, 'GROUP', .25, weights.get(s, 0), equity)
                    for s in ('AAA', 'BBB')]
    history = market()
    history.stocks['BBB'] = list(history.stocks['AAA'])
    attempts = []
    def reject(candidate, day):
        attempts.append((day, candidate.symbol))
        return False
    run_backtest(TwoTargets, history, settings.model_copy(update={'max_daily_trades': 1}),
                 config, order_allowed=reject)
    assert [symbol for _, symbol in attempts] == ['AAA', 'AAA']


def test_cli_keeps_reserved_final_sessions_out_of_development(tmp_path, monkeypatch):
    from research.__main__ import main
    import research.__main__ as cli
    dataset = synthetic_dataset(310)
    dataset_path = tmp_path / 'dataset.json'
    dataset_path.write_text(dataset.model_dump_json())
    observed = []
    original = cli.run_backtest
    def record(factory, history, settings, config):
        observed.append((history.spy[-1].date, config.warmup_sessions + config.delay_sessions))
        return {'metrics': {}}
    cli.run_backtest = record
    monkeypatch.setenv('RESEARCH_FINAL_TEST_SESSIONS', '20')
    try:
        assert main([str(dataset_path), str(tmp_path / 'development.json')]) == 0
        assert len(observed) == 9
        assert all(day < dataset.history.spy[-20].date for day, _ in observed)
        assert len({start for _, start in observed}) == 1
        observed.clear()
        monkeypatch.setenv('RESEARCH_FINAL_TEST', 'true')
        assert main([str(dataset_path), str(tmp_path / 'final.json')]) == 0
        assert all(day == dataset.history.spy[-1].date for day, _ in observed)
        assert all(start == 290 for _, start in observed)
    finally:
        cli.run_backtest = original


def test_cli_records_protocol_and_every_trial_including_failures(tmp_path, monkeypatch):
    from research.__main__ import main
    import research.__main__ as cli
    dataset = synthetic_dataset(310)
    dataset_path = tmp_path / 'dataset.json'
    dataset_path.write_text(dataset.model_dump_json())
    original = cli.run_backtest
    def record(factory, history, settings, config):
        factory()
        return {'metrics': {}}
    cli.run_backtest = record
    monkeypatch.setenv('RESEARCH_EXPERIMENT', 'allocation')
    try:
        assert main([str(dataset_path), str(tmp_path / 'allocation.json')]) == 0
        output = json.loads((tmp_path / 'allocation.json').read_text())
        assert output['protocol']['experiment'] == 'allocation'
        assert output['protocol']['rejection_criteria']
        assert set(output['results']) == {'momentum_rotation', 'boosted_ranker'}
        def fail(*args):
            raise ValueError('Invalid portfolio accounting')
        cli.run_backtest = fail
        monkeypatch.setenv('RESEARCH_EXPERIMENT', 'baselines')
        with pytest.raises(ValueError):
            main([str(dataset_path), str(tmp_path / 'failed.json')])
        monkeypatch.setenv('RESEARCH_EXPERIMENT', 'unknown')
        with pytest.raises(ValueError, match='RESEARCH_EXPERIMENT'):
            main([str(dataset_path), str(tmp_path / 'unknown.json')])
    finally:
        cli.run_backtest = original
    trials = [json.loads(line) for line in (tmp_path / 'research-trials.jsonl').read_text().splitlines()]
    assert [(t['experiment'], t['status']) for t in trials] == [('allocation', 'completed'), ('baselines', 'failed')]
    assert trials[1]['error'] == 'ValueError: Invalid portfolio accounting'
    assert all(t['dataset_sha256'] for t in trials)


def test_reports_tail_and_regime_sensitivity(settings):
    dataset = synthetic_dataset(310)
    config = BacktestConfig(initial_cash=1000, warmup_sessions=290, spread_bps=0, slippage_bps=0)
    report = run_backtest(RoundTrip, dataset.history, settings, config)
    assert report['metrics']['expected_shortfall_5pct'] <= 0
    assert set(report['metrics']['regimes']['trend']) <= {'uptrend', 'downtrend'}
    assert set(report['metrics']['regimes']['volatility']) <= {'high_volatility', 'low_volatility'}
    assert sum(r['sessions'] for r in report['metrics']['regimes']['trend'].values()) == len(report['snapshots'])


def test_reversion_experiment_compares_both_variants_with_baselines():
    from core.registry import ModelRegistry
    from research.__main__ import strategy_factories
    factories = strategy_factories(ModelRegistry(), 'reversion')
    assert list(factories) == ['momentum_rotation', 'boosted_ranker', 'residual_reversion',
                               'residual_reversion_filtered']


def test_meta_experiment_compares_against_unfiltered_momentum():
    from core.registry import ModelRegistry
    from research.__main__ import strategy_factories
    assert list(strategy_factories(ModelRegistry(), 'meta_momentum')) == ['momentum_rotation', 'boosted_ranker',
                                                                          'meta_momentum']


def test_regime_experiment_compares_against_static_strategies():
    from core.registry import ModelRegistry
    from research.__main__ import strategy_factories
    assert list(strategy_factories(ModelRegistry(), 'regime')) == ['momentum_rotation', 'boosted_ranker',
                                                                   'residual_reversion', 'regime_composite']
