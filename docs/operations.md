# Deployment and operations

## Build and start

The trader container is named `trader` and uses image `trader:latest`.
The dashboard container is named `dashboard` and uses image
`trader-dashboard:latest`. These explicit container names must be unique on the
Docker host; the persistent volumes remain scoped to the Compose project.

Configure the root `.env` using [configuration](configuration.md). Docker Compose
uses [docker-compose.yml](../docker-compose.yml) and
[trader.dockerfile](../build_files/trader.dockerfile):

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 -f trader
```

The image uses Python 3.13 and dependencies from `uv.lock`, excluding development
dependencies. It runs as UID/GID 10001 with a read-only root filesystem, writable
state volume and temporary directory, dropped capabilities, and an init process.
The trader exposes no ports. The companion dashboard publishes port 8080 only
on host loopback; see [dashboard access](dashboard.md). Logs rotate at 10 MB with up to three files.

## Stop, resume, and update

| Action | Command |
| --- | --- |
| Stop intentionally | `docker compose stop trader` |
| Resume | `docker compose up -d trader` |
| Apply changed `.env` | `docker compose up -d --force-recreate trader` |
| Apply source/dependency changes | `docker compose up -d --build trader dashboard` |
| Open control room | http://localhost:8080 |
| Check heartbeat now | `docker compose exec trader python src/main.py --healthcheck` |

Run one instance per paper account. A file lock prevents concurrent processes
sharing the same state directory, but does not coordinate different volumes or
other broker clients.

## Health and recovery

After a successful cycle, the application writes a healthy heartbeat. It
refreshes that heartbeat during waits in chunks of at most 30 seconds. Closed
markets, pending orders, a used daily budget, and no approved trade all count as
successful idle cycles. Health therefore indicates recent runner progress, not
that orders have filled or every broker endpoint was checked.

The health command exits 0 only when the heartbeat is healthy and between 0 and
180 seconds old. Missing, malformed, stale, or unhealthy state yields exit 1.
It does not contact Alpaca or load `.env`; locally, export `TRADER_STATE_DIR`
when checking a custom state location.

Docker checks every 30 seconds, with a five-second check timeout, a 60-second
startup grace period, and three failed checks before marking the container
unhealthy. An unhealthy status alone does not restart it. The application exits
after three consecutive ordinary cycle failures by default, or immediately
when its 120-second cycle deadline interrupts a cycle. Successful cycles reset
the failure counter. Initialization and login are also inside the deadline.

Compose's `unless-stopped` policy restarts an exited container unless explicitly
stopped. The host and Docker must remain available; host sleep or loss of network
can interrupt service. SIGTERM/SIGINT requests a stop, interrupting waits; an
in-progress cycle may continue until completion or deadline. Compose allows
30 seconds before forcibly terminating a stuck shutdown.

## Persistent state

The dashboard writes editable controls to a separate trader-control volume.
The trader records its last applied revision in applied-settings.json in the
state volume. Preserve both volumes; the dashboard mounts trading state read-only.

Compose stores these files in its project-scoped `trader-state` named volume:

| File | Purpose |
| --- | --- |
| `orders.json` | Current recorded day's attempted target fingerprints |
| `heartbeat.json` | Recent runner health; removed on normal loop exit |
| `applied-settings.json` | Last settings revision loaded by the trader |
| `performance.json` | Cached account P/L, daily totals and completed fills for the calendar |
| `runner.lock` | Exclusive process lock for this state directory |

The journal is written through a temporary file and replacement. Preserve the
volume across updates and restarts. Removing it, using `docker compose down -v`,
or changing the Compose project name can give the service an empty journal and
lose its daily count. Stop the service before backing up state. Restoring an
older journal can omit attempts made afterward; reconcile those against the
broker before resuming. Do not clear a corrupt journal just to silence an error.

Performance reporting uses the existing trader credentials and writes its report
atomically. It runs at most once every five minutes, even when trading is paused
or the market is closed. The dashboard polls the report every minute while its
calendar is open and flags reports older than ten minutes. A healthy heartbeat
can coexist with a stale report: ordinary reporting failures are logged and
retain the last valid cache. Reporting runs within the existing cycle deadline.
See [the calendar guide](dashboard.md#performance-calendar) for range definitions
and account-wide P/L semantics.

## Troubleshooting

| Log or symptom | Meaning and next check |
| --- | --- |
| Credentials missing / login failed | Check matching paper credentials in the service environment |
| Universe configuration error | Check JSON syntax, string values, and at least two candidates |
| Market closed | Normal waiting state; the broker clock controls trading |
| Waiting for open orders | Check all account orders, including manually placed ones |
| No new model-approved trade | Costs, targets, duplicate suppression, asset eligibility, or buying power may exclude orders |
| Daily limit reached | Wait for the next broker date or adjust the budget in the dashboard; do not reset the journal |
| Insufficient/stale history | Check feed access, ticker history, and exact session alignment with SPY |
| Insufficient liquid stocks | IEX venue volume may fail the $5 million filter; review feed and candidates |
| Cycle deadline exceeded | Check broker latency and training cost for the configured universe |
| Invalid order state | Preserve the journal and reconcile with broker orders before repair |
| Calendar unavailable / API 503 | Rebuild both services, start the trader, and check its logs for `Performance refresh failed`; the cache must exist and validate |
| Calendar history out of date | Check the displayed sync time and trader logs; Refresh rereads the cache and does not force a broker sync |
| Unhealthy / restarting | Inspect the preceding cycle exception in logs, not only container status |

The 150-symbol universe has not been established as a successful live-data
workload merely by passing offline tests. Download and training time depend on
broker access and the host.
