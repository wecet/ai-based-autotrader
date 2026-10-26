"""A compact causal temporal convolutional network in NumPy, plus point-in-time sequences."""
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from core.types import DailyBar

SEQUENCE_CHANNELS = ("return_1d", "excess_return_vs_spy", "excess_return_vs_group", "log_volume_ratio_20d")


@dataclass(frozen=True)
class TrainingBudget:
    """Declared before training; never adjusted from validation results."""

    epochs: int = 20
    batch_size: int = 256
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    hidden: int = 8
    dilations: tuple[int, ...] = (1, 2, 4)
    kernel: int = 3
    seed: int = 0


def _shift(values, steps):
    """Delay along time by ``steps`` with zero padding, so position t sees t - steps."""
    if steps == 0:
        return values
    shifted = np.zeros_like(values)
    shifted[:, steps:] = values[:, :-steps]
    return shifted


def _unshift(values, steps):
    if steps == 0:
        return values
    shifted = np.zeros_like(values)
    shifted[:, :-steps] = values[:, steps:]
    return shifted


class TemporalConvNet:
    """Dilated causal convolutions with ReLU, read out at the final session.

    Inputs are (samples, sessions, channels). Each layer's output at session t
    depends only on sessions up to t. Channel scaling is fitted inside ``fit``.
    Training uses mini-batch Adam with the declared budget and a fixed seed.
    """

    def __init__(self, budget: TrainingBudget = TrainingBudget()):
        self.budget = budget
        self.parameters = None

    def _initialize(self, channels, generator):
        budget, parameters, width = self.budget, {}, channels
        for layer, _ in enumerate(budget.dilations):
            scale = np.sqrt(2 / (width * budget.kernel))
            parameters[f"w{layer}"] = generator.normal(0, scale, (budget.kernel, width, budget.hidden))
            parameters[f"b{layer}"] = np.zeros(budget.hidden)
            width = budget.hidden
        parameters["v"] = generator.normal(0, np.sqrt(1 / width), width)
        parameters["c"] = np.zeros(())
        return parameters

    def _forward(self, x, parameters):
        cache, hidden = [], x
        for layer, dilation in enumerate(self.budget.dilations):
            shifted = [_shift(hidden, k * dilation) for k in range(self.budget.kernel)]
            output = parameters[f"b{layer}"] + sum(s @ parameters[f"w{layer}"][k] for k, s in enumerate(shifted))
            cache.append((shifted, output))
            hidden = np.maximum(output, 0)
        return hidden[:, -1] @ parameters["v"] + parameters["c"], hidden, cache

    def loss_and_gradients(self, x, y, parameters):
        prediction, hidden, cache = self._forward(x, parameters)
        error = prediction - y
        loss = float(np.mean(error ** 2))
        d_prediction = 2 * error / len(y)
        gradients = {"v": hidden[:, -1].T @ d_prediction, "c": np.asarray(d_prediction.sum())}
        d_hidden = np.zeros_like(hidden)
        d_hidden[:, -1] = np.outer(d_prediction, parameters["v"])
        for layer in reversed(range(len(self.budget.dilations))):
            shifted, output = cache[layer]
            d_output = d_hidden * (output > 0)
            dilation = self.budget.dilations[layer]
            weights = parameters[f"w{layer}"]
            gradients[f"w{layer}"] = np.stack([np.einsum("ntc,nth->ch", s, d_output) for s in shifted])
            gradients[f"b{layer}"] = d_output.sum(axis=(0, 1))
            d_hidden = sum(_unshift(d_output @ weights[k].T, k * dilation) for k in range(self.budget.kernel))
        return loss, gradients

    def fit(self, sequences, targets):
        x = np.asarray(sequences, dtype=float)
        y = np.asarray(targets, dtype=float)
        if x.ndim != 3 or len(x) != len(y) or len(x) == 0:
            raise ValueError("Sequences must be (samples, sessions, channels) with matching targets")
        self.mean = x.reshape(-1, x.shape[2]).mean(axis=0)
        self.scale = x.reshape(-1, x.shape[2]).std(axis=0)
        self.scale[self.scale == 0] = 1.0
        x = (x - self.mean) / self.scale
        budget = self.budget
        generator = np.random.default_rng(budget.seed)
        parameters = self._initialize(x.shape[2], generator)
        first = {name: np.zeros_like(value) for name, value in parameters.items()}
        second = {name: np.zeros_like(value) for name, value in parameters.items()}
        step, self.losses = 0, []
        for _ in range(budget.epochs):
            order = generator.permutation(len(x))
            epoch_loss = 0.0
            for start in range(0, len(x), budget.batch_size):
                batch = order[start:start + budget.batch_size]
                loss, gradients = self.loss_and_gradients(x[batch], y[batch], parameters)
                epoch_loss += loss * len(batch)
                step += 1
                for name, gradient in gradients.items():
                    if name.startswith("w"):
                        gradient = gradient + budget.weight_decay * parameters[name]
                    first[name] = .9 * first[name] + .1 * gradient
                    second[name] = .999 * second[name] + .001 * gradient ** 2
                    corrected = first[name] / (1 - .9 ** step)
                    variance = second[name] / (1 - .999 ** step)
                    parameters[name] = parameters[name] - budget.learning_rate * corrected / (np.sqrt(variance) + 1e-8)
            self.losses.append(epoch_loss / len(x))
        self.parameters = parameters
        self.updates = step
        return self

    def predict(self, sequences):
        if self.parameters is None:
            raise RuntimeError("The network must be fitted before prediction")
        x = (np.asarray(sequences, dtype=float) - self.mean) / self.scale
        return self._forward(x, self.parameters)[0]


def sequence_channels(bars: Sequence[DailyBar], spy_bars: Sequence[DailyBar],
                      group_bars: Sequence[DailyBar]) -> np.ndarray:
    """(sessions, channels) inputs; row t uses data up to t and row 0..20 are NaN."""
    closes = np.asarray([b.close for b in bars], dtype=float)
    spy = np.asarray([b.close for b in spy_bars], dtype=float)
    group = np.asarray([b.close for b in group_bars], dtype=float)
    volumes = np.asarray([b.volume for b in bars], dtype=float)
    channels = np.full((len(closes), len(SEQUENCE_CHANNELS)), np.nan)
    if len(closes) < 21:
        return channels
    returns = closes[1:] / closes[:-1] - 1
    average_volume = np.convolve(volumes, np.ones(20) / 20, mode="valid")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.log(volumes[19:] / average_volume)
    channels[1:, 0] = returns
    channels[1:, 1] = returns - (spy[1:] / spy[:-1] - 1)
    channels[1:, 2] = returns - (group[1:] / group[:-1] - 1)
    channels[19:, 3] = np.where(np.isfinite(ratio), ratio, 0.0)
    channels[:20] = np.nan
    return channels


def build_sequences(stock_bars: Mapping[str, Sequence[DailyBar]], spy_bars, sector_bars, sectors, *,
                    length=32, horizon=5, minimum_price=10.0, minimum_average_dollar_volume=5_000_000.0):
    """Labeled and latest point-in-time sequences for aligned, liquid symbols.

    Returns (labeled, latest): labeled is a list of (date_index, symbol, sequence,
    future_return, target_rank) with ranks computed within each date; latest maps
    symbols to (sequence, close) for the final session.
    """
    dates = [b.date for b in spy_bars]
    by_date, latest = {}, {}
    for symbol, bars in sorted(stock_bars.items()):
        group = sector_bars.get(sectors.get(symbol, ""), [])
        if [b.date for b in bars] != dates or [b.date for b in group] != dates or len(bars) < length + 21:
            continue
        channels = sequence_channels(bars, spy_bars, group)
        closes = np.asarray([b.close for b in bars], dtype=float)
        dollar_volume = np.convolve(closes * np.asarray([b.volume for b in bars], dtype=float),
                                    np.ones(20) / 20, mode="valid")
        for t in range(length + 19, len(bars)):
            window = channels[t - length + 1:t + 1]
            if (not np.isfinite(window).all() or closes[t] <= minimum_price
                    or dollar_volume[t - 19] <= minimum_average_dollar_volume):
                continue
            if t == len(bars) - 1:
                latest[symbol] = (window, float(closes[t]))
            elif t + horizon < len(bars):
                by_date.setdefault(t, []).append((symbol, window, closes[t + horizon] / closes[t] - 1))
    labeled = []
    for t, rows in sorted(by_date.items()):
        ordered = sorted(rows, key=lambda row: row[2])
        denominator = max(len(ordered) - 1, 1)
        for rank, (symbol, window, outcome) in enumerate(ordered):
            labeled.append((t, symbol, window, float(outcome), rank / denominator))
    return labeled, latest
