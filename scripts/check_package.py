"""Smoke-check an installed distribution from outside the source checkout."""
from importlib.metadata import distribution
from pathlib import Path
import subprocess
import sys

from core.registry import ModelRegistry
from dashboard import app

package = distribution("ai-trading-playground")
assert package.version
static = Path(app.__file__).parent / "static"
for filename in ("index.html", "style.css", "app.js", "favicon.svg", "favicon.png"):
    assert (static / filename).is_file(), filename
assert "cross_sectional" in ModelRegistry().discover()[0]
entries = {entry.name: entry for entry in package.entry_points}
assert entries["ai-autotrader"].load()(["--help"]) == 0
assert callable(entries["ai-autotrader-dashboard"].load())
subprocess.run([sys.executable, "-m", "main", "--help"], check=True)
print("Installed package, strategy discovery, dashboard assets and entry points verified.")
