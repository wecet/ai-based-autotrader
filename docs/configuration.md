# Configuration

The service reads its configuration from environment variables. Local startup
also loads `.env`; existing process variables take precedence. Docker Compose
passes the root `.env` file into the container. Credentials are not built into
the image.

The [live dashboard](dashboard.md) edits the controls below through a shared
runtime settings file. After the first dashboard save, that file takes precedence
for all editable controls. The host root .env remains the source of credentials
and startup-only settings.

## Required settings

| Variable | Meaning |
| --- | --- |
| `APCA_API_KEY_ID` | Alpaca paper-account API key |
| `APCA_API_SECRET_KEY` | Matching paper-account secret |
| `TRADER_UNIVERSE` | JSON object mapping candidate symbols to benchmark ETF symbols |

`Account.login()` explicitly selects paper trading. There is no environment
setting that enables a live-money account.

Create or edit `.env` without overwriting existing credentials. This minimal
configuration illustrates the syntax; it does not replace the expanded universe:

```dotenv
APCA_API_KEY_ID=your-paper-api-key
APCA_API_SECRET_KEY=your-paper-secret-key
TRADER_UNIVERSE='{
  "AAPL": "XLK",
  "MSFT": "XLK",
  "JPM": "XLF"
}'
TRADER_DATA_FEED=iex
TRADER_POLL_SECONDS=60
TRADER_CYCLE_TIMEOUT_SECONDS=120
TRADER_MAX_FAILURES=3
```

Keep the JSON inside a single pair of single quotes. JSON keys and values use
double quotes; JSON does not allow comments or trailing commas. Multiline quoted
values are supported by dotenv and Compose. Each symbol must occur only once:
JSON parsing otherwise silently retains the last occurrence. The loader strips
whitespace and uppercases symbols and proxies, so avoid case-only duplicates too.

The loader requires 2–500 unique candidates and IEX or SIP as the data feed. The data pipeline subsequently
requires at least two eligible symbols with sufficient aligned history. A
benchmark appearing only as a value supplies features; it becomes a trade
candidate only if it also appears as a key.

## Runtime settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `TRADER_DATA_FEED` | `iex` | Alpaca historical equity data feed |
| `TRADER_POLL_SECONDS` | `60` | Wait after each completed or recoverable failed cycle |
| `TRADER_CYCLE_TIMEOUT_SECONDS` | `120` | Deadline covering initialization and each cycle |
| `TRADER_MAX_FAILURES` | `3` | Consecutive ordinary cycle failures before process exit |
| `TRADER_STATE_DIR` | `state` locally | Writable directory for journal, heartbeat, and lock |

Timing values must be finite and positive; the failure limit must be a positive
integer. A cycle takes time itself, so the interval between cycle starts is
longer than the configured wait.

Compose overrides `TRADER_STATE_DIR` with `/app/state` and mounts a named volume
there. Changing that variable in `.env` does not change the Compose mount.

Keep cycle timeout below the fixed 180-second heartbeat age limit. A larger
universe increases download and training work. Measure it before changing the
timeout; longer deadlines also require reviewing health checks and shutdown
settings in the implementation.

IEX volume is specific to that venue. It can cause candidates to fail the model's
liquidity filter. SIP requires an appropriate Alpaca subscription. Neither the
universe list nor offline tests verify your account's data access.

## Editable trading defaults

These optional root .env values seed the dashboard and trader until a runtime
settings file is saved. Exposure values here are fractions; the page shows percentages.

| Variable | Default |
| --- | --- |
| TRADER_MODEL | cross_sectional |
| TRADER_ENABLED | true |
| TRADER_MAX_DAILY_TRADES | 10 |
| TRADER_MIN_ORDER_NOTIONAL | 10 |
| TRADER_MAX_ORDER_NOTIONAL | null (no cap) |
| TRADER_MAX_TOTAL_EXPOSURE | 0.5 |
| TRADER_MAX_SECTOR_EXPOSURE | 0.2 |
| TRADER_MAX_POSITION | 0.1 |
| TRADER_TOP_N | 5 |
| TRADER_CONTROL_DIR | state/control locally; /app/control in Compose |

Use the dashboard for live changes to these controls, the universe, and the feed.
Cycle timing, retry settings, credentials, and state locations remain startup-only.

## Applying changes

Root .env changes require restarting local execution. Dashboard changes apply
before the next cycle without a restart; saved runtime controls override root .env.
For an existing Compose deployment, recreate the service to apply environment
changes:

```sh
docker compose up -d --force-recreate trader
```

A plain container restart does not load new environment values. Rebuild as well
when source code or dependencies change. See [operations](operations.md).

Strategy selection uses the model filename without .py. The dashboard shows
the name and explanation exported by that file; see [models](models.md). Saved
settings created before this key existed retain cross_sectional as the default.
