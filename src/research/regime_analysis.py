"""Stability and transition diagnostics for regime-adaptive walk-forward decisions."""
import numpy as np

from models.regime_composite import RegimeCompositeModel
from research.backtest import run_backtest


def regime_stability(decisions):
    """Switch counts, spell lengths and probability confidence from one decision per date."""
    by_date = {d["date"]: d for d in decisions}
    ordered = [by_date[day] for day in sorted(by_date)]
    regimes = [d["regime"] for d in ordered]
    components = [d["component"] for d in ordered]
    spells, length = [], 0
    for index, regime in enumerate(regimes):
        length += 1
        if index == len(regimes) - 1 or regimes[index + 1] != regime:
            spells.append(length)
            length = 0
    return {
        "decisions": len(ordered),
        "regime_share": {name: regimes.count(name) / len(regimes) for name in sorted(set(regimes))} if regimes else {},
        "regime_switches": sum(a != b for a, b in zip(regimes, regimes[1:])),
        "component_switches": sum(a != b for a, b in zip(components, components[1:])),
        "mean_spell_sessions": float(np.mean(spells)) if spells else None,
        "mean_stress_probability": float(np.mean([d["p_stress"] for d in ordered])) if ordered else None,
        "mean_dominant_probability": float(np.mean([max(d["p_trend"], d["p_range"], d["p_stress"])
                                                    for d in ordered])) if ordered else None,
    }


def margin_variant(margin):
    class HysteresisRegimeModel(RegimeCompositeModel):
        switch_margin = margin
    HysteresisRegimeModel.__name__ = f"RegimeComposite_margin_{margin:g}"
    return HysteresisRegimeModel


def transition_sensitivity(history, settings, config, margins=(0.0, 0.2), *, runner=run_backtest):
    """Rerun the composite per switching margin, reporting stability and net metrics together."""
    results = {}
    for margin in margins:
        decisions = []
        variant = margin_variant(margin)
        report = runner(lambda: variant(decision_log=decisions), history,
                        settings.model_copy(update={"model_id": "regime_composite"}), config)
        results[f"{margin:g}"] = {"metrics": report["metrics"], "stability": regime_stability(decisions)}
    return results
