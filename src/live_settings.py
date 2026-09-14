"""Validated, allowlisted runtime settings shared by the trader and dashboard."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TradingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    universe: dict[str, str] = Field(min_length=2, max_length=500)
    feed: Literal["iex", "sip"] = "iex"
    trading_enabled: bool = True
    max_daily_trades: int = Field(default=10, ge=0, le=1000)
    minimum_order_notional: float = Field(default=10.0, ge=1, le=1_000_000)
    maximum_order_notional: float | None = Field(default=None, ge=1, le=1_000_000)
    maximum_total_exposure: float = Field(default=.5, gt=0, le=1)
    maximum_sector_exposure: float = Field(default=.2, gt=0, le=1)
    maximum_position: float = Field(default=.1, gt=0, le=1)
    top_n: int = Field(default=5, ge=1, le=100)

    @field_validator("universe")
    @classmethod
    def valid_universe(cls, value):
        normalized = {}
        for symbol, proxy in value.items():
            symbol, proxy = symbol.strip().upper(), proxy.strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", proxy):
                raise ValueError("Use valid stock/ETF ticker symbols and benchmark symbols")
            if symbol in normalized:
                raise ValueError("Duplicate candidate after normalizing ticker symbols")
            normalized[symbol] = proxy
        return normalized

    @field_validator("minimum_order_notional", "maximum_order_notional")
    @classmethod
    def whole_cents(cls, value):
        if value is not None and abs(value * 100 - round(value * 100)) > .00001:
            raise ValueError("Order amounts must have at most two decimal places")
        return value

    @model_validator(mode="after")
    def consistent_limits(self):
        if self.maximum_order_notional is not None and self.maximum_order_notional < self.minimum_order_notional:
            raise ValueError("Maximum order amount must be at least the minimum")
        if not self.maximum_position <= self.maximum_sector_exposure <= self.maximum_total_exposure:
            raise ValueError("Position exposure must not exceed group exposure or total exposure")
        return self

    @classmethod
    def from_environment(cls):
        return cls.from_env_values(os.environ)

    @classmethod
    def from_env_values(cls, values):
        data = {}
        for field, (name, convert) in ENV_FIELDS.items():
            if name in values and values[name] is not None:
                data[field] = convert(values[name])
        if "universe" not in data:
            raise ValueError("Set TRADER_UNIVERSE to at least two stock:sector-ETF pairs")
        return cls.model_validate(data)


def json_object(value):
    def no_duplicates(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate symbol in TRADER_UNIVERSE")
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=no_duplicates)


def boolean(value):
    if value.lower() not in ("true", "false"):
        raise ValueError("TRADER_ENABLED must be true or false")
    return value.lower() == "true"


ENV_FIELDS = {
    "universe": ("TRADER_UNIVERSE", json_object),
    "feed": ("TRADER_DATA_FEED", str),
    "trading_enabled": ("TRADER_ENABLED", boolean),
    "max_daily_trades": ("TRADER_MAX_DAILY_TRADES", int),
    "minimum_order_notional": ("TRADER_MIN_ORDER_NOTIONAL", float),
    "maximum_order_notional": ("TRADER_MAX_ORDER_NOTIONAL", lambda s: None if s in ("", "null") else float(s)),
    "maximum_total_exposure": ("TRADER_MAX_TOTAL_EXPOSURE", float),
    "maximum_sector_exposure": ("TRADER_MAX_SECTOR_EXPOSURE", float),
    "maximum_position": ("TRADER_MAX_POSITION", float),
    "top_n": ("TRADER_TOP_N", int),
}


def serialize_settings(settings):
    values = settings.model_dump()
    lines = ["# Managed runtime settings. Broker credentials never belong in this file."]
    for field, (name, _) in ENV_FIELDS.items():
        value = values[field]
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
        if isinstance(value, (dict, str)):
            encoded = "'" + encoded + "'" if isinstance(value, dict) else value
        lines.append(f"{name}={encoded}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SettingsSnapshot:
    settings: TradingSettings
    revision: str


def snapshot(settings):
    revision = hashlib.sha256(serialize_settings(settings).encode()).hexdigest()[:20]
    return SettingsSnapshot(settings, revision)


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class SettingsConflict(Exception):
    pass


class LiveSettingsStore:
    def __init__(self, directory, defaults=TradingSettings.from_environment):
        self.directory = Path(directory)
        self.path = self.directory / "runtime.env"
        self.defaults = defaults

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "settings.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self):
        if not self.path.exists():
            return snapshot(self.defaults())
        values = dotenv_values(stream=StringIO(self.path.read_text()), interpolate=False)
        expected = {name for name, _ in ENV_FIELDS.values()}
        if set(values) != expected:
            raise ValueError("Runtime settings file has missing or unknown keys")
        return snapshot(TradingSettings.from_env_values(values))

    def save(self, settings, expected_revision):
        # Validate again at the storage boundary, including callers outside HTTP.
        settings = TradingSettings.model_validate(settings.model_dump())
        with self.lock():
            if self.read().revision != expected_revision:
                raise SettingsConflict("Settings changed in another session. Reload before saving.")
            atomic_text(self.path, serialize_settings(settings))
        return snapshot(settings)
