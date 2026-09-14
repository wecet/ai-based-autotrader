# Candidate universe

The expanded selection prepared on 2026-09-14 contains **150 distinct
candidates: 50 equity ETFs and 100 individual stocks**. It is a static research
selection, not an automatically maintained index or a guarantee that 150 symbols
will pass the model's filters. The runtime uses whatever JSON is present in
`TRADER_UNIVERSE`; it does not enforce these counts or fetch the selection list.

## ETF selection

“Top 50” means the 50 US-listed **equity** ETFs by reported assets under management
in the [selection source](https://myetf.app/market/us/asset-class/equity) at the
time of preparation. It excludes other asset classes and does not mean the
best-performing funds. The snapshot's symbols, in source order, were:

```text
VOO IVV SPY VTI QQQ VEA VUG IEFA VTV SPYM
VXUS IEMG VGT VWO IWF IJH XLK VIG SCHD VO
IJR QQQM RSP ITOT IWD VYM VT VB EFA IWM
IVW SCHX SMH VEU SCHF SCHG IXUS IWR VV XLF
SPYG IVE DFAC IWB QUAL VONG JEPI DIA SOXX SCHB
```

Rankings and tickers can change. Updating this snapshot is a deliberate
configuration change, not something the service does at startup.

## Individual stocks

The stock selection used a reproducible, sector-stratified random sample from a
curated pool of established companies, with Python seed `20260914`. It was not
a random sample of every listed stock and did not apply an analyst-upside,
valuation, or positive-expected-return screen. The model decides whether an
eligible candidate offers a trade under its own rules.

| Sector | Benchmark | Stock count |
| --- | --- | ---: |
| Information technology | XLK | 20 |
| Financials | XLF | 15 |
| Health care | XLV | 15 |
| Industrials | XLI | 15 |
| Consumer discretionary | XLY | 10 |
| Consumer staples | XLP | 8 |
| Communication services | XLC | 5 |
| Energy | XLE | 4 |
| Materials | XLB | 3 |
| Utilities | XLU | 3 |
| Real estate | XLRE | 2 |

The seed alone is insufficient to recreate the exact sample without its original
pool and ordering. Preserve the actual JSON for reproducibility. Sector labels
were informed by the [S&P 500 component reference](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies).

## ETF benchmark groups

The model uses each mapping value both for relative-momentum features and as an
exposure group. ETF mappings are deliberately coarse:

| Candidates | Proxy |
| --- | --- |
| Broad US, style, size, dividend, and remaining equity funds | SPY |
| VEA, IEFA, VXUS, EFA, VEU, SCHF, IXUS | EFA |
| IEMG, VWO | EEM |
| VGT, XLK, SMH, SOXX | XLK |
| XLF | XLF |
| VT | VT |

A fund mapped to itself has zero sector-relative momentum. Broad funds can
share underlying holdings with each other and with individual stocks. The
20% group limit does not measure underlying sector exposure across fund holdings.
Changing a proxy changes both the feature and the allocation group.

## Eligibility and practical limits

All candidates, including ETFs, go through the same daily-bar pipeline. Their
sessions and their proxy's sessions must exactly match SPY's returned history.
Recent listings, ticker changes, missing data, low prices, and insufficient
venue volume can exclude candidates. Before an order, Alpaca must report the
asset as tradable and fractionable.

150 candidates do not mean 150 positions. The model selects at most five target
positions and the runner attempts at most 10 orders per day. Existing holdings
outside the eligible universe are retained and reduce the new allocation budget.
See [trading behavior](trading.md) for the exact limits.
