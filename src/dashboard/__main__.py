"""Start the dashboard with python -m dashboard (src on PYTHONPATH)."""
import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

from dashboard.app import access_key, create_app
from configuration.settings import LiveSettingsStore


def main():
    load_dotenv()
    state_dir = Path(os.getenv("TRADER_STATE_DIR", "state"))
    store = LiveSettingsStore(Path(os.getenv("TRADER_CONTROL_DIR", str(state_dir / "control"))))
    app = create_app(store, state_dir, access_key(store))
    uvicorn.run(app, host=os.getenv("DASHBOARD_BIND", "127.0.0.1"),
                port=int(os.getenv("DASHBOARD_PORT", "8080")), access_log=False)


if __name__ == "__main__":
    main()
