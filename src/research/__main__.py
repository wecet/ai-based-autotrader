"""Compare registered strategies on a frozen dataset without broker access."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

from configuration.settings import TradingSettings
from core.registry import ModelRegistry
from research.backtest import BacktestConfig, history_before, run_backtest
from research.datasets import load_dataset, synthetic_dataset

USAGE = "Usage: python -m research DATASET.json OUTPUT.json | --demo OUTPUT.json"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--help"]:
        print(USAGE)
        return 0
    if len(args) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    if args[0] == "--demo":
        dataset = synthetic_dataset()
        digest = hashlib.sha256(dataset.model_dump_json().encode()).hexdigest()
    else:
        dataset, digest = load_dataset(args[0])
    settings = TradingSettings.from_env_values({**os.environ, "TRADER_UNIVERSE": json.dumps(dataset.universe)})
    config = BacktestConfig(
        warmup_sessions=int(os.getenv("RESEARCH_WARMUP_SESSIONS", "260")),
        rebalance_sessions=int(os.getenv("RESEARCH_REBALANCE_SESSIONS", "1")),
        spread_bps=float(os.getenv("RESEARCH_SPREAD_BPS", "5")),
        slippage_bps=float(os.getenv("RESEARCH_SLIPPAGE_BPS", "5")),
        fee_bps=float(os.getenv("RESEARCH_FEE_BPS", "0")),
    )
    reserved = int(os.getenv("RESEARCH_FINAL_TEST_SESSIONS", "20"))
    if reserved < 1 or len(dataset.history.spy) <= config.warmup_sessions + reserved:
        raise ValueError("Need development and reserved final-test sessions after warmup")
    final_start = dataset.history.spy[-reserved].date
    final_mode = os.getenv("RESEARCH_FINAL_TEST", "false").lower() == "true"
    evaluation_history = dataset.history if final_mode else history_before(dataset.history, final_start)
    if final_mode:
        config = replace(config, warmup_sessions=len(dataset.history.spy) - reserved)
    registry = ModelRegistry()
    results = {}
    for name in ("cross_sectional", "momentum_rotation", "boosted_ranker"):
        factory = registry.require(name).model_type
        strategy_settings = settings.model_copy(update={"model_id": name})
        result = run_backtest(factory, evaluation_history, strategy_settings, config)
        cost_stress = run_backtest(factory, evaluation_history, strategy_settings,
                                  replace(config, spread_bps=config.spread_bps * 2,
                                          slippage_bps=config.slippage_bps * 2))
        delay_stress = run_backtest(factory, evaluation_history, strategy_settings,
                                    replace(config, warmup_sessions=config.warmup_sessions - 1, delay_sessions=1))
        result["stress_metrics"] = {"double_costs": cost_stress["metrics"], "one_session_delay": delay_stress["metrics"]}
        results[name] = result
    output = {"dataset_sha256": digest, "source": dataset.source,
              "selection_notes": dataset.selection_notes, "annualization_sessions": 252,
              "evaluation_mode": "final_test" if final_mode else "development",
              "reserved_final_start": final_start.isoformat(), "reserved_final_sessions": reserved,
              "results": results}
    Path(args[1]).write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {args[1]} (offline research; source: {dataset.source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
