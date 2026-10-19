"""Replaceable deterministic regression estimators; no broker dependencies."""
from typing import Protocol

import numpy as np


class Estimator(Protocol):
    def fit(self, features: np.ndarray, targets: np.ndarray) -> object: ...

    def predict(self, features: np.ndarray) -> np.ndarray: ...


class RidgeRanker:
    """Simple, deterministic baseline rank regressor implemented with NumPy."""

    def __init__(self, regularization: float = 1.0) -> None:
        if regularization < 0:
            raise ValueError("regularization must be non-negative")
        self.regularization = regularization
        self.feature_mean: np.ndarray | None = None
        self.feature_scale: np.ndarray | None = None
        self.coefficients: np.ndarray | None = None

    def fit(self, features: np.ndarray, targets: np.ndarray) -> "RidgeRanker":
        x = np.asarray(features, dtype=float)
        y = np.asarray(targets, dtype=float)
        if x.ndim != 2 or y.ndim != 1 or len(x) != len(y):
            raise ValueError("features must be 2-D and targets must be a matching 1-D array")
        if len(x) == 0:
            raise ValueError("cannot fit an empty training set")

        self.feature_mean = x.mean(axis=0)
        self.feature_scale = x.std(axis=0)
        self.feature_scale[self.feature_scale == 0] = 1.0
        standardized = (x - self.feature_mean) / self.feature_scale
        design = np.column_stack((np.ones(len(standardized)), standardized))
        penalty = np.eye(design.shape[1]) * self.regularization
        penalty[0, 0] = 0.0
        self.coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        if self.coefficients is None or self.feature_mean is None or self.feature_scale is None:
            raise RuntimeError("the ranker must be fitted before prediction")
        x = np.asarray(features, dtype=float)
        standardized = (x - self.feature_mean) / self.feature_scale
        design = np.column_stack((np.ones(len(standardized)), standardized))
        return design @ self.coefficients


class BoostedRanker:
    """Histogram gradient boosting with no randomly shuffled validation split."""
    def __init__(self):
        from sklearn.ensemble import HistGradientBoostingRegressor
        self.regressor = HistGradientBoostingRegressor(
            max_iter=80, max_leaf_nodes=7, max_bins=64, min_samples_leaf=20,
            l2_regularization=1.0, learning_rate=.05, early_stopping=False, random_state=0)

    def fit(self, features, targets):
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            self.regressor.fit(features, targets)
        return self

    def predict(self, features):
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            return self.regressor.predict(features)
