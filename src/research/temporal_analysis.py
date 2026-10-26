"""Retraining cost and history sufficiency for the temporal network experiment."""
import numpy as np

from models.temporal_network import TemporalNetworkModel
from research.backtest import run_backtest

MINIMUM_INTERPRETABLE_SESSIONS = 1260


def retraining_cost(history, settings, config, *, runner=run_backtest):
    """Rerun the network once, summarizing every refit's time, updates and calibration."""
    log = []
    runner(lambda: TemporalNetworkModel(cost_log=log), history,
           settings.model_copy(update={"model_id": "temporal_network"}), config)
    if not log:
        return {"refits": 0}
    return {"refits": len(log),
            "mean_training_seconds": float(np.mean([entry["seconds"] for entry in log])),
            "total_training_seconds": float(np.sum([entry["seconds"] for entry in log])),
            "updates_per_refit": log[0]["updates"],
            "mean_training_examples": float(np.mean([entry["examples"] for entry in log])),
            "mean_final_loss": float(np.mean([entry["final_loss"] for entry in log])),
            "flat_calibration_share": float(np.mean([entry["calibration_slope"] <= 0 for entry in log]))}


def history_sufficiency(history):
    sessions = len(history.spy)
    return {"sessions": sessions, "minimum_interpretable_sessions": MINIMUM_INTERPRETABLE_SESSIONS,
            "interpretable": sessions >= MINIMUM_INTERPRETABLE_SESSIONS}
