"""Compare registered strategies on a frozen dataset without broker access."""
from dataclasses import asdict, replace
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys

from configuration.settings import TradingSettings
from core.registry import ModelRegistry
from research.allocation import compare_allocations
from research.backtest import BacktestConfig, history_before, run_backtest
from research.datasets import load_dataset, synthetic_dataset
from research.meta_labeling import meta_decision_quality
from research.regime_analysis import transition_sensitivity
from research.shadow import shadow_comparison
from research.temporal_analysis import history_sufficiency, retraining_cost
from research.protocols import PROTOCOLS, append_trials, protocol_record

USAGE = "Usage: python -m research DATASET.json OUTPUT.json | --demo OUTPUT.json"
BASELINES = ("cross_sectional", "momentum_rotation", "boosted_ranker")
ALLOCATION_SIGNALS = ("momentum_rotation", "boosted_ranker")


def strategy_factories(registry, experiment):
    if experiment == "ensemble":
        return {name: registry.require(name).model_type
                for name in ("momentum_rotation", "boosted_ranker", "residual_reversion", "ensemble")}
    if experiment == "temporal":
        return {name: registry.require(name).model_type
                for name in ("momentum_rotation", "boosted_ranker", "temporal_network")}
    if experiment == "regime":
        return {name: registry.require(name).model_type
                for name in ("momentum_rotation", "boosted_ranker", "residual_reversion", "regime_composite")}
    if experiment == "meta_momentum":
        return {name: registry.require(name).model_type
                for name in ("momentum_rotation", "boosted_ranker", "meta_momentum")}
    if experiment == "reversion":
        from models.residual_reversion import FilteredResidualReversionModel
        names = ("momentum_rotation", "boosted_ranker", "residual_reversion")
        return {**{name: registry.require(name).model_type for name in names},
                "residual_reversion_filtered": FilteredResidualReversionModel}
    return {name: registry.require(name).model_type for name in BASELINES}


def compare_strategies(factories, history, settings, config, runner=None):
    runner = runner or run_backtest
    results = {}
    for name, factory in factories.items():
        strategy_settings = settings.model_copy(update={"model_id": name})
        result = runner(factory, history, strategy_settings, config)
        cost_stress = runner(factory, history, strategy_settings,
                                   replace(config, spread_bps=config.spread_bps * 2,
                                           slippage_bps=config.slippage_bps * 2))
        delay_stress = runner(factory, history, strategy_settings,
                                    replace(config, warmup_sessions=config.warmup_sessions - 1, delay_sessions=1))
        result["stress_metrics"] = {"double_costs": cost_stress["metrics"],
                                    "one_session_delay": delay_stress["metrics"]}
        results[name] = result
    return results


def with_cadences(runner, cadences):
    """Apply per-strategy refit cadences, keyed by the run's settings.model_id."""
    def run(factory, history, settings, config, **options):
        sessions = cadences.get(settings.model_id)
        if sessions is not None:
            config = replace(config, rebalance_sessions=sessions)
        return runner(factory, history, settings, config, **options)
    return run


def parse_cadences(value):
    cadences = json.loads(value or "{}")
    if not isinstance(cadences, dict) or any(type(v) is not int or v < 1 for v in cadences.values()):
        raise ValueError("RESEARCH_REBALANCE_BY_MODEL must map model IDs to positive integers")
    return cadences


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
        lookback_sessions=int(os.environ["RESEARCH_LOOKBACK_SESSIONS"]) if os.getenv("RESEARCH_LOOKBACK_SESSIONS")
        else None,
    )
    cadences = parse_cadences(os.getenv("RESEARCH_REBALANCE_BY_MODEL"))
    reserved = int(os.getenv("RESEARCH_FINAL_TEST_SESSIONS", "20"))
    if reserved < 1 or len(dataset.history.spy) <= config.warmup_sessions + reserved:
        raise ValueError("Need development and reserved final-test sessions after warmup")
    final_start = dataset.history.spy[-reserved].date
    final_mode = os.getenv("RESEARCH_FINAL_TEST", "false").lower() == "true"
    experiment = os.getenv("RESEARCH_EXPERIMENT", "baselines")
    if experiment not in PROTOCOLS:
        raise ValueError("RESEARCH_EXPERIMENT must be one of: " + ", ".join(PROTOCOLS))
    evaluation_history = dataset.history if final_mode else history_before(dataset.history, final_start)
    if final_mode:
        config = replace(config, warmup_sessions=len(dataset.history.spy) - reserved)
    registry = ModelRegistry()
    mode = "final_test" if final_mode else "development"
    trial = {"dataset_sha256": digest, "evaluation_mode": mode, "output": args[1],
             "config": asdict(config), "rebalance_by_model": cadences, **protocol_record(experiment)}
    trial_log = Path(os.getenv("RESEARCH_TRIAL_LOG", str(Path(args[1]).parent / "research-trials.jsonl")))
    # Datasets with events execute on raw prices; models still see adjusted history.
    runner = run_backtest
    if not dataset.events.is_empty():
        runner = partial(run_backtest, market_events=dataset.events)
    runner = with_cadences(runner, cadences)
    try:
        if experiment == "allocation":
            model_types = {name: registry.require(name).model_type for name in ALLOCATION_SIGNALS}
            results = compare_allocations(model_types, evaluation_history, settings, config, runner=runner)
        else:
            results = compare_strategies(strategy_factories(registry, experiment), evaluation_history, settings, config,
                                         runner)
            if experiment == "ensemble":
                results["ensemble"]["shadow"] = shadow_comparison(evaluation_history, settings, config,
                                                                  runner=runner)
            if experiment == "temporal":
                results["temporal_network"]["retraining_cost"] = retraining_cost(
                    evaluation_history, settings, config, runner=runner)
                results["temporal_network"]["history_sufficiency"] = history_sufficiency(evaluation_history)
            if experiment == "regime":
                results["regime_composite"]["transition_sensitivity"] = transition_sensitivity(
                    evaluation_history, settings, config, runner=runner)
            if experiment == "meta_momentum":
                results["meta_momentum"]["decision_quality"] = meta_decision_quality(
                    evaluation_history, settings, config, runner=runner)
    except Exception as error:
        append_trials(trial_log, {**trial, "status": "failed", "error": type(error).__name__ + ": " + str(error)})
        raise
    append_trials(trial_log, {**trial, "status": "completed"})
    output = {"dataset_sha256": digest, "source": dataset.source, "price_basis": dataset.price_basis,
              "ingested_at": dataset.ingested_at.isoformat() if dataset.ingested_at else None,
              "protocol": protocol_record(experiment),
              "selection_notes": dataset.selection_notes, "annualization_sessions": 252,
              "evaluation_mode": mode, "trial_log": str(trial_log), "rebalance_by_model": cadences,
              "reserved_final_start": final_start.isoformat(), "reserved_final_sessions": reserved,
              "results": results}
    Path(args[1]).write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {args[1]} (offline research; source: {dataset.source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
