"""Offline fixtures and loader for the existing hyphenated model filename."""
import socket

import pytest
import brokers.account as utils


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
    from models import cross_sectional
    return cross_sectional
