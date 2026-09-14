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
| [live_settings.py](../src/live_settings.py) | Validated controls, runtime dotenv storage, atomic saves and revision checks |
| [dashboard/app.py](../src/dashboard/app.py) | Authenticated HTTP controls and read-only runner status |
| [trader.py](../src/trader.py) | Broker checks and coordination of one trading cycle |
| [market_data.py](../src/market_data.py) | Alpaca history retrieval, conversion, and session alignment |
| [signals.py](../src/signals.py) | Daily training, cached ranking, and target construction |
| [order_journal.py](../src/order_journal.py) | Persistent attempt reservations, daily cap, and order IDs |
| [execution.py](../src/execution.py) | Asset eligibility, cost decisions, and order submission |
| [model_loader.py](../src/model_loader.py) | Shared loader for the legacy model filename |
| [cross-sectional.py](../src/models/cross-sectional.py) | Features, labels, ridge estimator, ranking and allocation |
| [utils.py](../src/utils.py) | Shared `Account` connection and `Ticker` asset lookup |
| [templates.py](../src/modules/templates.py) | Abstract model/trader interfaces and decision dispatch |
| [config.py](../src/config.py) | Typed model/account configuration and `TraderSettings` environment parsing |

The existing hyphenated model filename is loaded using `importlib`. Production
execution uses `Autotrader`; the model module also contains a
`CrossSectionalTrader` helper, which is not the persistent service's order path.

## Component boundaries

`Account` is used by `Autotrader.from_environment()` to create one paper-trading
connection. `Ticker(symbol, account)` receives that existing account; constructing
it neither logs in nor opens another connection. `OrderExecutor` uses `Ticker`
for asset eligibility checks before reserving and submitting an order.

`Autotrader` supplies default components, or accepts explicit `signals`,
`journal`, and `executor` objects. Tests and alternate implementations can replace
these independently. `DailySignals` accepts a history provider and an optional
estimator-backed model. `HistoricalData` receives its data client rather than
constructing credentials or authenticating itself.

```mermaid
flowchart TD
    Main[main.py] --> Runtime[Persistent runtime]
    Runtime --> Trader[Autotrader coordinator]
    Trader --> Signals[DailySignals]
    Signals --> History[HistoricalData]
    Signals --> Model[Cross-sectional model]
    Trader --> Executor[OrderExecutor]
    Trader --> Journal[OrderJournal]
    Executor --> Journal
    Executor --> Ticker[Ticker]
    Ticker --> Account[Shared Account]
    Executor --> Account
```

The environment factory wires concrete Alpaca dependencies together. Scheduling
remains in `runtime.py`; data loading does not submit orders, signal generation
does not modify the journal, and the journal does not contact Alpaca. Configuration
is validated before the factory logs in. Logging configuration belongs to the
entry point, not an import of `utils.py`.

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
| `test_components.py` | Independent component injection, journal limits, account reuse, and universe validation |
| `test_dashboard.py` | HTTP authentication, validation, persistence, status and concurrent edit conflicts |
| `test_live_settings.py` | Live limits, pause/resume, order caps, cache invalidation and unchanged daily counts |
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

Run the companion UI with `PYTHONPATH=src uv run python -m dashboard`. See
[the dashboard guide](dashboard.md) for its access key and API.
