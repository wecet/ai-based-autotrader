# Automated tests

Run from the repository root with Python 3.13:

```sh
uv sync --group dev
uv run pytest
uv run pytest --cov --cov-report=term-missing
```

VS Code's Testing sidebar uses the project's pytest configuration and `.venv`
interpreter. Tests cover the model, accounts, persistent runtime, broker guards,
and restart-safe order limits with synthetic data and mock clients.

See [development and testing](../docs/development.md) for the module guide,
offline-test boundaries, debugging setup, and coding conventions.
