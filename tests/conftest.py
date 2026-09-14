"""Offline fixtures and loader for the existing hyphenated model filename."""
import importlib.util
from pathlib import Path
import socket
import sys

import pytest
import utils


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(utils, "load_dotenv", lambda: None)
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not connect to the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture(scope="session")
def cs():
    path = Path(__file__).resolve().parents[1] / "src/models/cross-sectional.py"
    spec = importlib.util.spec_from_file_location("cross_sectional", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
