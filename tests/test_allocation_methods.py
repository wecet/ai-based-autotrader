from datetime import date, timedelta

import numpy as np
import pytest

from configuration.settings import TradingSettings
from core.types import DailyBar, MarketHistory, PortfolioLimits
from research.allocation import ReallocatedModel, compare_allocations, stability
from research.backtest import BacktestConfig, regime_sensitivity
from trading.allocation import (TargetSignal, allocate_targets, hierarchical_risk_weights,
                                inverse_volatility_weights, reallocate, trailing_returns)


def two_scale_returns():
    base = np.tile([.01, -.01], 30)
    noise = np.tile([.01, .01, -.01, -.01], 15)
    return np.column_stack([base, 2 * noise])


def test_inverse_volatility_is_hand_checkable():
    assert inverse_volatility_weights(two_scale_returns()) == pytest.approx([2 / 3, 1 / 3])


def test_hierarchical_risk_reduces_to_inverse_variance_for_two_uncorrelated_assets():
    returns = two_scale_returns()
    assert abs(np.corrcoef(returns, rowvar=False)[0, 1]) < .05
    assert hierarchical_risk_weights(returns) == pytest.approx([.8, .2], abs=.01)


def test_hierarchical_risk_treats_a_correlated_pair_as_one_cluster():
    generator = np.random.default_rng(3)
    common, independent = generator.normal(0, .01, 250), generator.normal(0, .01, 250)
    pair = common[:, None] + generator.normal(0, .001, (250, 2))
    weights = hierarchical_risk_weights(np.column_stack([pair, independent]))
    assert weights.sum() == pytest.approx(1)
    assert weights[2] == pytest.approx(.5, abs=.05)
    assert weights[0] == pytest.approx(weights[1], abs=.03)


def test_degenerate_volatility_is_rejected():
    with pytest.raises(ValueError):
        inverse_volatility_weights(np.zeros((10, 2)))
    assert trailing_returns([100.0] * 70) is None
    assert trailing_returns([100.0, 101.0]) is None


def signals():
    return [TargetSignal('AAA', 100, 'TECH', 3), TargetSignal('BBB', 100, 'TECH', 2),
            TargetSignal('CCC', 100, 'OTHER', 1), TargetSignal('OUT', 100, 'OTHER', 4, allocation_weight=0)]


def test_reallocation_keeps_selection_and_only_resizes():
    returns = dict(zip(['AAA', 'BBB'], two_scale_returns().T))
    resized, proportional = reallocate(signals(), returns, 'inverse_volatility', top_n=2)
    weights = {s.symbol: s.allocation_weight for s in resized}
    assert proportional
    assert weights == pytest.approx({'AAA': 2 / 3, 'BBB': 1 / 3, 'CCC': 0, 'OUT': 0})
    assert [(s.symbol, s.score, s.price) for s in resized] == [(s.symbol, s.score, s.price) for s in signals()]
    capped, proportional = reallocate(signals(), {}, 'capped', top_n=2)
    assert not proportional
    assert [s.allocation_weight for s in capped] == [1, 1, 1, 0]
    missing, _ = reallocate(signals(), {}, 'equal', top_n=2)
    assert all(s.allocation_weight == 0 for s in missing)
    with pytest.raises(ValueError):
        reallocate(signals(), {}, 'leveraged', top_n=2)


@pytest.mark.parametrize('method', ['capped', 'equal', 'inverse_volatility', 'hierarchical_risk'])
def test_every_method_respects_retained_holdings_and_limits(method):
    returns = dict(zip(['AAA', 'BBB', 'CCC'], np.column_stack([*two_scale_returns().T, np.tile([.02, -.02], 30)]).T))
    limits = PortfolioLimits(.6, .3, .25, 3)
    resized, proportional = reallocate(signals(), returns, method, limits.top_n)
    candidates = allocate_targets(resized, {'STALE': .2}, 1000, limits,
                                  {'STALE': 'TECH', 'AAA': 'TECH', 'BBB': 'TECH', 'CCC': 'OTHER'},
                                  proportional=proportional)
    targets = {c.symbol: c.desired_weight for c in candidates}
    assert sum(targets.values()) + .2 <= limits.maximum_total_exposure + 1e-12
    assert targets['AAA'] + targets['BBB'] + .2 <= limits.maximum_sector_exposure + 1e-12
    assert max(targets.values()) <= limits.maximum_position
    assert targets.get('OUT', 0) == 0


class FrozenSignals:
    def __init__(self):
        self.prepared = False

    def configure(self, settings):
        self.configured = settings

    def prepare(self, history, universe):
        self.prepared = True

    def target_signals(self):
        return signals()[:3]

    def decide_to_buy(self, context):
        return 'base-buy'

    def decide_to_sell(self, context):
        return 'base-sell'


def history(count=80):
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(count)]
    def series(pattern):
        closes = 100 * np.cumprod(1 + np.resize(pattern, count))
        return [DailyBar(d, c, c, c, c, 1e6) for d, c in zip(days, closes)]
    spy = series([.001])
    return MarketHistory({'AAA': series([.01, -.01]), 'BBB': series([.02, .02, -.02, -.02]),
                          'CCC': series([.03, -.03])}, spy, {'TECH': spy, 'OTHER': spy})


def test_reallocated_model_changes_only_sizes_and_records_diagnostics():
    limits = PortfolioLimits(.9, .9, .9, 3)
    targets = {}
    for method in ('equal', 'inverse_volatility'):
        diagnostics = []
        model = ReallocatedModel(FrozenSignals(), method, diagnostics)
        model.prepare(history(), {'AAA': 'TECH', 'BBB': 'TECH', 'CCC': 'OTHER'})
        candidates = model.candidates({}, 1000, limits)
        targets[method] = {c.symbol: round(c.desired_weight, 4) for c in candidates}
        assert model.decide_to_buy(candidates[0]) == 'base-buy'
        assert model.decide_to_sell(candidates[0]) == 'base-sell'
        assert diagnostics[0]['date'] == '2024-03-20'
        assert len(diagnostics[0]['correlations']) == 3
    assert targets['equal'] == {'AAA': .3, 'BBB': .3, 'CCC': .3}
    assert targets['inverse_volatility']['AAA'] > targets['inverse_volatility']['CCC']
    assert sum(targets['inverse_volatility'].values()) == pytest.approx(.9, abs=1e-3)


def test_stability_measures_weight_turnover_between_rebalances():
    entries = [{'date': '2024-01-02', 'targets': {'A': .5}, 'correlations': {('A', 'B'): .2},
                'largest_group_weight': .5},
               {'date': '2024-01-03', 'targets': {'B': .3}, 'correlations': {('A', 'B'): .5},
                'largest_group_weight': .3}]
    summary = stability(entries)
    assert summary['mean_target_weight_change'] == pytest.approx(.8)
    assert summary['mean_pairwise_correlation_change'] == pytest.approx(.3)
    assert summary['maximum_total_target'] == .5


def test_comparison_reports_failures_and_stress_costs():
    calls = []
    def runner(factory, history, settings, config):
        calls.append(config.spread_bps)
        if config.spread_bps > 5:
            raise ValueError('Invalid portfolio accounting')
        factory()
        return {'metrics': {'net_return': 0}}
    settings = TradingSettings(universe={'AAA': 'TECH', 'BBB': 'TECH'})
    results = compare_allocations({'frozen': FrozenSignals}, history(), settings, BacktestConfig(), runner=runner)
    assert set(results['frozen']) == {'capped', 'equal', 'inverse_volatility', 'hierarchical_risk'}
    assert results['frozen']['equal']['double_costs'] == {'failed': 'Invalid portfolio accounting'}
    assert results['frozen']['equal']['base_costs']['stability']['rebalances'] == 0
    assert calls.count(10) == 4


def test_regime_labels_use_prior_close_and_report_unknown_without_history():
    spy = history(3).spy
    snapshots = [{'date': spy[1].date.isoformat(), 'equity': 110}, {'date': spy[2].date.isoformat(), 'equity': 99}]
    regimes = regime_sensitivity(snapshots, 100, spy)
    unknown = {'unknown': {'sessions': 2, 'mean_session_return': pytest.approx(0),
                           'compounded_return': pytest.approx(-.01)}}
    assert regimes == {'trend': unknown, 'volatility': unknown}
