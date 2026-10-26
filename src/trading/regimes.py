"""Causal market-regime probabilities from a small Gaussian mixture and forward filter."""
import numpy as np

REGIMES = ("trend", "range", "stress")


class FilteredRegimeModel:
    """Diagonal Gaussian mixture emissions with a transition matrix and forward filtering.

    ``fit`` standardizes features and estimates everything from the supplied
    (training) rows only. ``filter`` returns p(state_t | x_1..x_t) per row, so a
    probability never depends on later observations. States are named from their
    centroids: highest volatility minus trend is ``stress``, highest trend minus
    volatility among the rest is ``trend``, the remaining state is ``range``.
    """

    def __init__(self, components=3, seed=0, smoothing=1.0):
        self.components = components
        self.seed = seed
        self.smoothing = smoothing

    def fit(self, features):
        from sklearn.mixture import GaussianMixture
        from threadpoolctl import threadpool_limits
        features = np.asarray(features, dtype=float)
        if len(features) < 10 * self.components or not np.isfinite(features).all():
            raise ValueError("Regime model needs at least ten finite rows per state")
        self.mean = features.mean(axis=0)
        self.scale = features.std(axis=0)
        self.scale[self.scale == 0] = 1.0
        standardized = (features - self.mean) / self.scale
        mixture = GaussianMixture(self.components, covariance_type="diag", n_init=3, random_state=self.seed)
        with threadpool_limits(limits=1):
            mixture.fit(standardized)
        self.means, self.variances, self.weights = mixture.means_, mixture.covariances_, mixture.weights_
        with threadpool_limits(limits=1):
            states = mixture.predict(standardized)
        counts = np.full((self.components, self.components), self.smoothing)
        for previous, current in zip(states, states[1:]):
            counts[previous, current] += 1
        self.transitions = counts / counts.sum(axis=1, keepdims=True)
        # Columns 0 and 1 are trend and volatility (REGIME_FEATURE_NAMES order).
        risk = self.means[:, 1] - self.means[:, 0]
        stress = int(np.argmax(risk))
        rest = [state for state in range(self.components) if state != stress]
        trend = max(rest, key=lambda state: -risk[state])
        self.names = {stress: "stress", trend: "trend"}
        for state in rest:
            self.names.setdefault(state, "range")
        return self

    def _log_emissions(self, features):
        standardized = (np.asarray(features, dtype=float) - self.mean) / self.scale
        difference = standardized[:, None, :] - self.means[None, :, :]
        return -.5 * (np.sum(difference ** 2 / self.variances, axis=2)
                      + np.sum(np.log(2 * np.pi * self.variances), axis=1))

    def filter(self, features):
        """Forward-filtered state probabilities, keyed by regime name."""
        emissions = self._log_emissions(features)
        belief = self.weights.copy()
        filtered = np.empty_like(emissions)
        for t, log_likelihood in enumerate(emissions):
            prior = belief if t == 0 else belief @ self.transitions
            posterior = np.log(prior) + log_likelihood
            posterior = np.exp(posterior - posterior.max())
            belief = posterior / posterior.sum()
            filtered[t] = belief
        named = {name: np.zeros(len(filtered)) for name in REGIMES}
        for state, name in self.names.items():
            named[name] = named[name] + filtered[:, state]
        return named


def dominant_regimes(probabilities, margin=0.0):
    """Active regime per row; switching requires beating the incumbent by ``margin``."""
    current, path = None, []
    for row in zip(*(probabilities[name] for name in REGIMES)):
        values = dict(zip(REGIMES, row))
        best = max(REGIMES, key=lambda name: values[name])
        if current is None or values[best] > values[current] + margin:
            current = best
        path.append(current)
    return path
