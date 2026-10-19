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


@pytest.fixture
def strategy_dataset():
    """Synthetic daily histories, independent of the research CLI/package."""
    from datetime import date, timedelta
    from types import SimpleNamespace
    import numpy as np
    from core.types import DailyBar, MarketHistory

    def make(count=300):
        random = np.random.default_rng(7)
        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(count)]
        def series(drift):
            close = 100 * np.exp(np.cumsum(random.normal(drift, .008, count)))
            return [DailyBar(day, float(c), float(c * 1.01), float(c * .99), float(c), 1_000_000)
                    for day, c in zip(dates, close)]
        spy, sector = series(.0002), series(.0003)
        universe = {'AAA': 'GROUP', 'BBB': 'GROUP', 'CCC': 'OTHER'}
        history = MarketHistory({'AAA': series(.0008), 'BBB': series(-.0001), 'CCC': series(.0005)},
                                spy, {'GROUP': sector, 'OTHER': spy})
        return SimpleNamespace(universe=universe, history=history)
    return make
