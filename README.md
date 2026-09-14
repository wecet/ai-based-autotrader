# AI autotrader

A persistent, long-only Alpaca **paper-trading** service using a cross-sectional
ranking model. It evaluates trading opportunities between waits of 60 seconds,
uses completed daily bars for its signals, and submits at most **10 order
attempts per New York trading day**. The intended expanded universe contains
50 equity ETFs and 100 individual stocks; the actual candidates come from your
`TRADER_UNIVERSE` configuration.

## Getting started

Configure `.env` using the [configuration guide](docs/configuration.md), keeping
any existing credentials and universe entries. Then run:

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f trader
```

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

## Documentation

Start with the [documentation index](docs/README.md).

- [Configuration](docs/configuration.md): environment variables and universe format.
- [Universe](docs/universe.md): ETF selection, stock sampling, benchmark groups, and eligibility.
- [Trading behavior](docs/trading.md): data, ranking, portfolio targets, and order limits.
- [Deployment and operations](docs/operations.md): Docker lifecycle, health, state, and troubleshooting.
- [Development and testing](docs/development.md): architecture, pytest, VS Code, and coding conventions.
