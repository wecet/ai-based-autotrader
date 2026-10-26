"""Frozen JSON datasets and deterministic fixtures for offline research."""
from datetime import date, datetime, timedelta
from typing import Literal
import hashlib
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict

from core.types import DailyBar, MarketHistory
from research.backtest import validate_history
from research.events import MarketEvents, validate_events


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: dict[str, str]
    history: MarketHistory
    source: str
    selection_notes: str
    price_basis: Literal["split_adjusted", "raw"] = "split_adjusted"
    events: MarketEvents = MarketEvents()
    ingested_at: datetime | None = None
    information_notes: str | None = None


def load_dataset(path):
    content = Path(path).read_bytes()
    dataset = Dataset.model_validate_json(content)
    validate_history(dataset.history)
    validate_events(dataset.events, dataset.history, dataset.price_basis)
    return dataset, hashlib.sha256(content).hexdigest()


def synthetic_dataset(count=310, seed=7):
    generator = np.random.default_rng(seed)
    days, day = [], date(2022, 1, 3)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    def bars(drift):
        closes = 100 * np.exp(np.cumsum(generator.normal(drift, .008, count)))
        opens = np.r_[closes[0], closes[:-1]]
        return [DailyBar(day, float(o), float(max(o, c) * 1.002), float(min(o, c) * .998), float(c), 1_000_000)
                for day, o, c in zip(days, opens, closes)]
    spy, sector = bars(.0002), bars(.0003)
    return Dataset(universe={"AAA": "GROUP", "BBB": "GROUP", "CCC": "OTHER"},
                   history=MarketHistory({"AAA": bars(.0008), "BBB": bars(-.0001), "CCC": bars(.0005)},
                                         spy, {"GROUP": sector, "OTHER": spy}),
                   source=f"Synthetic fixture, seed={seed}",
                   selection_notes="Artificial weekday sessions; not market data or evidence of profitability.")
