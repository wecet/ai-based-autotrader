# Strategy research and evaluation

The dashboard discovers three daily, long-only strategies. Only one is active;
installing a strategy does not select it or change saved risk settings.

| Model ID | Method | Interpretation |
| --- | --- | --- |
| `cross_sectional` | Original ridge regression on within-date five-session return ranks | Linear baseline, with the existing in-sample return calibration preserved |
| `momentum_rotation` | Positive 12-minus-1-month momentum above a 200-session moving average; inverse-volatility targets | Historical momentum score, not a return forecast |
| `boosted_ranker` | Histogram gradient-boosted return ranks with a later return-calibration window | Estimated five-session return, used only when its calibrated edge exceeds costs |

## Model boundaries

`trading/features.py` owns the shared 16 historical features and five-session
rank labels. `trading/estimators.py` provides ridge and boosted estimators.
`trading/ranking.py` provides the ranking lifecycle; the original model module
re-exports its research helpers for compatibility. Models remain independent
of brokers and expose the contract in [the model guide](models.md).

`trading/allocation.py` accepts typed `TargetSignal` objects and returns the
existing `TradeCandidate` contract. It reserves total and known benchmark-group
exposure for holdings without usable signals. Zero allocation weights request
exits for holdings with usable data; missing histories are retained. The new
models obey configured position, group, total and position-count limits. Unused
allocation capacity remains cash rather than forcing full investment.

`TradeCandidate.predicted_return` may be `None`. Momentum deliberately leaves
it unavailable: its buy/sell methods use eligible targets and minimum notional.
The original and boosted ranking decisions require a numeric return estimate.
`percentile_rank` expresses the cross-sectional ordering of the historical or
learned scores; tied scores receive the same average percentile.

Momentum needs at least 253 aligned sessions. It ranks the return from 252
sessions ago to 21 sessions ago, requires the latest close above its 200-session
average, and sizes positive trends by inverse 60-session daily volatility.
Both new models retain the existing price-above-$10 and average daily dollar
volume-above-$5-million eligibility filters. Neither lowers configured limits.

Boosting uses scikit-learn's `HistGradientBoostingRegressor` with deterministic
seed 0, 80 iterations, seven leaves, and random early stopping disabled. It fits
on the earliest 80% of labeled dates minus a five-signal-date purge, then
calibrates predicted ranks against returns on the later 20%. It needs at least
40 labeled sessions. A nonpositive calibration slope disables new allocations.
The calibration window is separate from estimator fitting; it is not the final
untouched research test period. Thread pools are bounded during fit/predict.

## Offline comparison

The research package never connects to a broker. To exercise it with fabricated
data, run from the repository root:

```sh
uv sync --locked --group dev
uv run python -m research --demo /tmp/strategy-demo.json
```

The synthetic example tests the pipeline and accounting, not profitability.
Compare a frozen real-data file with:

```sh
uv run python -m research /path/to/dataset.json /tmp/strategy-comparison.json
```

The JSON dataset contains `source`, `selection_notes`, `universe` (the usual
symbol-to-benchmark mapping), and `history` with `stocks`, `spy`, and `sectors`.
Each series is a chronological list of daily bars with ISO `date`, numeric
`open`, `high`, `low`, `close`, and `volume`. The loader rejects duplicate dates,
invalid prices and inconsistent OHLC. A file hash records the exact input.
Use split-adjusted prices consistently; the current ledger does not apply
separate split events or dividend distributions.

The API is `research.backtest.run_backtest(model_factory, history, settings,
config)`. A fresh model is fitted on completed bars before each preparation
cutoff. For five-session labels, the last usable training outcome is already
observed by that cutoff. Models never receive future evaluation bars.
Orders use a subsequent session's open plus configurable spread/slippage,
with fees, cash limits, configured order caps and budget, and reductions before
additions in symbol order. Participation capacity uses the previous completed
session's volume; constrained orders can partially fill. Missing execution
bars prevent fills; missing end-of-day prices carry the last mark and are
reported. An injectable `order_allowed` policy supports deterministic rejection
scenarios; rejected simulated orders consume an attempt.

By default the final 20 sessions are excluded from all development comparisons,
including stress runs. Set `RESEARCH_FINAL_TEST=true` only after fixing the
hypothesis and parameters; this evaluates that reserved final interval. Once
inspected, a period cannot be treated as untouched for subsequent tuning.
Walk-forward refitting within an evaluation interval uses only past outcomes.
Record all experiments and failed hypotheses rather than selecting a winner
from repeated tests on the final interval.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `RESEARCH_WARMUP_SESSIONS` | `260` | Completed sessions before development evaluation |
| `RESEARCH_REBALANCE_SESSIONS` | `1` | Sessions between model preparations; targets are evaluated each session |
| `RESEARCH_FINAL_TEST_SESSIONS` | `20` | Number of sessions reserved from development runs |
| `RESEARCH_FINAL_TEST` | `false` | Explicitly evaluate the reserved final interval |
| `RESEARCH_SPREAD_BPS` | `5` | Per-side spread impact assumption |
| `RESEARCH_SLIPPAGE_BPS` | `5` | Per-side slippage assumption |
| `RESEARCH_FEE_BPS` | `0` | Per-side fee assumption |
| `RESEARCH_EXPERIMENT` | `baselines` | `baselines` compares shipped strategies; `allocation` compares sizing methods |
| `RESEARCH_TRIAL_LOG` | `research-trials.jsonl` beside the output | Append-only record of every run, including failures |

The CLI uses `TradingSettings` defaults and any exported `TRADER_*` overrides,
with the dataset's universe. It does not load broker credentials or modify
runtime controls. The Python API accepts an explicit settings object and
`BacktestConfig`, including initial cash, execution delay and volume participation.

Reports include net and benchmark-relative price returns, drawdown, annualized
volatility and Sharpe with a zero cash rate, worst-session return, turnover,
exposure, execution costs, fills, daily cash/positions/equity, and abstention
or rejection events. Annualization uses 252 sessions. Every CLI comparison
also reruns double spread/slippage and one-session delay on the same evaluation
dates. Input failures and training abstentions remain visible in the report.

Reports also include 5% expected shortfall, return skewness, and returns grouped
by regimes known at the prior close: SPY above or below its 200-session average,
and 20-session SPY volatility above or below its trailing 252-session median.
Sessions without enough history are labeled `unknown`. Regime labels are
reporting only and are never passed to a model.

Every report carries the experiment's pre-registered protocol (hypothesis,
universe, information cutoff, horizon, benchmark and rejection criteria) from
`research/protocols.py`. Change a protocol in a reviewed commit before tuning,
not after inspecting results. Each invocation appends a line to the trial log
with the dataset hash, configuration, mode and status, so failed and repeated
trials stay visible.

## Allocation comparison

`RESEARCH_EXPERIMENT=allocation` holds momentum and boosted-ranking signals fixed
and changes only target sizes, using interchangeable functions in
`trading/allocation.py`:

| Method | Sizing of the selected `top_n` signals |
| --- | --- |
| `capped` | Each selected symbol requests the maximum position (boosted ranking's shipped behavior) |
| `equal` | Equal shares of the remaining total exposure budget |
| `inverse_volatility` | Shares proportional to 1/σ of 60 trailing daily returns (momentum's shipped behavior) |
| `hierarchical_risk` | Hierarchical risk parity: single-linkage clustering on correlation distance √((1−ρ)/2), then recursive variance bisection |

Selection, eligibility, exits and buy/sell decisions are delegated unchanged to
the base model. Every method passes through `allocate_targets`, so retained
holdings, position, group, total and count limits still apply and unused room
stays cash; no method adds leverage. Selected symbols without a usable 60-session
return window receive no target rather than an invented risk estimate.

Each method runs at base and double costs. Reports add the mean summed absolute
target-weight change between rebalances, the mean change in pairwise holding
correlations (covariance stability), mean holding correlation, largest group
weight, and maximum total target. Datasets carry no ETF constituents, so holding
correlation and group concentration stand in for constituent overlap.

When `top_n × maximum_position` does not exceed `maximum_total_exposure`, as with
the defaults (5 × 10% vs 50%), every proportional method is capped at the maximum
position and all methods produce identical targets. Differences only appear when
the position cap leaves room, for example with more names or a higher cap.

Daily opens approximate executions: they cannot reproduce one-order-per-cycle
intraday timing, actual broker rejection rates, or live market impact. Results
use a static universe, omit dividends/taxes/cash interest, and are not corrected
for survivorship bias. The SPY comparison is an unlevered price-return benchmark,
while strategies remain within configured exposure caps. These limitations
prevent treating a successful synthetic run as investment evidence.

## Follow-up research

The next stages are tracked in GitHub:

| Issue | Scope |
| --- | --- |
| [#2](https://github.com/wecet/ai-based-autotrader/issues/2) | Point-in-time universes, longer histories, corporate actions and total returns |
| [#3](https://github.com/wecet/ai-based-autotrader/issues/3) | Long-only residual mean reversion |
| [#4](https://github.com/wecet/ai-based-autotrader/issues/4) | ML filtering of momentum opportunities |
| [#5](https://github.com/wecet/ai-based-autotrader/issues/5) | Causal regime adaptation |
| [#6](https://github.com/wecet/ai-based-autotrader/issues/6) | Compact temporal neural networks |
| [#7](https://github.com/wecet/ai-based-autotrader/issues/7) | Volatility and hierarchical risk allocation comparisons |
| [#8](https://github.com/wecet/ai-based-autotrader/issues/8) | Ensembles and broker-free shadow comparisons |

Research follows the repository's [quant-research](../libs/skills/skills/algorithmic-trading/quant-research/SKILL.md),
[backtest-validation](../libs/skills/skills/algorithmic-trading/backtest-validation/SKILL.md),
and [trading-systems](../libs/skills/skills/algorithmic-trading/trading-systems/SKILL.md)
skills. Submodules are optional for runtime; see [their setup guide](../libs/README.md).
