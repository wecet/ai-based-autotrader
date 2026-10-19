# <img src="src/dashboard/static/favicon.svg" width="32" height="32" alt="Autotrader logo"> AI-Based Autotrader

A persistent, long-only Alpaca **paper-trading** service with selectable models and a cross-sectional
ranking strategy by default. Momentum rotation and boosted-tree ranking are also
available through the model selector. It evaluates trading opportunities between waits of 60 seconds,
uses completed daily bars for its signals, and defaults to at most **10 order
attempts per New York trading day**, adjustable in the dashboard. The intended expanded universe contains
136 established stocks, 29 specialist stocks, 58 equity ETFs and 7 commodity funds; the actual candidates come from your
`TRADER_UNIVERSE` configuration.

## Getting started

Configure `.env` using the [configuration guide](docs/configuration.md), keeping
any existing credentials and universe entries. Then run:

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f trader
```

Open the [Control room](http://localhost:8080) to select a strategy and edit the universe, daily order
budget, order amounts, and exposure limits without restarting the trader. Unlock
it with the separate dashboard key:

```sh
docker compose exec dashboard cat /app/control/dashboard.key
```

The **Performance calendar** page shows daily account gains and losses, completed
trade fills, and net P/L for a rolling 24 hours, the last 7 or 30 New York
calendar days, or all available broker history. P/L includes open positions and
all activity in the paper account. Rebuild both services to enable reporting;
the trader syncs history at most once every five minutes.

See the [dashboard guide](docs/dashboard.md) for live-update timing, access,
and the shared runtime settings file.

Docker must remain running on an awake host. The service waits through closed
markets and restarts after process failures unless explicitly stopped. Its
persistent order journal prevents repeat submissions and preserves the daily
limit across container restarts.

For local development on macOS or Linux with Python 3.13:

```sh
uv sync --group dev
uv run python src/main.py
uv run pytest
```

The model can generate multiple orders in a day, but it refreshes its predictive
signals daily. Universe inclusion and a healthy container do not establish
profitability or successful order fills.

## Strategy research

Compare the three models offline using the shared walk-forward harness:

```sh
uv run python -m research --demo /tmp/strategy-demo.json
```

The synthetic demo verifies the pipeline; it does not demonstrate profitability.
See [strategy research](docs/research.md) for frozen datasets, reserved final-test
periods, cost/delay stress cases, and the linked GitHub research roadmap.
To merge the versioned candidate list into an existing local universe, use
`uv run python -m configuration.universe --apply`; see [universe setup](docs/universe.md)
for saved Docker settings and benchmark preservation.

## Repository layout

- `src/`: trader, broker adapters, infrastructure, and web portal.
- `tests/`: offline automated tests.
- `docs/`: configuration, operations, and development guides.
- `scripts/`: package verification and the optional `check_account.py` paper-account diagnostic.
- `libs/`: Git submodules; see [submodule setup](libs/README.md).
- `build_files/`: Docker build definitions.

Local `.env`, `.venv/`, and `state/` stay outside version control. Build output,
test caches, coverage files, and macOS metadata are generated and ignored.

## Documentation

Start with the [documentation index](docs/README.md).

- [Dashboard and performance calendar](docs/dashboard.md): edit controls, review daily P/L, and inspect trade fills.
- [Strategy research](docs/research.md): model methods, offline comparisons, validation, and upcoming work.
- [Drop-in strategies](docs/models.md): add a model file and select it at runtime.
- [Configuration](docs/configuration.md): environment variables and universe format.
- [Universe](docs/universe.md): versioned stock, ETF and commodity candidates, benchmark groups, and eligibility.
- [Trading behavior](docs/trading.md): data, ranking, portfolio targets, and order limits.
- [Deployment and operations](docs/operations.md): Docker lifecycle, health, state, and troubleshooting.
- [Development and testing](docs/development.md): architecture, pytest, VS Code, and coding conventions.

- [Code architecture](src/readme.md): runtime flow and component boundaries.
- [Releases and packages](docs/releases.md): build distributions and publish tagged releases.
