# Drop-in strategies

The runtime discovers strategy files in `src/models/`. Each file exports a
`MODEL` definition containing its display name, explanation, and concrete
`Model` class. The dashboard reads those definitions; it does not maintain a
separate list of strategy names or descriptions.

The shipped strategies are `cross_sectional` (ridge baseline),
`momentum_rotation` (trend and inverse volatility), `boosted_ranker`
(nonlinear ranks with chronological return calibration), and
`residual_reversion` (long-only recovery from unusual residual weakness). See
[strategy research](research.md) for their methods and validation.

One strategy is active at a time. The selection is stored as `TRADER_MODEL` in
the shared runtime settings file. Its value is the Python filename without
`.py`, such as `cross_sectional`. The current strategy is shown as a checked
card in the dashboard. Choosing another card selects it exclusively; use the
pause control to disable order submissions.

For detailed formulas, feature definitions, calibration windows, allocation
behavior and limitations, see the [model implementation README](../src/models/README.md).

## Add a model

1. Copy [the template](../src/models/_template.py) to a new file in `src/models/`,
   for example `momentum.py`. Use a lowercase filename with letters, digits,
   and underscores; files beginning with an underscore are not discovered.
2. Implement the four methods below and remove their `@abstractmethod`
   decorators. Keep the model constructor callable with no arguments.
3. Export a `MODEL` definition with a meaningful name and strategy explanation.
4. Add tests using synthetic data, then run `uv run pytest`.
5. Rebuild and recreate the Compose services so both contain the new file:

   ```sh
   docker compose up -d --build
   ```

6. Reload the dashboard, tick the new strategy, and save. Switching between
   strategies already in the image does not require another restart.

A metadata export looks like this, after implementing `MomentumModel`:

```python
from core.registry import ModelDefinition

MODEL = ModelDefinition(
    name="Momentum rotation",
    description="Describe the implemented signal, holding horizon, and selection rules.",
    model_type=MomentumModel,
)
```

No edit to the runner, dashboard JavaScript, registry list, or legacy enum is
needed. The shipped template is intentionally abstract and excluded from the
catalog so unfinished code cannot accidentally become an active strategy.

## Model interface

The base class is [core/contracts.py](../src/core/contracts.py).
Shared data types are in [core/types.py](../src/core/types.py).

| Method | Responsibility |
| --- | --- |
| `configure(settings)` | Apply editable controls; inherited implementation updates minimum order notional |
| `prepare(history, universe)` | Prepare signals from completed histories; train an estimator if the strategy needs one |
| `candidates(current_weights, portfolio_value, limits)` | Return a list of desired portfolio changes |
| `decide_to_buy(candidate)` | Approve or reject an increase |
| `decide_to_sell(candidate)` | Approve or reject a reduction |

`configure` has a default implementation; the other four methods are abstract.
An override of `configure` should call `super().configure(settings)`.

`MarketHistory` supplies candidate histories, SPY history, and benchmark ETF
histories. Each `DailyBar` carries its date and OHLCV values. `PortfolioLimits`
carries total exposure, benchmark-group exposure, per-position exposure, and
target-position count. `TradeCandidate` carries the symbol, optional expected return,
price, sector/group, desired/current weights, account value, and cost estimates.
A mathematical score must not be represented as a return forecast; momentum
leaves `predicted_return=None` and uses its own target-based entry decisions.
Its order notional is the absolute weight change multiplied by account value.

Each model must apply the supplied portfolio limits when generating targets,
account for retained holdings, and return sensible long-only candidates. The
shared executor enforces order minimum/maximum amounts, buying power for buys,
asset eligibility, daily budget, and duplicate suppression. It does not infer a
new model's allocation algorithm or independently repair its portfolio targets.

Models do not instantiate accounts, submit broker orders, or modify runtime
settings/journals. Their constructor and module import should have no external
side effects. Model files are trusted Python code installed by the developer;
there is no dashboard upload or untrusted-plugin sandbox.

## Lifecycle and switching

`DailySignals` calls the selected model's `prepare` once per eligible New York
date and delegates candidate generation to that model. A model, universe, or
feed change creates a fresh model instance and clears the preparation date.
Preparation happens on the next eligible cycle, after broker guards pass.
This is a daily-history interface; a future intraday or news model will need
appropriate data-provider extensions rather than assuming those inputs exist.

`OrderExecutor` inherits `Trader`. Its production path calls `Trader.trade`,
which evaluates buy and sell once each and rejects conflicting decisions before
broker submission. `buy` and `sell` then use the same eligibility and journal
checks regardless of which model is selected.

Switching does not liquidate holdings, cancel pending broker orders, clear daily
attempts, or grant a new budget. Fingerprints remain symbol/side/target based,
without a strategy ID, so changing models cannot bypass same-day deduplication.
A switch takes effect between cycles, not midway through an order.

## Discovery errors and compatibility

Missing exports, abstract/non-Model classes, invalid metadata, import failures,
and invalid filenames are reported as unavailable in the dashboard. Valid files
remain selectable. Unknown IDs cannot be saved through the API; a configured
model that becomes unavailable causes the trader to fail rather than silently
fall back to another strategy. Imports are cached in a running process; restart
or rebuild after editing an existing model's code.

The original linear strategy is exposed through [cross_sectional.py](../src/models/cross_sectional.py).
Its research helpers remain available through compatibility re-exports from
`trading/features.py`, `trading/estimators.py` and `trading/ranking.py`.
Its former hyphenated
filename has been normalized for package imports. Old runtime settings files
without `TRADER_MODEL` are read with the `cross_sectional` default, preserving
all other saved limits. The next dashboard save includes the new key.

`configuration/legacy.py` retains the earlier research `Config` and enum for
compatibility; neither defines the runtime catalog. `brokers/paper_trader.py`
retains the standalone `PaperTrader` adapter for research/tests. The deployed
service always uses `trading/execution.py` and its persistent journal.
