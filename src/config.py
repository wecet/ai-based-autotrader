import pydantic
import enum
import utils


class MODELS_(enum.Enum):
    SENTIMENT = 1
    TIMESERIES = 2
    REINFORCEMENTLEARNING = 3
    CROSSSECTIONAL = 4
    MARKETREGIME = 5


class Config(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,  # Accept Account instances as field values
        strict=True,           # Disables type coercion
        extra='forbid',        # Rejects undefined fields
        validate_default=True  # Validates default values
    )
    model: MODELS_
    account: utils.Account


class TraderSettings(pydantic.BaseModel):
    """Validated strategy settings; broker construction remains at the entry boundary."""
    universe: dict[str, str]
    feed: str = "iex"

    @classmethod
    def from_environment(cls):
        import json
        import os
        from alpaca.data.enums import DataFeed

        universe = json.loads(os.environ.get("TRADER_UNIVERSE", "{}"))
        if not isinstance(universe, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in universe.items()
        ):
            raise ValueError("TRADER_UNIVERSE must be a JSON object of stock:sector-ETF pairs")
        universe = {key.strip().upper(): value.strip().upper() for key, value in universe.items()}
        if len(universe) < 2 or any(not key or not value for key, value in universe.items()):
            raise ValueError("Set TRADER_UNIVERSE to at least two nonempty stock:sector-ETF pairs")
        feed = DataFeed(os.getenv("TRADER_DATA_FEED", "iex"))
        return cls(universe=universe, feed=feed.value)
