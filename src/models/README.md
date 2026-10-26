# Trading models

This directory contains the discoverable strategies. Each model prepares signals
from completed daily bars, constructs desired portfolio weights, and approves
buy/sell decisions. Broker access, order submission, account credentials and the
persistent order journal belong to the shared trading infrastructure.

| Module / model ID | Dashboard name | Method | Signal horizon |
| --- | --- | --- | --- |
| [cross_sectional.py](cross_sectional.py) | Cross-sectional ranking | Linear ridge regression on return ranks | Five-session forward return |
| [momentum_rotation.py](momentum_rotation.py) | Momentum rotation | Historical trend ranking with inverse-volatility sizing | Approximately 12 months, excluding the latest month |
| [boosted_ranker.py](boosted_ranker.py) | Boosted return ranking | Nonlinear boosted-tree rank regression with chronological return calibration | Five-session forward return |
| [meta_momentum.py](meta_momentum.py) | Meta-labelled momentum | Momentum rule entries filtered by a logistic classifier of past rule outcomes | Approximately 12 months; 21-session labels |
| [regime_composite.py](regime_composite.py) | Regime-adaptive composite | Forward-filtered trend/range/stress regimes select momentum or reversion; stress scales exposure down | Daily regime; component horizons |
| [residual_reversion.py](residual_reversion.py) | Residual mean reversion | Long-only entries on standardized five-session residuals versus SPY and the group benchmark | Up to ten sessions, with explicit exits |
| [temporal_network.py](temporal_network.py) | Temporal network ranking | Compact PyTorch causal TCN on 32-session return/volume sequences with purged return calibration | Five-session forward return |
| [_template.py](_template.py) | Not selectable | Abstract scaffold for another strategy | Defined by its implementation |

The prediction horizon is not a mandatory holding period. Targets are evaluated
repeatedly, and holdings can be reduced whenever the selected model changes its
targets. There is no fixed five-day exit or model-owned stop-loss order.

## Shared lifecycle and data

[DailySignals](../trading/signals.py) calls `prepare(history, universe)` once per
eligible New York trading date. Subsequent cycles reuse that prepared model but
recalculate candidates using current holdings and account equity. Changing the
selected model, universe or data feed creates a new instance and invalidates
preparation. Other configuration changes update the existing model's controls.
Models and fitted estimators are held in memory; restart or switching requires
fresh preparation. No trained artifact is persisted by these implementations.

The [data provider](../data/historical.py) requests approximately 730 calendar
days of split-adjusted daily OHLCV for candidates, their benchmark ETFs and SPY.
It excludes the current New York date, requires recent SPY history, and admits
candidates only when their sessions and their benchmark's sessions exactly align
with SPY. A benchmark mapping also defines an allocation group; it is not a
look-through analysis of an ETF's holdings.

All three strategies use these eligibility rules:

- Latest closing price strictly above $10.
- Mean daily dollar volume over the latest 20 sessions strictly above $5 million.
- Sufficient completed history and usable numerical inputs.

With IEX, volume is venue-specific. Passing these filters does not establish
that Alpaca will accept an order: the executor separately checks tradability,
fractionability, minimum/maximum notional, buying power, daily budget and
same-day duplicate targets.

Preparation errors propagate to the live service's normal failure handling.
The offline harness records `ValueError` preparation failures as abstentions.
A valid model with no attractive targets can instead remain in cash without a
preparation error.

## Cross-sectional ranking: linear baseline

[cross_sectional.py](cross_sectional.py) is a thin discoverable wrapper around
[RankingModel](../trading/ranking.py). It preserves imports of the original
feature and estimator helpers for existing research callers.

### Features

Both ranking models use [trading/features.py](../trading/features.py). Features
for a historical date use only bars available through that date.

| Feature | Calculation / interpretation |
| --- | --- |
| `return_1d`, `return_5d`, `return_20d`, `return_60d` | Closing-price returns over the stated sessions |
| `distance_from_ma20`, `distance_from_ma50`, `distance_from_ma200` | Latest close divided by the trailing mean close, minus one |
| `rsi_14` | RSI from simple mean gains and losses over 14 changes; not Wilder's recursive smoothing |
| `volatility_20d` | Sample standard deviation of daily returns, annualized by `sqrt(252)` |
| `volume_ratio_20d` | Latest volume divided by mean volume over 20 sessions |
| `overnight_gap` | Latest completed bar's open divided by its preceding close, minus one |
| `market_beta_60d` | Sample covariance with SPY divided by SPY return variance over 60 changes |
| `sector_relative_momentum_20d` | Candidate's 20-session return minus its mapped benchmark's return |
| `distance_from_high_60d` | Latest close divided by the highest high over 60 sessions, minus one |
| `spy_trend_20d` | SPY's 20-session closing-price return |
| `spy_volatility_20d` | SPY's annualized sample volatility over 20 changes |

There are 16 numerical inputs in total. A latest feature row needs at least
201 candidate bars. Five-session labels need five additional observed sessions,
so an earliest labeled row requires at least 206 bars. The shared preparation
path also requires at least two eligible candidates in the latest cross-section.

### Labels, fitting and return estimates

For each usable historical date `t`, the training pipeline computes:

```text
forward_return(t) = close(t + 5 sessions) / close(t) - 1
```

It orders candidates by that outcome within the same date and assigns labels
from 0 (lowest) to 1 (highest). These are relative ranks, not probabilities.
Historical label ties currently follow input order rather than receiving an
average rank. Only dates whose future outcome is already observed enter fitting.

[RidgeRanker](../trading/estimators.py) standardizes each input using the fitting
sample's mean and standard deviation. A constant feature uses scale 1. It fits
an intercept and linear coefficients with an L2 penalty of 1.0 by default; the
intercept is not penalized.

The baseline then regresses actual five-session returns on its fitted rank
predictions to obtain an intercept `a` and slope `b`:

```text
estimated_return = a + b * predicted_rank
```

This calibration uses the same rows that fitted the ridge estimator. It is an
in-sample conversion preserved for baseline compatibility, not independent
validation. Predicted ranks and return estimates are not clipped to [0, 1].
Latest candidates are ordered by predicted rank, even if calibration gives a
negative slope; the baseline does not apply the boosted model's slope guard.

### Targets and trade decisions

The baseline walks the rank ordering and selects candidates whose estimated
return strictly exceeds the fixed cost hurdle:

```text
0.05% estimated spread + 0.05% estimated slippage + 0.20% safety margin = 0.30%
```

It assigns each selected symbol as much as its configured position cap permits,
then caps that amount by remaining group and total exposure. This is sequential
capped allocation, not inverse-volatility sizing or equal-risk allocation.
Selection stops at `top_n` targets or exhaustion of available total exposure.

An eligible holding that no longer receives a target gets a zero target. A
holding without a usable current feature row is retained. Retained weights
reduce the baseline's available total exposure; its legacy allocator does not
reserve their known group exposure. The newer shared allocator described below
adds that group reservation for momentum and boosted ranking.

Buys require an increased target, estimated return above costs plus safety
margin, and sufficient order notional. Sells require a decreased target and
sufficient notional; they do not require a positive forecast.

## Momentum rotation: mathematical trend strategy

[MomentumRotationModel](momentum_rotation.py) does not fit a predictive estimator.
It requires at least 253 aligned candidate sessions and computes:

```text
momentum_score = close(t - 21 sessions) / close(t - 252 sessions) - 1
```

This approximates 12-minus-1-month momentum: the latest month is omitted from
the ranking signal. A candidate can receive a positive target only when:

1. Its momentum score is strictly positive.
2. Its latest close is strictly above the trailing 200-session mean.
3. Its volatility, calculated from 60 daily return changes, is strictly positive.

Eligible candidates are sorted by momentum score. Up to `top_n` are selected;
relative sizing weights are `1 / volatility`. Each selected candidate initially
requests its share of available total exposure, then position, group and total
caps restrict the request. Unused capacity stays cash rather than being
redistributed repeatedly after caps.

For example, two selected candidates with daily volatilities of 1% and 2%
request relative shares of two-thirds and one-third of the available allocation
budget. Configured caps can reduce both allocations substantially.

A candidate with valid history that fails the trend conditions remains in the
signal set with zero allocation weight. An existing position can therefore be
exited. A candidate with missing, short, invalid or illiquid history has no usable
signal and is retained instead of being automatically liquidated.

The historical momentum score is not an expected return. Momentum sets
`TradeCandidate.predicted_return` to `None`; its trade decisions compare target
and current weights and enforce minimum notional. There is no additional
forecast-versus-cost hurdle for momentum entries.

## Boosted return ranking: nonlinear model

[BoostedRankingModel](boosted_ranker.py) reuses the baseline's features, labels
and ranking lifecycle, but replaces the estimator, calibration procedure and
allocation implementation.

[BoostedRanker](../trading/estimators.py) wraps scikit-learn's histogram gradient
boosting regressor. Successive small trees learn corrections to previous
predictions, allowing nonlinear feature interactions that a linear ridge model
cannot represent directly. It learns percentile-rank labels, not raw returns.

| Parameter | Current value |
| --- | ---: |
| Boosting iterations | 80 |
| Learning rate | 0.05 |
| Maximum leaves per tree | 7 |
| Maximum histogram bins | 64 |
| Minimum samples per leaf | 20 |
| L2 regularization | 1.0 |
| Random seed | 0 |
| Early stopping | Disabled |

Fit and predict bound thread pools to one thread. Random early-stopping splits
are disabled so the estimator does not introduce shuffled validation dates.
These model parameters are code defaults, not dashboard controls.

### Chronological calibration and purge

The model requires at least 40 distinct usable labeled dates, typically at
least 245 aligned bars before liquidity or data exclusions. It sorts those
signal dates and sets a split at `floor(0.8 * number_of_dates)`:

```text
Earlier dates                 Later dates
[ estimator fitting ][ purge ][ return calibration ]
                      5 dates
```

The estimator fits the dates before `split - 5`. Five signal dates are omitted
before calibration begins at `split`, keeping five-session fitting outcomes
before the calibration signal dates. All symbols for a date remain together.

The fitted estimator predicts ranks on the later calibration rows. A separate
linear regression maps those predictions to observed five-session returns.
The stored slope is clamped to a minimum of zero. A zero slope disables positive
allocation weights, even if the fitted intercept alone is positive. It does not
reverse the strategy when the rank relationship is negative.

The calibration interval is not used to refit the tree estimator. It is also
not an untouched final research test: it is part of model construction and is
recomputed at each preparation. Independent walk-forward evaluation remains
necessary.

### Targets and trade decisions

Latest candidates need both a positive calibration slope and an estimated
return strictly above the same 0.30% default hurdle to receive positive allocation
weights. The model selects by predicted rank and uses the shared allocator's
sequential capped sizing rather than inverse volatility.

A usable holding that loses selection gets a zero target. If calibration loses
its positive slope, usable existing holdings receive zero targets as well;
missing-history holdings remain retained. Buy/sell approval then follows the
same estimated-edge and minimum-notional rules as the ridge baseline.

## Residual mean reversion: long-only recovery

[ResidualReversionModel](residual_reversion.py) scores each symbol with
`trading.features.residual_zscores`: betas to SPY and the group benchmark are
fitted on 60 sessions before the latest five, and the five-session residual sum
is standardized. Scores of -2 or lower open an episode; it closes at -0.5, after
ten sessions, or when the score is unavailable, and must re-arm above -2 before
another entry. Episodes are rederived from history on each preparation. Open
episodes receive inverse-volatility weights through `allocate_targets`;
`predicted_return` is `None`, as for momentum.

`FilteredResidualReversionModel` overrides `reject_entries` with a logistic
breakdown classifier trained only on completed past outcomes. Vetoes apply to new
entries, never to existing holdings. It is a research variant with no `MODEL`
export. See [strategy research](../../docs/research.md#residual-mean-reversion).

## Meta-labelled momentum: classifier-filtered entries

[MetaMomentumModel](meta_momentum.py) takes opportunities from
`trading.features.momentum_panel`, which reproduces the momentum rule for every
past session, and accepts a `classifier_factory` exposing `fit`/`predict_proba`.
It labels every completed rule opportunity by its next-open, 21-session return
net of a 0.2% round trip, fits the classifier, and vetoes new entries with
probability below 0.5. Exits and inverse-volatility sizing match momentum. See
[strategy research](../../docs/research.md#meta-labelled-momentum).

## Regime-adaptive composite

[RegimeCompositeModel](regime_composite.py) fits `trading.regimes.FilteredRegimeModel`
on `trading.features.regime_features` at every preparation, using only the
supplied history. It prepares momentum rotation in `trend` and residual reversion
in `range`, multiplies the total exposure limit by 1 − P(stress), and delegates
signals to the active component. See
[strategy research](../../docs/research.md#causal-regime-adaptation).

## Temporal network ranking

[TemporalNetworkModel](temporal_network.py) builds point-in-time sequences with
`trading.temporal.build_sequences` and trains `TemporalConvNet` under a declared
`TrainingBudget`. Like boosted ranking, it separates fitting from return
calibration with a five-date purge, disables allocations when the calibration
slope is not positive, and records retraining time and update counts. See
[strategy research](../../docs/research.md#compact-temporal-network).

## Shared allocation, execution and switching

[allocate_targets](../trading/allocation.py), used by momentum and boosted
ranking, reserves total exposure for holdings without usable signals. It also
reserves their benchmark-group exposure when the current universe mapping
identifies that group. Holdings outside the mapping consume total capacity but
cannot be assigned a known group reservation.

It chooses the highest-scoring positive-weight signals up to `top_n` before
applying caps. It does not backfill lower-ranked symbols when a selected symbol
has no remaining group capacity. Equal scores receive average percentiles in
output candidates; selection ties use symbol order. Group caps do not measure
underlying issuer or sector overlap across ETFs.

Target weights describe the desired portfolio, not guaranteed fills. The
[executor](../trading/execution.py) submits at most one order per cycle, considers
reductions before additions, and orders symbols alphabetically within those
categories. It enforces configured notional bounds, buying power for buys,
asset eligibility and the shared daily attempt budget. Failed submissions can
consume budget, and a capped target is not automatically split into repeat
same-day orders.

Switching models does not liquidate holdings immediately, cancel pending broker
orders, reset the daily journal or grant a fresh order budget. The next eligible
preparation supplies the new targets. Exposure caps govern target construction;
market movements, retained holdings and incomplete fills can leave actual
exposure different from those targets.

## Evaluation and extension

The current evidence includes deterministic synthetic-data and accounting tests.
It does not establish that any strategy is profitable. Price-based signals omit
dividend distributions; present-day universe selection can introduce survivorship
bias in historical research. Momentum can suffer in trend reversals, while rank
models can overfit unstable feature relationships and return calibration.

Use the [offline research guide](../../docs/research.md) for frozen datasets,
walk-forward comparison, final-test reservation, costs, delayed execution and
known simulation limitations. The original `chronological_split` helper is not
an automatic production validation gate and does not purge overlapping labels.

To add another model, follow the [drop-in strategy guide](../../docs/models.md):
copy `_template.py`, implement the `Model` contract, keep a no-argument
constructor, and export `MODEL = ModelDefinition(...)`. Imports and construction
must have no broker or filesystem side effects. Reuse shared feature, estimator
and allocation components instead of duplicating execution rules.

Relevant checks:

```sh
uv run pytest tests/test_cross_sectional.py tests/test_momentum.py tests/test_boosted_ranker.py tests/test_residual_reversion.py tests/test_meta_momentum.py tests/test_regimes.py tests/test_temporal_network.py tests/test_allocation.py
uv run python -m research --demo /tmp/strategy-demo.json
```

The registry discovers lowercase Python filenames with explicit `MODEL`
metadata; this README and files beginning with an underscore are not selectable.
