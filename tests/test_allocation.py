import pytest

from core.types import PortfolioLimits
from trading.allocation import TargetSignal, allocate_targets


def test_allocation_reserves_retained_group_and_total_exposure():
    limits = PortfolioLimits(.5, .2, .1, 5)
    signals = [TargetSignal('AAA', 100, 'TECH', 1), TargetSignal('BBB', 100, 'OTHER', .5)]
    candidates = allocate_targets(signals, {'STALE': .2, 'UNKNOWN': .15}, 1000, limits,
                                  {'STALE': 'TECH', 'AAA': 'TECH', 'BBB': 'OTHER'})
    targets = {c.symbol: c.desired_weight for c in candidates}
    assert targets == pytest.approx({'AAA': 0, 'BBB': .1})
    assert sum(targets.values()) + .35 <= limits.maximum_total_exposure
    assert 'STALE' not in targets


def test_allocation_returns_exits_and_distinguishes_scores_from_forecasts():
    signal = TargetSignal('EXIT', 100, 'TECH', -.2, allocation_weight=0)
    candidates = allocate_targets([signal], {'EXIT': .1}, 1000, PortfolioLimits(), {'EXIT': 'TECH'})
    assert candidates[0].desired_weight == 0
    assert candidates[0].predicted_return is None
    assert candidates[0].desired_weight < candidates[0].current_weight


@pytest.mark.parametrize('weight', [-1, float('nan')])
def test_allocation_rejects_invalid_long_only_holdings(weight):
    with pytest.raises(ValueError):
        allocate_targets([], {'AAA': weight}, 1000, PortfolioLimits(), {})
