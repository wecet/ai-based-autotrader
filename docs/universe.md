# Candidate universe

The versioned [candidate catalog](../src/configuration/universe.json), dated
2026-10-19, contains **230 research candidates**:

| Category | Count | Examples |
| --- | ---: | --- |
| Established stocks | 136 | AAPL, NVDA, ASML, GOOGL, MSFT, AMZN, META, TSM, JPM, COST, LLY |
| Specialist / less-followed stocks | 29 | ACLS, AEIS, CAMT, FORM, ONTO, SITM, AVAV, BWXT, CACI, HALO, IRDM |
| Equity ETFs | 58 | SPY, VOO, VTI, QQQ, IWM, SMH, SOXX, XBI, IHI, ITA, ICLN, GDX |
| Commodity funds and trusts | 7 | GLD, IAU, SLV, PDBC, DBC, DBA, USO |

These categories describe the curated research mix, not objective market-cap or
obscurity thresholds. They do not prescribe portfolio weights or guarantee
selection by a strategy. The catalog extends the previous 150-symbol selection
by 80 candidates and replaces the obsolete MMC ticker with MRSH. Alphabet is
represented by GOOGL; GOOG is not additionally included to avoid deliberately
doubling the company's share classes.

All current catalog symbols were checked against Alpaca's active, tradable,
fractionable US-equity asset catalog on 2026-10-19. Availability can change;
execution checks remain authoritative. Marsh's [ticker change announcement](https://www.marsh.com/en/corp/about/news/marsh-mclennan-to-change-nyse-symbol-to-mrsh.html)
confirms that MRSH replaced MMC on January 14, 2026. The merge migrates that known
rename while preserving existing benchmark choices. Historical ticker continuity
still depends on the data provider and is not repaired by the catalog.

## Apply or customize

The runtime uses saved dashboard settings, or initially `TRADER_UNIVERSE` from
`.env`. The catalog is an explicit configuration resource; startup does not
silently replace a custom universe.

Preview the catalog or merge it into your local configuration:

```sh
uv run python -m configuration.universe --show
uv run python -m configuration.universe --apply
```

Run `--apply` from the project root. It adds catalog candidates, keeps custom
symbols and existing benchmark mappings, applies known ticker renames, and
updates only `TRADER_UNIVERSE` in `.env`. Credentials and other settings are
preserved. If the configured local `TRADER_CONTROL_DIR/runtime.env` exists,
it also updates its universe through the revision-checked settings store.
Model selection, enabled/paused state, risk limits and order journal stay intact.

Recreate both Compose services after changing the root environment:

```sh
docker compose up -d --build trader dashboard
```

A saved runtime file in the Docker control volume takes precedence over root
`.env`. To merge the same catalog into an existing container's saved controls,
run this after rebuilding (it makes no broker requests):

```sh
docker compose exec -T trader python -c 'import os; from pathlib import Path; from configuration.settings import LiveSettingsStore, TradingSettings; from configuration.universe import merge_candidates; store = LiveSettingsStore(Path(os.environ["TRADER_CONTROL_DIR"])); current = store.read(); updated = TradingSettings.model_validate({**current.settings.model_dump(), "universe": merge_candidates(current.settings.universe)}); store.save(updated, current.revision)'
```

Alternatively, edit the universe in the [dashboard](dashboard.md). Saving a
changed universe invalidates the model's cached daily preparation.

## Benchmarks and overlap

Each catalog value is both a relative-feature benchmark and an allocation group.
Individual stocks use sector ETFs such as XLK, XLF, XLV and XLI. Broad US equity
funds generally use SPY, international funds use EFA or EEM, technology funds use
XLK, and specialist equity funds use their stated sector or thematic benchmark.
The checked-in JSON is the authoritative mapping.

Commodity funds use underlying-exposure groups: GLD/IAU share GLD; PDBC/DBC
share PDBC; SLV, DBA and USO use themselves. A self-benchmarked fund has zero
benchmark-relative momentum. Gold and silver trusts provide commodity exposure
through listed securities; futures-based funds also reflect roll effects and
fund expenses. These are not direct commodity or futures contracts. See the
[issuer's commodity fund overview](https://www.ishares.com/us/products/commodity-etps).

Multiple ETFs can hold the same stocks, and fund candidates overlap individual
stock candidates. A benchmark-group cap is not a look-through sector or issuer
cap. Catalog balance does not imply that the resulting portfolio is balanced
across categories; that would require a separately researched allocation policy.

## Eligibility and research limits

Candidates and their proxies must have completed sessions aligned with SPY.
Recent listings, ticker changes, missing data, low prices and low venue volume
can exclude candidates. The new strategies preserve price-above-$10 and average
daily dollar-volume-above-$5-million filters. IEX volume covers that venue and
can exclude otherwise active securities. Each submitted asset must still be
tradable and fractionable according to Alpaca.

A list of 230 candidates does not mean 230 holdings. By default, configured
limits allow five targets and ten daily order attempts. The original model
remains selected until explicitly changed. Existing holdings outside usable
histories are retained and consume exposure budget. See [trading behavior](trading.md)
and [strategy research](research.md).

This catalog is a static present-day research selection. Backtesting it through
older dates introduces survivorship and selection bias. The daily data provider
currently supplies split-adjusted prices rather than a complete total-return
ledger. Point-in-time membership, delisted securities and corporate-action-aware
research are tracked in [issue #2](https://github.com/wecet/ai-based-autotrader/issues/2).
