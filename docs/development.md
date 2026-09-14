# Development and testing

## Environment

Use Python 3.13 and uv. Local runtime depends on POSIX file locking and interval
signals, so use macOS/Linux, or run the Linux container on Windows.

```sh
uv sync --group dev
uv run python src/main.py
# Equivalent module entry point:
uv run python -m src.main
```

Both entry points start the same persistent service. `--help` prints usage,
`--healthcheck` checks the heartbeat, and unsupported arguments return exit 2.
Configuration is supplied through environment variables.

## Source layout

| File | Responsibility |
| --- | --- |
| [main.py](../src/main.py) | Entry point, process lock, startup and exit status |
| [runtime.py](../src/runtime.py) | Loop, deadlines, signals, heartbeat and failure policy |
| [trader.py](../src/trader.py) | Data retrieval, daily model refresh, order journal and execution |
| [cross-sectional.py](../src/models/cross-sectional.py) | Features, labels, ridge estimator, ranking and allocation |
| [utils.py](../src/utils.py) | Paper account login and asset utilities |
| [templates.py](../src/modules/templates.py) | Abstract model/trader interfaces and decision dispatch |
| [config.py](../src/config.py) | Typed model/account configuration; not the runtime environment loader |

The existing hyphenated model filename is loaded using `importlib`. Production
execution uses `Autotrader`; the model module also contains a
`CrossSectionalTrader` helper, which is not the persistent service's order path.

## Run tests

```sh
uv run pytest
uv run pytest --cov --cov-report=term-missing
uv run pytest tests/test_autotrader.py tests/test_runtime.py
```

Pytest discovers `tests/` and adds `src/` to its import path through
[pyproject.toml](../pyproject.toml). Coverage is optional and has no configured
minimum threshold. There is no repository CI workflow; these commands run the
automated suite locally.

| Test module | Main coverage |
| --- | --- |
| `test_cross_sectional.py` | Features, labels, date splitting, estimator, ranking and allocation |
| `test_autotrader.py` | Broker guards, daily data pipeline, order cap and restart deduplication |
| `test_runtime.py` | Repeated cycles, deadlines, retries, heartbeat and subprocess SIGTERM |
| `test_accounts.py` | Credentials, paper client construction, asset lookup and errors |
| `test_config.py` | Valid account configuration and strict field validation |
| `test_templates.py` | Buy/sell/hold dispatch and conflicting decisions |

The shared fixture supplies fake credentials, disables account dotenv loading,
and blocks socket connection methods in the pytest process. Broker calls use
mocks and market bars are synthetic. The entry-point subprocess test supplies a
fake trader explicitly; parent-process fixtures do not automatically patch child
processes. Tests do not establish real fills, data-feed entitlement, financial
performance, or indefinite uptime. Coverage from child processes is not included
by the current default setup.

## VS Code

Install the recommended Microsoft Python extension and select the project's
`.venv` interpreter if needed. The Testing sidebar discovers pytest tests using
the committed [.vscode/settings.json](../.vscode/settings.json). Use its run and
debug actions for a module or individual test.

## Conventions

Follow [AGENTS.md](../AGENTS.md) and the persistent
[coding conventions](../.agents/coding-conventions.md). Use explicit attribute
access and assignments; avoid dynamic attribute helpers and `argparse`. Restore
test replacements in `finally` blocks or fixture teardown. Preserve the offline
suite and health-check interface when changing startup behavior.
