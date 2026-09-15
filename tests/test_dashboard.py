from core.types import FeatureRow, TradeCandidate
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from dashboard.app import access_key, create_app
from configuration.settings import LiveSettingsStore, TradingSettings
from infrastructure.storage import write_json


@pytest.fixture
def panel(tmp_path):
    defaults = TradingSettings(universe={"AAA": "XLK", "BBB": "XLF"})
    store = LiveSettingsStore(tmp_path / "control", lambda: defaults)
    key = access_key(store)
    app = create_app(store, tmp_path, key, allowed_hosts=["testserver"])
    with TestClient(app) as client:
        yield client, store, key


def headers(key):
    return {"Authorization": "Bearer " + key}


def test_page_and_assets_are_available_but_api_is_locked(panel):
    client, store, key = panel
    response = client.get("/")
    assert response.status_code == 200
    assert "A little more control." in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    for path in ("/api/settings", "/api/status", "/api/export"):
        assert client.get(path).status_code == 401
    assert client.put("/api/settings", json={}).status_code == 401
    assert client.get("/healthz").status_code == 200
    assert key not in response.text


def test_save_export_status_and_persistence(panel, tmp_path):
    client, store, key = panel
    current = client.get("/api/settings", headers=headers(key)).json()
    current["settings"]["max_daily_trades"] = 4
    current["settings"]["maximum_order_notional"] = 50
    response = client.put("/api/settings", headers=headers(key), json={"settings": current["settings"], "revision": current["revision"]})
    assert response.status_code == 200
    assert response.json()["status"]["settings_state"] == "pending"
    revision = response.json()["revision"]
    assert store.read().settings.max_daily_trades == 4
    write_json(tmp_path / "applied-settings.json", {"revision": revision, "time": time.time()})
    write_json(tmp_path / "heartbeat.json", {"healthy": True, "time": time.time()})
    write_json(tmp_path / "orders.json", {"day": datetime.now(ZoneInfo("America/New_York")).date().isoformat(), "attempts": ["a", "b"]})
    status = client.get("/api/status", headers=headers(key)).json()["status"]
    assert status["settings_state"] == "applied"
    assert status["attempts_today"] == 2
    exported = client.get("/api/export", headers=headers(key))
    assert "TRADER_MAX_DAILY_TRADES=4" in exported.text
    assert "APCA_" not in exported.text and key not in exported.text
    assert "runtime.env" in exported.headers["content-disposition"]


def test_invalid_settings_and_secret_fields_not_persisted_or_echoed(panel):
    client, store, key = panel
    current = client.get("/api/settings", headers=headers(key)).json()
    current["settings"]["APCA_API_SECRET_KEY"] = "never-echo-this"
    response = client.put("/api/settings", headers=headers(key), json={"revision": current["revision"], "settings": current["settings"]})
    assert response.status_code == 422
    assert "never-echo-this" not in response.text
    assert not store.path.exists()


def test_stale_revision_is_rejected(panel):
    client, store, key = panel
    current = client.get("/api/settings", headers=headers(key)).json()
    payload = {"revision": current["revision"], "settings": current["settings"]}
    payload["settings"]["max_daily_trades"] = 2
    assert client.put("/api/settings", headers=headers(key), json=payload).status_code == 200
    payload["settings"]["max_daily_trades"] = 9
    assert client.put("/api/settings", headers=headers(key), json=payload).status_code == 409
    assert store.read().settings.max_daily_trades == 2


def test_cross_origin_and_unknown_host_are_rejected(panel):
    client, _, key = panel
    assert client.get("/api/settings", headers={**headers(key), "Origin": "https://other.example"}).status_code == 403
    assert client.get("/", headers={"Host": "malicious.example"}).status_code == 400


def test_large_or_wrong_content_type_requests_rejected(panel):
    client, _, key = panel
    assert client.put("/api/settings", headers=headers(key), content="bad").status_code == 415
    response = client.put("/api/settings", headers={**headers(key), "Content-Type": "application/json"}, content=" " * 131073)
    assert response.status_code == 413


def test_key_is_persistent_and_not_world_readable(panel):
    _, store, key = panel
    assert access_key(store) == key
    assert (store.directory / "dashboard.key").stat().st_mode & 0o077 == 0


def test_malformed_state_does_not_claim_zero_attempts(panel, tmp_path):
    client, _, key = panel
    (tmp_path / "orders.json").write_text("bad")
    status = client.get("/api/status", headers=headers(key)).json()["status"]
    assert status["attempts_today"] is None
    assert status["health"] == "unavailable"


def test_duplicate_json_keys_rejected_before_last_value_wins(panel):
    client, store, key = panel
    current = client.get('/api/settings', headers=headers(key)).json()
    payload = json.dumps({'revision': current['revision'], 'settings': current['settings']})
    payload = payload.replace('"max_daily_trades": 10', '"max_daily_trades": 10, "max_daily_trades": 500')
    response = client.put('/api/settings', headers={**headers(key), 'Content-Type': 'application/json'}, content=payload)
    assert response.status_code == 422
    assert not store.path.exists()


def test_api_save_reaches_real_trader_components(panel, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from trading.runner import Autotrader
    from data.historical import NY

    http, store, key = panel
    defaults = store.read().settings
    broker = Mock()
    broker.get_clock.return_value = SimpleNamespace(is_open=True, timestamp=datetime(2026, 9, 14, 10, tzinfo=NY))
    broker.get_orders.return_value = []
    broker.get_account.return_value = SimpleNamespace(trading_blocked=False, equity='1000', buying_power='1000')
    broker.get_all_positions.return_value = []
    broker.get_asset.return_value = SimpleNamespace(tradable=True, fractionable=True)
    runner = Autotrader(SimpleNamespace(trading_client=broker), Mock(), defaults.universe,
                       tmp_path, settings=defaults, settings_store=store)
    runner.signals.refresh = Mock()
    row = FeatureRow(datetime(2026, 9, 11).date(), 'AAA', 'XLK', 100, 10000000, (0,) * 16)
    runner.signals.model.ranked = [(row, .02, 1)]
    current = http.get('/api/settings', headers=headers(key)).json()
    settings = {**current['settings'], 'max_daily_trades': 1, 'maximum_order_notional': 20.5}
    response = http.put('/api/settings', headers=headers(key), json={'revision': current['revision'], 'settings': settings})
    assert response.status_code == 200
    runner.run_once()
    assert broker.submit_order.call_args.kwargs['order_data'].notional == 20.5
    assert runner.journal.limit == 1
    assert runner.settings_revision == response.json()['revision']
    runner.run_once()
    assert broker.submit_order.call_count == 1
