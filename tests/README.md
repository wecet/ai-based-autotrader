# Automated tests

Run from the repository root with Python 3.13:

```sh
uv sync --group dev
uv run pytest
uv run pytest --cov --cov-report=term-missing
```

VS Code's Testing sidebar uses the project's pytest configuration and `.venv`
interpreter. Tests cover the model, accounts, persistent runtime, broker guards,
restart-safe order limits, dashboard authentication/settings, and performance
reporting with synthetic data and mock clients. Performance tests cover daily
cumulative P/L, rolling windows, New York date grouping, paginated trade fills,
paused-trader reporting, and retaining a valid report after a failed refresh.

Run the reporting and API checks directly with:

```sh
uv run pytest tests/test_performance.py tests/test_dashboard.py
```

Strategy and research tests also cover retained-holding exposure, momentum
entry/exit rules, deterministic nonlinear fitting, purged calibration, next-open
accounting, cost stress, partial fills, rejected orders, stale marks and additive
universe updates:

```sh
uv run pytest tests/test_allocation.py tests/test_momentum.py tests/test_boosted_ranker.py tests/test_research.py tests/test_universe.py
```

See [development and testing](../docs/development.md) for the module guide,
offline-test boundaries, debugging setup, and coding conventions.
