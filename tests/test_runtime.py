import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from unittest.mock import Mock

import pytest
import main
from infrastructure.service import CycleTimeout, deadline, healthy, run_forever, write_json


def test_loop_repeats_recovers_and_stops(tmp_path):
    stop = threading.Event()
    heartbeat = tmp_path / "heartbeat.json"
    count = 0

    def cycle():
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("temporary outage")
        if count == 4:
            stop.set()

    run_forever(cycle, stop, heartbeat, interval=.001)
    assert count == 4
    assert not heartbeat.exists()


def test_repeated_failures_exit(tmp_path):
    cycle = Mock(side_effect=RuntimeError("offline"))
    with pytest.raises(RuntimeError):
        run_forever(cycle, threading.Event(), tmp_path / "heartbeat.json", interval=.001)
    assert cycle.call_count == 3


def test_deadline_interrupts_stuck_cycle(tmp_path):
    with pytest.raises(CycleTimeout):
        run_forever(lambda: time.sleep(1), threading.Event(), tmp_path / "heartbeat.json",
                    cycle_timeout=.01)
    assert not (tmp_path / "heartbeat.json").exists()


@pytest.mark.parametrize("payload,expected", [
    ({"time": 100, "healthy": True}, True),
    ({"time": 100, "healthy": False}, False),
    ({"time": -100, "healthy": True}, False),
    ({"time": 101, "healthy": True}, False), ({}, False)])
def test_health_status(tmp_path, payload, expected):
    path = tmp_path / "heartbeat.json"
    write_json(path, payload)
    assert healthy(path, now=100) is expected


def test_main_healthcheck_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_STATE_DIR", str(tmp_path))
    assert main.main(["--healthcheck"]) == 1
    write_json(tmp_path / "heartbeat.json", {"time": time.time(), "healthy": True})
    assert main.main(["--healthcheck"]) == 0


def test_real_main_repeats_and_handles_sigterm(tmp_path):
    # Exercise actual entry-point/lifecycle in a separate process, with no broker.
    script = '''
import main
from trading.runner import Autotrader
from pathlib import Path
import os
class Fake:
    def run_once(self):
        with (Path(os.environ["TRADER_STATE_DIR"]) / "cycles").open("a") as f:
            f.write("cycle\\n")
Autotrader.from_environment = lambda path: Fake()
raise SystemExit(main.main([]))
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "TRADER_STATE_DIR": str(tmp_path), "TRADER_POLL_SECONDS": ".02"}
    process = subprocess.Popen([os.sys.executable, "-c", script], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            cycles = tmp_path / "cycles"
            if cycles.exists() and len(cycles.read_text().splitlines()) >= 3:
                break
            if process.poll() is not None:
                pytest.fail(process.communicate()[1].decode())
            time.sleep(.02)
        else:
            pytest.fail("Entry point did not run repeated cycles")
        assert healthy(tmp_path / "heartbeat.json")
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=5)
        assert process.returncode == 0, error.decode()
        assert not (tmp_path / "heartbeat.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_serve_initialization_retries_and_signal_stop(tmp_path, monkeypatch):
    import infrastructure.service as runtime
    monkeypatch.setenv("TRADER_POLL_SECONDS", ".001")
    old_handler = signal.getsignal(signal.SIGTERM)
    service = Mock()
    service.run_once.side_effect = lambda: os.kill(os.getpid(), signal.SIGTERM)
    factory = Mock(side_effect=[RuntimeError("login outage"), service])
    runtime.serve(factory, tmp_path)
    assert factory.call_count == 2
    service.run_once.assert_called_once()
    assert signal.getsignal(signal.SIGTERM) == old_handler


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_runtime_settings(tmp_path, monkeypatch, value):
    import infrastructure.service as runtime
    monkeypatch.setenv("TRADER_POLL_SECONDS", value)
    with pytest.raises(ValueError):
        runtime.serve(Mock(), tmp_path)
