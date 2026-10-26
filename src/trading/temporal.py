"""A compact causal temporal convolutional network (PyTorch) and point-in-time sequences."""
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


class TemporalConvNet:
    """Dilated causal convolutions with ReLU in PyTorch, read out at the final session.

    Inputs are (samples, sessions, channels). Left-only padding makes each
    layer's output at session t depend only on sessions up to t. Channel scaling
    is fitted inside ``fit``. Training is CPU-only, single-threaded, seeded and
    deterministic, using mini-batch AdamW with the declared budget.
    """

    def __init__(self, budget: TrainingBudget = TrainingBudget()):
        self.budget = budget
        self.network = None

    def _build(self, channels):
        import torch
        budget = self.budget

        class CausalLayer(torch.nn.Module):
            def __init__(self, width, dilation):
                super().__init__()
                self.padding = (budget.kernel - 1) * dilation
                self.convolution = torch.nn.Conv1d(width, budget.hidden, budget.kernel, dilation=dilation)

            def forward(self, x):
                return torch.relu(self.convolution(torch.nn.functional.pad(x, (self.padding, 0))))

        class Network(torch.nn.Module):
            def __init__(self):
                super().__init__()
                widths = [channels] + [budget.hidden] * (len(budget.dilations) - 1)
                self.layers = torch.nn.Sequential(*(CausalLayer(w, d) for w, d in zip(widths, budget.dilations)))
                self.head = torch.nn.Linear(budget.hidden, 1)

            def hidden(self, x):
                return self.layers(x.transpose(1, 2)).transpose(1, 2)

            def forward(self, x):
                return self.head(self.hidden(x)[:, -1]).squeeze(-1)

        return Network()

    def _tensor(self, sequences):
        import torch
        x = (np.asarray(sequences, dtype=float) - self.mean) / self.scale
        return torch.as_tensor(x, dtype=torch.float32)

    def fit(self, sequences, targets):
        import torch
        x = np.asarray(sequences, dtype=float)
        y = np.asarray(targets, dtype=float)
        if x.ndim != 3 or len(x) != len(y) or len(x) == 0:
            raise ValueError("Sequences must be (samples, sessions, channels) with matching targets")
        self.mean = x.reshape(-1, x.shape[2]).mean(axis=0)
        self.scale = x.reshape(-1, x.shape[2]).std(axis=0)
        self.scale[self.scale == 0] = 1.0
        budget = self.budget
        threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            torch.manual_seed(budget.seed)
            network = self._build(x.shape[2])
            optimizer = torch.optim.AdamW(network.parameters(), lr=budget.learning_rate,
                                          weight_decay=budget.weight_decay)
            inputs, outputs = self._tensor(x), torch.as_tensor(y, dtype=torch.float32)
            generator = torch.Generator().manual_seed(budget.seed)
            self.losses, self.updates = [], 0
            for _ in range(budget.epochs):
                order = torch.randperm(len(inputs), generator=generator)
                epoch_loss = 0.0
                for start in range(0, len(inputs), budget.batch_size):
                    batch = order[start:start + budget.batch_size]
                    optimizer.zero_grad()
                    loss = torch.nn.functional.mse_loss(network(inputs[batch]), outputs[batch])
                    loss.backward()
                    optimizer.step()
                    epoch_loss += loss.item() * len(batch)
                    self.updates += 1
                self.losses.append(epoch_loss / len(inputs))
        finally:
            torch.set_num_threads(threads)
        self.network = network.eval()
        return self

    def hidden(self, sequences):
        """Final-layer activations per session, for causality checks."""
        import torch
        with torch.no_grad():
            return self.network.hidden(self._tensor(sequences)).numpy()

    def predict(self, sequences):
        import torch
        if self.network is None:
            raise RuntimeError("The network must be fitted before prediction")
        with torch.no_grad():
            return self.network(self._tensor(sequences)).numpy().astype(float)


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
