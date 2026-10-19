"""Smoke-check an installed distribution from outside the source checkout."""
from importlib.metadata import distribution
from pathlib import Path
import subprocess
import sys

from core.registry import ModelRegistry
from dashboard import app
from configuration.universe import candidate_catalog
from research.backtest import BacktestConfig

package = distribution("ai-trading-playground")
assert package.version
static = Path(app.__file__).parent / "static"
for filename in ("index.html", "style.css", "app.js", "favicon.svg", "favicon.png"):
    assert (static / filename).is_file(), filename
assert {"cross_sectional", "momentum_rotation", "boosted_ranker"} <= ModelRegistry().discover()[0].keys()
assert len(candidate_catalog()[1]) == 230
assert BacktestConfig().warmup_sessions == 260
entries = {entry.name: entry for entry in package.entry_points}
assert entries["ai-autotrader"].load()(["--help"]) == 0
assert callable(entries["ai-autotrader-dashboard"].load())
subprocess.run([sys.executable, "-m", "main", "--help"], check=True)
print("Installed package, strategy discovery, dashboard assets and entry points verified.")
