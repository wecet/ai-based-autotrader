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
}


def append_trials(path, record):
    """Append one JSON line per invocation, including failures, to expose selection bias."""
    entry = {"recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
    with Path(path).open("a") as log:
        log.write(json.dumps(entry, allow_nan=False, sort_keys=True) + "\n")


def protocol_record(name):
    return {"experiment": name, **asdict(PROTOCOLS[name])}
