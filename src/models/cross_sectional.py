"""Cross-sectional stock ranking experiment.

The module deliberately separates research from execution:

* :func:`build_feature_rows` turns completed daily bars into point-in-time features.
* :func:`build_ranked_training_set` creates five-day, within-date rank targets.
* :class:`RidgeRanker` is a small NumPy baseline that can later be replaced by any
  LightGBM/XGBoost-style estimator exposing ``fit`` and ``predict``.
* :class:`CrossSectionalModel` ranks the latest universe and emits trade decisions.

No order is submitted by importing this module.
"""

from __future__ import annotations

from core.types import DailyBar, FeatureRow, TradeCandidate, MarketHistory, PortfolioLimits
from core.registry import ModelDefinition


# Re-export the original research API for existing callers.
from trading.features import (FEATURE_NAMES, calculate_features, build_feature_rows,
                              build_ranked_training_set, chronological_split)
from trading.estimators import Estimator, RidgeRanker

from trading.ranking import RankingModel


class CrossSectionalModel(RankingModel):
    """The original linear rank strategy; shared mechanics live in trading."""


MODEL = ModelDefinition(
    name="Cross-sectional ranking",
    description=("Ranks stocks and ETFs against one another using completed daily bars. "
                 "A ridge model estimates five-session return ranks, then selects positions "
                 "whose estimated edge exceeds trading costs, within your exposure limits."),
    model_type=CrossSectionalModel,
)
