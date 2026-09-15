"""Run with `python src/main.py` or `python -m src.main`."""
import logging
import os
from pathlib import Path
import sys

# Preserve the project's existing src-root imports for both entry-point forms.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from infrastructure.service import CycleTimeout, healthy, serve


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments in (["-h"], ["--help"]):
        print("Usage: python src/main.py [--healthcheck]")
        return 0
    if arguments not in ([], ["--healthcheck"]):
        print("Usage: python src/main.py [--healthcheck]", file=sys.stderr)
        return 2
    state_dir = Path(os.getenv("TRADER_STATE_DIR", "state"))
    if arguments == ["--healthcheck"]:
        return 0 if healthy(state_dir / "heartbeat.json") else 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from dotenv import load_dotenv
    load_dotenv()
    state_dir = Path(os.getenv("TRADER_STATE_DIR", "state"))
    from trading.runner import Autotrader
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        # One process per state volume; prevents concurrent rebalances.
        import fcntl
        with (state_dir / "runner.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            serve(lambda: Autotrader.from_environment(state_dir), state_dir)
    except (Exception, CycleTimeout):
        logging.exception("Autotrader stopped after a fatal error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
