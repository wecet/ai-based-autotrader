# AI autotrader

The service runs the cross-sectional model against an Alpaca **paper account**.
It checks the market and portfolio every 60 seconds, submits a model-approved
buy or sell when needed, and continues waiting when the market is closed or
there is no trade. It is long-only and submits at most **10 order attempts per
New York trading day**. Failed or ambiguous submissions also count toward this
limit. Orders are sequential: the next cycle waits for pending orders to settle.

## Deploy

1. Install and start Docker with Docker Compose support.
2. Add the settings from `.env.example` to your `.env`, keeping your paper API
   credentials. Set `TRADER_UNIVERSE` to a JSON mapping of stock symbols to their
   sector ETFs; the example supplies six stocks across three sectors. At least
   two stocks with complete history are required.
3. Build and start the service:

   ```sh
   docker compose up -d --build
   docker compose ps
   docker compose logs -f trader
   ```

Stop with `docker compose stop`; resume with `docker compose up -d`.
Code changes require `docker compose up -d --build`.

The container runs as an unprivileged user, with a read-only root filesystem,
rotating logs, and a named volume for its order journal. Credentials are passed
at runtime and excluded from the image. Do not remove the `trader-state` volume
or use `docker compose down -v` during a trading day: the journal enforces the
order cap and duplicate suppression across restarts. Run one instance per paper
account; other programs' orders are not counted in this service's limit.

`restart: unless-stopped` restarts an exited process, including after Docker
restarts unless you explicitly stopped the service. Docker and the host must
remain running and awake. A health check alone does not restart a container:
the application exits after three consecutive cycle failures or a 120-second
cycle timeout so the restart policy can take effect. SIGTERM requests a graceful
stop; Docker terminates a stuck shutdown after 30 seconds.

## Model behavior

The service downloads two years of split-adjusted daily bars through Alpaca's
historical-data client. It excludes today's incomplete bar, requires histories
to align with SPY, and trains only on rows with completed five-day outcomes.
Signals refresh on the first open-market cycle each day. The existing model is
a daily model: more frequent polling does not create intraday predictive signals.
Current holdings and buying power are refreshed each cycle. A given symbol,
side, and target weight is attempted only once per day to avoid repeated orders
from the same signal. Reductions are processed before additions.

The existing model targets at most five stocks, 10% per position, 20% per sector,
and 50% total exposure. Holdings outside the eligible universe are retained and
consume the total exposure budget. This is a target allocation; market movements
can move actual holdings above these percentages between rebalances. The service
requires tradable, fractionable assets for its notional orders.

`TRADER_DATA_FEED=iex` uses IEX bars. These reflect that venue's volume and can
cause the model's $5 million average daily dollar-volume filter to exclude stocks.
Use `sip` only with the corresponding Alpaca data entitlement. No eligible data,
a blocked account, or broker failures are logged as errors rather than silently
reported as successful trading.

## Local execution and tests

```sh
uv sync --group dev
uv run python src/main.py
uv run pytest --cov
```

`uv run python -m src.main` is also supported. Local runtime state goes in
`state/` unless `TRADER_STATE_DIR` is set. `--healthcheck` exits 0 only for a recent
healthy heartbeat, without logging into Alpaca.

Tests use synthetic market data and mock broker clients. They exercise repeated
cycles and the actual entry point in a subprocess, including graceful SIGTERM,
timeouts, recovery, daily limits, and persisted duplicate prevention. They do
not establish strategy profitability or verify real broker execution.

Configuration: `TRADER_POLL_SECONDS` (60), `TRADER_CYCLE_TIMEOUT_SECONDS` (120),
and `TRADER_MAX_FAILURES` (3) must be positive. Keep the cycle timeout below the
health check's 180-second staleness threshold.

References: [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/),
[Alpaca historical data](https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html).
