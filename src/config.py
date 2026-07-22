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
        strict=True,           # Disables type coercion
        extra='forbid',        # Rejects undefined fields
        validate_default=True  # Validates default values
    )
    model: MODELS_
    account: utils.Account
