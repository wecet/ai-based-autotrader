import pytest

from configuration.settings import TradingSettings
from core.registry import ModelRegistry
from core.types import PortfolioLimits, TradeCandidate
from models.ensemble import StaticEnsembleModel, combine_targets
from research.backtest import BacktestConfig
from research.datasets import synthetic_dataset
from research.shadow import attribute, shadow_comparison, shadow_divergence


def candidate(symbol, target, current=0.0, group='TECH'):
    return TradeCandidate(symbol, None, .5, 100, group, target, current, 10_000)


def test_combination_is_convex_retains_missing_views_and_trims_to_top_n():
    members = {'a': {'AAA': candidate('AAA', .1), 'BBB': candidate('BBB', .1)},
               'b': {'AAA': candidate('AAA', .1), 'CCC': candidate('CCC', .1, group='OTHER')}}
    targets, shares = combine_targets(members, {'a': .5, 'b': .5}, {'CCC': .04, 'BBB': .02}, top_n=2)
    assert targets == pytest.approx({'AAA': .1, 'CCC': .07, 'BBB': 0})
    assert shares['CCC'] == pytest.approx({'a': .02, 'b': .05})
    assert shares['BBB'] == {'a': 0, 'b': 0}
    assert combine_targets({}, {}, {}, 5) == ({}, {})


class Fixed:
    def __init__(self, targets=None, error=None):
        self.targets, self.error = targets or {}, error

    def configure(self, settings):
        self.configured = True

    def prepare(self, history, universe):
        if self.error:
            raise ValueError(self.error)

    def candidates(self, weights, equity, limits):
        return [candidate(s, t, weights.get(s, 0)) for s, t in self.targets.items()]


class Stubbed(StaticEnsembleModel):
    weights = {'a': .5, 'b': .5}

    def __init__(self, members, decision_log=None):
        self.decision_log = decision_log
        self.members = members
        self.prepared, self.abstentions, self.shadow_targets, self.universe = {}, {}, {}, {}


def test_abstaining_member_leaves_its_share_in_cash_and_shadows_are_kept():
    log = []
    model = Stubbed({'a': Fixed({'AAA': .1}), 'b': Fixed(error='no history')}, log)
    model.configure(TradingSettings(universe={'AAA': 'TECH', 'BBB': 'TECH'}))
    model.prepare(synthetic_dataset().history, {'AAA': 'TECH'})
    result = model.candidates({}, 10_000, PortfolioLimits())
    assert [(c.symbol, c.desired_weight, c.predicted_return) for c in result] == [('AAA', .05, None)]
    assert model.decide_to_buy(result[0]) and not model.decide_to_sell(result[0])
    assert model.shadow_targets == {'a': {'AAA': .1}}
    assert log[0]['abstentions'] == {'b': 'no history'}
    with pytest.raises(ValueError, match='Every ensemble member abstained'):
        Stubbed({'a': Fixed(error='x'), 'b': Fixed(error='y')}).prepare(synthetic_dataset().history, {})


def test_attribution_splits_holding_pnl_by_member_share():
    history = synthetic_dataset(5).history
    days = [b.date.isoformat() for b in history.spy]
    closes = [b.close for b in history.stocks['AAA']]
    decisions = [{'date': days[0], 'shares': {'AAA': {'a': .1, 'b': .05}}}]
    snapshots = [{'date': days[1], 'equity': 1000, 'positions': {'AAA': 10}},
                 {'date': days[2], 'equity': 1000 + 10 * (closes[2] - closes[1]) - 1, 'positions': {}}]
    report = attribute(decisions, snapshots, history, 1000)
    pnl = 10 * (closes[2] - closes[1])
    assert report['members'] == pytest.approx({'a': pnl * 2 / 3, 'b': pnl / 3})
    assert report['execution_and_timing'] == pytest.approx(-1)
    assert report['total'] == pytest.approx(pnl - 1)


def test_divergence_compares_each_member_with_the_traded_aggregate():
    decisions = [{'targets': {'AAA': .05}, 'shadow_targets': {'a': {'AAA': .1}, 'b': {'BBB': .1}},
                  'abstentions': {}},
                 {'targets': {'AAA': .05}, 'shadow_targets': {'a': {'AAA': .1}}, 'abstentions': {'b': 'x'}}]
    report = shadow_divergence(decisions)
    assert report['mean_target_divergence'] == pytest.approx({'a': .05, 'b': .15})
    assert report['abstaining_decisions'] == {'b': 1}


def test_shadow_comparison_runs_broker_free_on_synthetic_data():
    dataset = synthetic_dataset()
    settings = TradingSettings(universe=dataset.universe)
    report = shadow_comparison(dataset.history, settings, BacktestConfig(warmup_sessions=300))
    attribution = report['attribution']
    parts = sum(attribution['members'].values()) + attribution['unattributed'] + attribution['execution_and_timing']
    assert parts == pytest.approx(attribution['total'])
    assert set(report['mean_target_divergence']) <= {'momentum_rotation', 'boosted_ranker', 'residual_reversion'}


def test_ensemble_is_discoverable():
    assert ModelRegistry().require('ensemble').model_type is StaticEnsembleModel
