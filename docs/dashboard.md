# Live settings dashboard

The **Control room** is a small web app in the same Compose project as the trader.
It edits a validated set of trading controls without restarting the trading
process. It does not log in to Alpaca or submit orders itself.

## Start and unlock

Keep your existing root `.env`, including `TRADER_UNIVERSE` and paper credentials.
Build and start the group:

```sh
docker compose up -d --build
```

Open [http://localhost:8080](http://localhost:8080). Retrieve the dashboard's
separate access key in your terminal:

```sh
docker compose exec dashboard cat /app/control/dashboard.key
```

Paste that key into the unlock form. Do not use an Alpaca API key. The key is
created on first dashboard startup and retained in the control volume with
owner-only file permissions. It is never returned by the HTTP API or logged.
The page keeps the key in memory; refreshing or locking requires unlocking again.

To start only the dashboard without starting broker trading:

```sh
docker compose up -d --build dashboard
```

A dashboard alone can save settings, but they remain pending until a trader
running the updated code picks them up. For an existing installation, recreate
both services with the rebuilt image to enable live configuration.

The port is published on host loopback only. For a remote Docker host, use an SSH
tunnel and open localhost on your own machine:

```sh
ssh -N -L 8080:127.0.0.1:8080 user@your-server
```

The current app is intended for local/tunneled administration. Arbitrary Host
headers and cross-origin API requests are rejected; it is not configured as a
public multi-user website.

## What you can change

| Control | Default | Validation / behavior |
| --- | --- | --- |
| Active strategy | Cross-sectional ranking | Exactly one discovered model, named and described by its source file |
| Allow new orders | On | Pause keeps the loop alive; it does not cancel existing orders |
| Daily order limit | 10 | Integer 0–1000; 0 prevents new attempts |
| Minimum order amount | $10 | $1–$1,000,000, at most two decimal places |
| Maximum order amount | No cap | Optional; same bounds, at least the minimum |
| Total target exposure | 50% | Greater than 0%, at most 100% |
| Benchmark-group target | 20% | No larger than the total target |
| Single-position target | 10% | No larger than the benchmark-group target |
| Target positions | 5 | Integer 1–100 |
| Universe | From `.env` initially | 2–500 unique candidate-to-benchmark mappings |
| Data feed | IEX | IEX or SIP; SIP still needs the account entitlement |

The universe supports inline editing, search, adding/removing rows, and bulk
JSON editing. The bulk editor updates a draft; **Save changes** commits it.
**Discard changes** loads the latest saved version. If another session saves
while you are editing, the app rejects a stale save rather than overwriting it.

Order caps apply to both buys and sells. The executor limits the desired dollar
amount to the maximum, rounds down to cents, and skips amounts below the minimum.
The remaining target is not automatically split into further orders: the same
symbol/side/target fingerprint is still attempted only once that day. A cap can
therefore leave a position short of its target, including a partial reduction.

## Performance calendar

After unlocking, choose **Performance calendar**. Green dates show a gain, red
dates show a loss, and neutral dates indicate flat P/L or unavailable history.
Select any past or current date to open its net account P/L and completed trade
fills, including execution time, symbol, side, quantity, price, and value.
Partial fills appear individually. Pending, rejected, and cancelled unfilled
orders do not appear as trades.

The total above the calendar defaults to **All time**. Switch between:

- **24hr**: rolling 24-hour P/L using five-minute broker history.
- **1 week**: the last seven New York calendar days, including today.
- **1 month**: the last 30 New York calendar days, including today.
- **All time**: all available broker portfolio history.

P/L uses the paper account's portfolio history, including changes in open
positions; execution values are not treated as profits. The report covers the
whole account, including trades outside this bot. Dates use America/New_York,
consistent with the order budget. Missing history displays an unavailable value
rather than assuming zero.

The trader syncs read-only Alpaca portfolio history and paginated fill activity
into `state/performance.json` at most once every five minutes, including while
paused or the market is closed. The dashboard reads that file without broker
credentials. The page refreshes its cached report every minute while open; its
**Refresh** button rereads the cache. The last sync time is shown, with a stale
notice after ten minutes. Sync failures retain the previous valid report and
are logged without interrupting order execution. A new deployment must rebuild
both services before history becomes available.

Portfolio calculations follow [Alpaca's portfolio history API](https://docs.alpaca.markets/us/reference/getaccountportfoliohistory-1);
fill pagination follows [Alpaca's account activities API](https://docs.alpaca.markets/us/docs/account-activities).

## How realtime updates work

The root `.env` supplies startup defaults. Saving in the dashboard writes only
allowlisted settings to **`/app/control/runtime.env`** in the `trader-control`
volume. It does not rewrite the host's root `.env` or expose its credentials.
Docker does not dynamically update running process environments when `.env`
changes; the trader explicitly reads this runtime settings file instead.

Before each cycle, the trader reads one complete validated settings snapshot.
Changed model, universe, or feed settings invalidate its cached daily ranking and cause
a refresh on the next eligible trading cycle. Other limits update without
retraining. The default cycle wait is 60 seconds, plus time spent in the preceding
cycle. A save does not interrupt an order or cycle already underway.

The page refreshes status every five seconds. **Pending** means the saved version
has not been acknowledged by a healthy trader. **Applied** means the trader
loaded that revision and is reporting a healthy heartbeat; it does not prove
that a new universe has sufficient data or that an order filled. Idle cycles,
including a pause or closed market, can be healthy.

Changing the daily limit never clears `orders.json`. If six attempts are already
recorded and you lower the limit to four, no more orders are allowed that day.
Raising it to eight leaves room for two further attempts, subject to all other
checks. Settings changes do not alter existing broker orders.

## Persistence and precedence

Once saved, `runtime.env` contains the full allowlisted configuration and takes
precedence over root `.env` values for those controls, including after restarts.
Use the page to edit them thereafter. The **Download runtime .env** action
exports just these settings for backup or review; it excludes access keys and
broker credentials.

The dashboard has read-only access to `trader-state` and cannot reset the order
journal through the API. The shared `trader-control` volume holds the runtime
configuration, writer lock, and dashboard key. Saves use a lock, atomic file
replacement, and revision checks. The trader continues using its previous
snapshot for any in-progress cycle. A malformed runtime file causes subsequent
cycles to fail instead of silently returning to potentially looser defaults.

Preserve both volumes during deployment. Deleting control state loses settings
and rotates the dashboard key; deleting trading state loses the daily order
count. Never use volume deletion as a way to apply configuration changes.

## API and development

The API is protected by `Authorization: Bearer <dashboard-key>`:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/settings` | Current allowlisted values, revision, and runner status |
| `PUT /api/settings` | Save `{ "revision": "…", "settings": { … } }` |
| `GET /api/models` | Available model names, descriptions, IDs and load errors |
| `GET /api/performance` | Cached account P/L, daily breakdown and completed fills; 503 until available |
| `GET /api/status` | Acknowledgement, heartbeat state, and today's attempt count |
| `GET /api/export` | Download the allowlisted runtime `.env` |
| `GET /healthz` | Dashboard health check, without authentication |

The page, stylesheet, and JavaScript are served locally; they make no external
asset requests. The API never returns the root environment, account credentials,
or raw journal contents. Server-side validation rejects unknown fields,
non-finite numbers, invalid bounds, and duplicate normalized ticker symbols.

Run locally in a separate terminal from the trader:

```sh
uv sync --group dev
PYTHONPATH=src uv run python -m dashboard
```

Use the same `TRADER_STATE_DIR` and `TRADER_CONTROL_DIR` for both processes.
Locally the default control directory is `state/control`; retrieve its
`dashboard.key` file locally instead of using the container command.

The implementation is split between `configuration/settings.py` (schema and persistence),
`dashboard/app.py` (API), and `dashboard/static/` (UI). The trader reloads settings
and passes them to `DailySignals` and `OrderExecutor`. API and execution tests
cover saves, conflicts, authentication, pause/resume, order caps, cache invalidation,
and daily-budget preservation without contacting the broker.

See [drop-in strategies](models.md) for the source-file contract. New model
files require rebuilding both containers; the checkbox switches existing models
at runtime. The default strategy remains cross-sectional ranking.
