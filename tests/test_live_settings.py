from core.types import FeatureRow, TradeCandidate
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from configuration.settings import LiveSettingsStore, SettingsConflict, TradingSettings, serialize_settings
from data.historical import NY
from infrastructure.storage import write_json
from trading.runner import Autotrader


@pytest.fixture
def defaults():
    return TradingSettings(universe={"AAA": "XLK", "BBB": "XLF"})


@pytest.fixture
def store(tmp_path, defaults):
    return LiveSettingsStore(tmp_path / "control", lambda: defaults)


@pytest.mark.parametrize("changes", [
    {"max_daily_trades": -1}, {"max_daily_trades": 1001}, {"max_daily_trades": 1.5},
    {"max_daily_trades": True}, {"minimum_order_notional": 0},
    {"maximum_order_notional": 5}, {"minimum_order_notional": float("nan")},
    {"maximum_order_notional": float("inf")}, {"maximum_order_notional": 100.001},
    {"maximum_position": .6}, {"maximum_sector_exposure": .6},
    {"maximum_total_exposure": 1.1}, {"top_n": 0}, {"feed": "unknown"},
    {"trading_enabled": "true"}, {"APCA_API_KEY_ID": "forbidden"},
    {"universe": {"<script>": "XLK", "AAA": "XLK"}},
    {"universe": {"AAA": "XLK", " aaa ": "XLF"}},
])
def test_rejects_invalid_controls(defaults, changes):
    with pytest.raises(ValidationError):
        TradingSettings.model_validate({**defaults.model_dump(), **changes})


def test_roundtrip_only_allowlisted_values(store, defaults, monkeypatch):
    monkeypatch.setenv("APCA_API_SECRET_KEY", "do-not-export")
    initial = store.read()
    changes = TradingSettings.model_validate({**defaults.model_dump(), "max_daily_trades": 7,
                                             "maximum_order_notional": 250.25, "trading_enabled": False})
    saved = store.save(changes, initial.revision)
    recovered = LiveSettingsStore(store.directory, lambda: defaults).read()
    assert recovered == saved
    assert "do-not-export" not in store.path.read_text()
    assert "APCA_" not in serialize_settings(saved.settings)
    assert saved.revision != initial.revision


def test_conflicting_saves_preserve_first_writer(store, defaults):
    initial = store.read()
    changed = TradingSettings.model_validate({**defaults.model_dump(), "max_daily_trades": 3})
    store.save(changed, initial.revision)
    with pytest.raises(SettingsConflict):
        store.save(defaults, initial.revision)
    assert store.read().settings.max_daily_trades == 3


def test_simultaneous_writers_have_one_winner(store, defaults):
    revision = store.read().revision
    def save(limit):
        settings = TradingSettings.model_validate({**defaults.model_dump(), "max_daily_trades": limit})
        try:
            store.save(settings, revision)
            return "saved"
        except SettingsConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [3, 4])) == ["conflict", "saved"]


def test_corrupt_file_does_not_fall_back_to_defaults(store):
    store.directory.mkdir()
    store.path.write_text("TRADER_MAX_DAILY_TRADES=900\n")
    with pytest.raises(ValueError):
        store.read()


def test_environment_duplicate_symbols_rejected(monkeypatch):
    monkeypatch.setenv("TRADER_UNIVERSE", '{"AAA":"XLK","AAA":"XLF"}')
    with pytest.raises(ValueError, match="Duplicate"):
        TradingSettings.from_environment()


def make_runner(tmp_path, store, defaults):
    client = Mock()
    client.get_clock.return_value = SimpleNamespace(is_open=True, timestamp=datetime(2026, 9, 14, 10, tzinfo=NY))
    client.get_orders.return_value = []
    client.get_account.return_value = SimpleNamespace(trading_blocked=False, equity="1000", buying_power="1000")
    client.get_all_positions.return_value = []
    client.get_asset.return_value = SimpleNamespace(tradable=True, fractionable=True)
    runner = Autotrader(SimpleNamespace(trading_client=client), Mock(), defaults.universe,
                       tmp_path, settings=defaults, settings_store=store)
    runner.signals.refresh = Mock()
    row = FeatureRow(datetime(2026, 9, 11).date(), "AAA", "XLK", 100, 10000000, (0,) * 16)
    runner.signals.model.ranked = [(row, .02, 1)]
    return runner


def test_live_limit_change_preserves_attempts(tmp_path, store, defaults):
    runner = make_runner(tmp_path, store, defaults)
    write_json(runner.journal.state_path, {"day": "2026-09-14", "attempts": ["old1", "old2"]})
    settings = TradingSettings.model_validate({**defaults.model_dump(), "max_daily_trades": 2})
    store.save(settings, store.read().revision)
    runner.run_once()
    runner.client.submit_order.assert_not_called()
    assert runner.journal.read("2026-09-14")["attempts"] == ["old1", "old2"]
    store.save(defaults, store.read().revision)
    runner.run_once()
    runner.client.submit_order.assert_called_once()
    assert len(runner.journal.read("2026-09-14")["attempts"]) == 3


def test_pause_then_resume_between_cycles(tmp_path, store, defaults):
    runner = make_runner(tmp_path, store, defaults)
    paused = TradingSettings.model_validate({**defaults.model_dump(), "trading_enabled": False})
    store.save(paused, store.read().revision)
    runner.run_once()
    runner.client.get_clock.assert_not_called()
    store.save(defaults, store.read().revision)
    runner.run_once()
    runner.client.submit_order.assert_called_once()


def test_universe_and_feed_changes_invalidate_daily_signals(tmp_path, store, defaults):
    runner = make_runner(tmp_path, store, defaults)
    runner.signals.signal_date = datetime(2026, 9, 14).date()
    updated = TradingSettings.model_validate({**defaults.model_dump(), "universe": {"CCC": "XLE", "DDD": "XLV"}, "feed": "sip"})
    store.save(updated, store.read().revision)
    runner.reload_settings()
    assert runner.signals.signal_date is None
    assert runner.signals.model.ranked == []
    assert runner.signals.universe == updated.universe
    assert runner.signals.history.feed.value == "sip"
    assert (tmp_path / "applied-settings.json").exists()


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_maximum_amount_caps_buys_and_sells_without_repeat(tmp_path, store, defaults, side):
    runner = make_runner(tmp_path, store, defaults)
    capped = TradingSettings.model_validate({**defaults.model_dump(), "maximum_order_notional": 25.25})
    store.save(capped, store.read().revision)
    if side == "sell":
        runner.client.get_all_positions.return_value = [SimpleNamespace(symbol="AAA", market_value="200")]
    runner.run_once()
    order = runner.client.submit_order.call_args.kwargs["order_data"]
    assert order.notional == 25.25 and order.side.value == side
    runner.run_once()
    assert runner.client.submit_order.call_count == 1


def test_minimum_is_applied_before_order_reservation(tmp_path, store, defaults):
    runner = make_runner(tmp_path, store, defaults)
    changes = TradingSettings.model_validate({**defaults.model_dump(), "minimum_order_notional": 150.0})
    store.save(changes, store.read().revision)
    runner.run_once()
    runner.client.submit_order.assert_not_called()
    assert not runner.journal.state_path.exists()


def test_new_process_reads_saved_controls_without_valid_environment(tmp_path, store, defaults, monkeypatch):
    paused = TradingSettings.model_validate({**defaults.model_dump(), "trading_enabled": False})
    store.save(paused, store.read().revision)
    monkeypatch.delenv("TRADER_UNIVERSE", raising=False)
    assert LiveSettingsStore(store.directory).read().settings == paused
