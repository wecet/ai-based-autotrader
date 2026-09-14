Run from the repository root (Python 3.13):

```sh
uv sync --group dev
uv run pytest
uv run pytest --cov --cov-report=term-missing
```

In VS Code, install the recommended Python extension, select the project's
`.venv` interpreter if prompted, then use the Testing sidebar to discover,
run, or debug tests. Pytest reads its settings from `pyproject.toml`.

Tests use synthetic daily bars and mocked Alpaca clients. The shared fixture
replaces credentials, disables `.env` loading, and rejects socket connections.
No real account or market data is required. Tests cover feature calculations,
point-in-time datasets, chronological splitting, regression and calibration,
portfolio limits, trade decisions, execution guards, account utilities, and
configuration validation. They do not validate strategy profitability or live
broker integration.
