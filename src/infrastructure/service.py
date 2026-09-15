"""Persistent service lifecycle, independent of strategy and broker."""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
from infrastructure.storage import write_json


class CycleTimeout(BaseException):
    """Fatal deadline: do not continue an interrupted trading cycle."""


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise CycleTimeout("Trading cycle exceeded its deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def healthy(path, max_age=180, now=None):
    try:
        status = json.loads(path.read_text())
        age = (time.time() if now is None else now) - status["time"]
        return status["healthy"] is True and 0 <= age <= max_age
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run_forever(cycle, stop, heartbeat, *, interval=60, cycle_timeout=120, max_failures=3):
    """Run sequential cycles; exit after repeated failures or a stuck cycle."""
    failures = 0
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    try:
        while not stop.is_set():
            try:
                with deadline(cycle_timeout):
                    cycle()
            except Exception:
                failures += 1
                logging.exception("Trading cycle failed (%s/%s)", failures, max_failures)
                write_json(heartbeat, {"time": time.time(), "healthy": False})
                if failures >= max_failures:
                    raise
            else:
                failures = 0
                write_json(heartbeat, {"time": time.time(), "healthy": True})
            # Refresh only after a successful cycle, including while the market is closed.
            remaining = interval
            while remaining > 0 and not stop.is_set():
                delay = min(remaining, 30)
                if stop.wait(delay):
                    break
                remaining -= delay
                if failures == 0:
                    write_json(heartbeat, {"time": time.time(), "healthy": True})
    finally:
        heartbeat.unlink(missing_ok=True)


def positive_number(name, default, kind=float):
    value = kind(os.getenv(name, str(default)))
    if not 0 < value < float("inf"):
        raise ValueError(f"{name} must be finite and positive")
    return value


def serve(factory, state_dir: Path):
    stop = threading.Event()
    previous = {}

    def shutdown(signum, frame):
        logging.info("Shutdown requested")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, shutdown)
    try:
        interval = positive_number("TRADER_POLL_SECONDS", 60)
        timeout = positive_number("TRADER_CYCLE_TIMEOUT_SECONDS", 120)
        failures = positive_number("TRADER_MAX_FAILURES", 3, int)
        # Construction is inside the deadline too (login can make network calls).
        service = None

        def cycle():
            nonlocal service
            if service is None:
                service = factory()
            if not stop.is_set():
                service.run_once()

        run_forever(cycle, stop, state_dir / "heartbeat.json", interval=interval,
                    cycle_timeout=timeout, max_failures=failures)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
