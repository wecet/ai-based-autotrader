"""Pre-registered experiment protocols and an append-only trial log."""
from dataclasses import asdict, dataclass
import datetime
import json
from pathlib import Path


@dataclass(frozen=True)
class ResearchProtocol:
    """Decisions fixed before tuning; reports carry them unchanged."""

    hypothesis: str
    universe: str
    information_cutoff: str
    horizon: str
    benchmark: str
    rejection_criteria: tuple[str, ...]


COMMON_UNIVERSE = "Dataset universe mapping; static membership unless the dataset records effective dates."
COMMON_CUTOFF = "Completed daily bars strictly before each preparation date; orders use a later session's open."

PROTOCOLS = {
    "baselines": ResearchProtocol(
        hypothesis="Momentum and boosted ranking outperform the ridge baseline after costs.",
        universe=COMMON_UNIVERSE, information_cutoff=COMMON_CUTOFF,
        horizon="Daily rebalancing; five-session labels for ranking models, 12-1 month momentum.",
        benchmark="SPY price return and the cross_sectional ridge strategy.",
        rejection_criteria=("Net return below the ridge baseline after double costs.",
                            "Maximum drawdown worse than SPY over the same sessions.")),
    "allocation": ResearchProtocol(
        hypothesis="Allocation changes net results independently of the predictive signal.",
        universe=COMMON_UNIVERSE, information_cutoff=COMMON_CUTOFF,
        horizon="Identical momentum and boosted-ranking signals; 60-session trailing return window for risk.",
        benchmark="Each signal's shipped allocation: inverse volatility for momentum, capped for boosted ranking.",
        rejection_criteria=("No method improves net return and drawdown together versus the shipped allocation.",
                            "Any improvement disappears under double costs.",
                            "Weights are unstable: mean rebalance weight change exceeds the shipped method's.")),
    "reversion": ResearchProtocol(
        hypothesis="Unusually weak five-session residual moves versus SPY and the group benchmark recover "
                   "over the following sessions, net of costs, without a short leg.",
        universe=COMMON_UNIVERSE, information_cutoff=COMMON_CUTOFF,
        horizon="Entry at a standardized residual of -2 or lower; exit at -0.5, after ten sessions, or on "
                "missing data. The breakdown filter labels five-session outcomes against a 0.2% round trip.",
        benchmark="SPY price return, momentum rotation and boosted ranking on the same sessions.",
        rejection_criteria=("Net return after double costs does not exceed momentum or boosted ranking.",
                            "Gains depend on the one-session-delay-free case only.",
                            "The filtered variant does not improve net return or drawdown over the "
                            "unfiltered baseline after costs.")),
    "meta_momentum": ResearchProtocol(
        hypothesis="A classifier trained on past momentum-rule outcomes improves net momentum results by "
                   "rejecting poor new entries.",
        universe=COMMON_UNIVERSE, information_cutoff=COMMON_CUTOFF,
        horizon="Label every rule opportunity by its next-open to open-21-sessions-later return net of a 0.2% "
                "round trip; train only on outcomes completed by the cutoff.",
        benchmark="Unfiltered momentum rotation and boosted ranking on the same sessions.",
        rejection_criteria=("Accepted entries do not beat unfiltered momentum's mean net outcome.",
                            "Missed upside exceeds avoided losses.",
                            "Net return or drawdown is not better than unfiltered momentum after double costs.",
                            "Brier score is no better than predicting the base rate.")),
    "regime": ResearchProtocol(
        hypothesis="Causal market regimes improve selection between momentum, residual reversion and reduced "
                   "exposure compared with any static strategy.",
        universe=COMMON_UNIVERSE, information_cutoff=COMMON_CUTOFF,
        horizon="Daily regime probabilities forward-filtered from trailing SPY trend, volatility, breadth and "
                "correlation; three-state mixture refitted at each preparation on past rows only.",
        benchmark="Static momentum rotation, residual reversion and boosted ranking on the same sessions.",
        rejection_criteria=("Net return after double costs does not beat the best static strategy.",
                            "Drawdown is not lower than momentum rotation's.",
                            "Results reverse between switching margins 0 and 0.2.",
                            "Regimes switch on more than a quarter of sessions.")),
}


def append_trials(path, record):
    """Append one JSON line per invocation, including failures, to expose selection bias."""
    entry = {"recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
    with Path(path).open("a") as log:
        log.write(json.dumps(entry, allow_nan=False, sort_keys=True) + "\n")


def protocol_record(name):
    return {"experiment": name, **asdict(PROTOCOLS[name])}
