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


# Backwards-compatible import for the shared validated settings model.
from live_settings import TradingSettings as TraderSettings
