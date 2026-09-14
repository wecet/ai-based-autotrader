"""Offline fixtures and loader for the existing hyphenated model filename."""
import importlib.util
from pathlib import Path
import socket
import sys

import pytest
import utils


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    original_socket = socket.socket
    original_load_dotenv = utils.load_dotenv

    class OfflineSocket(original_socket):
        def connect(self, *args, **kwargs):
            raise AssertionError("Tests must not connect to the network")

        def connect_ex(self, *args, **kwargs):
            raise AssertionError("Tests must not connect to the network")

    utils.load_dotenv = lambda: None
    socket.socket = OfflineSocket
    try:
        yield
    finally:
        socket.socket = original_socket
        utils.load_dotenv = original_load_dotenv


@pytest.fixture(scope="session")
def cs():
    path = Path(__file__).resolve().parents[1] / "src/models/cross-sectional.py"
    spec = importlib.util.spec_from_file_location("cross_sectional", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
