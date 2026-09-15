import pytest
from pydantic import ValidationError


def test_config_accepts_account():
    from configuration.legacy import Config, MODELS_
    from brokers.account import Account
    account = Account()
    config = Config(model=MODELS_.CROSSSECTIONAL, account=account)
    assert config.account is account
    assert config.model is MODELS_.CROSSSECTIONAL


@pytest.mark.parametrize("changes", [{"model": 4}, {"extra": True}, {"account": "invalid"}])
def test_config_rejects_invalid_fields(changes):
    from configuration.legacy import Config, MODELS_
    from brokers.account import Account
    with pytest.raises(ValidationError):
        Config(**{"model": MODELS_.CROSSSECTIONAL, "account": Account(), **changes})
