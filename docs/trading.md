# Trading behavior

## One cycle

The runner performs these checks sequentially:

1. Read Alpaca's clock; wait if the market is closed.
2. Read the persisted journal for the New York date; wait if 10 attempts are used.
3. Wait if the account has any open orders, including orders from other clients.
4. Check account status, positive finite equity, and finite long-only holdings.
5. Refresh the model if this is the first eligible cycle of the day.
6. Construct target weights using current holdings and account equity.
7. Consider reductions first, then additions; within each group, use symbol order.
8. Submit at most one eligible order, then wait before checking the broker again.

## Features and training

On refresh, the runner requests 730 calendar days of split-adjusted daily OHLCV
bars for candidates, proxies, and SPY. It excludes the current New York day's
incomplete bar. SPY needs at least 206 bars, with its latest bar no more than
four calendar days old. Candidate and proxy date lists must exactly match SPY.

The 16 features cover returns over 1/5/20/60 sessions, moving-average distances,
RSI, volatility, volume ratio, overnight gap, market beta, relative momentum,
distance from a recent high, and SPY trend/volatility. Features use data available
at the feature date. Rows require price strictly above $10 and average 20-session
dollar volume strictly above $5 million, with finite feature values.

Training labels rank realized five-session returns within each date. The NumPy
ridge estimator learns these ranks, then a linear calibration maps predicted
ranks to returns. The production runner fits all available labeled rows; the
separate chronological-split helper is used for research/tests, not a production
holdout or performance gate. There is no trained-model artifact persisted across
restarts.

Signals and cached ranking refresh once per day, or after process restart.
Polling more often refreshes holdings and buying power when guards allow, but
does not create intraday predictive signals. Trade notional is based on changes
in target weight times current account equity.

## Allocation and decisions

These are current code defaults, not environment settings:

| Rule | Value |
| --- | --- |
| Maximum selected targets | 5 |
| Maximum target per position | 10% |
| Maximum target per benchmark group | 20% |
| Maximum total target exposure | 50% |
| Minimum order notional | $10 |
| Estimated spread | 0.05% |
| Estimated slippage | 0.05% |
| Safety margin | 0.20% |

A new target needs predicted return strictly greater than the combined 0.30%
cost-and-margin estimate. These costs are fixed estimates, not live quotes.
A buy also requires an increase in desired weight and sufficient buying power.
A sell requires a reduction in desired weight and the minimum notional; it does
not require positive predicted return. Orders are DAY market orders in dollar
notional, rounded to cents, for tradable and fractionable assets.

Holdings missing from the ranked universe are not automatically liquidated.
Their weights reduce the 50% budget available to eligible targets. Target limits
are not continuous guarantees about actual exposure: market changes, pending or
failed orders, and retained holdings can leave a different allocation. There is
no explicit stop-loss order or underlying-ETF-holdings exposure calculation.

## Daily limit and duplicate prevention

The runner records a symbol/side/target-weight fingerprint **before** submitting
the broker request. A deterministic client order ID accompanies the order.
Rejected orders and ambiguous submission timeouts consume an attempt, and the
same fingerprint is not retried that day. This favors avoiding duplicate orders
over automatically completing an uncertain submission.

The journal caps this service at 10 attempts per New York calendar date during
open-market cycles. A newer broker date starts a new logical budget. Corrupt
state or a journal date ahead of the broker clock causes an error rather than
silently resetting the budget. The journal is not a fill ledger, and other
programs' submissions do not count toward this cap.

Several trades per day are possible, but the service does not promise to trade
or use all 10 slots. A changed target can produce a different fingerprint; an
unchanged one remains suppressed even if its earlier order failed.
