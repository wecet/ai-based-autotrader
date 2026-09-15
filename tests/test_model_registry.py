from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from alpaca.data.enums import DataFeed
from fastapi.testclient import TestClient
import pytest

from configuration.settings import LiveSettingsStore, TradingSettings, serialize_settings
from core.contracts import Model, Trader
from core.registry import ModelRegistry, UnknownModel
from core.types import MarketHistory, TradeCandidate
from dashboard.app import create_app
from data.historical import NY
from trading.execution import OrderExecutor
from trading.runner import Autotrader
from trading.signals import DailySignals
from infrastructure.journal import OrderJournal


PLUGIN = '''
from core.contracts import Model
from core.registry import ModelDefinition
from core.types import TradeCandidate

class ExampleModel(Model):
    def __init__(self):
        self.prepared = False

    def prepare(self, history, universe):
        self.prepared = True
        self.symbol = next(iter(universe))
        self.sector = universe[self.symbol]

    def candidates(self, current_weights, portfolio_value, limits):
        assert self.prepared
        return [TradeCandidate(self.symbol, .03, 1, 100, self.sector,
                limits.maximum_position, current_weights.get(self.symbol, 0), portfolio_value)]

    def decide_to_buy(self, candidate):
        return candidate.desired_weight > candidate.current_weight

    def decide_to_sell(self, candidate):
        return candidate.desired_weight < candidate.current_weight

MODEL = ModelDefinition(name="NAME", description="DESCRIPTION", model_type=ExampleModel)
'''


@pytest.fixture
def registry(tmp_path):
    directory = tmp_path / 'models'
    directory.mkdir()
    (directory / 'alpha.py').write_text(PLUGIN.replace('NAME', 'Alpha strategy').replace('DESCRIPTION', 'First model explanation.'))
    return ModelRegistry(directory)


def test_discovery_uses_file_metadata_and_not_hardcoded_names(registry):
    catalog = registry.catalog()
    assert catalog['models'] == [{'id': 'alpha', 'name': 'Alpha strategy',
                                  'description': 'First model explanation.', 'file': 'alpha.py'}]
    assert isinstance(registry.create('alpha'), Model)
    (registry.directory / 'beta.py').write_text(PLUGIN.replace('NAME', 'Second strategy').replace('DESCRIPTION', 'Second explanation.'))
    assert [item['id'] for item in registry.catalog()['models']] == ['alpha', 'beta']
    with pytest.raises(UnknownModel):
        registry.create('../../arbitrary-file')


def test_invalid_files_are_reported_without_hiding_valid_models(registry):
    (registry.directory / 'missing.py').write_text('VALUE = 3\n')
    (registry.directory / 'broken.py').write_text('this is invalid python!')
    (registry.directory / 'abstract.py').write_text('from core.contracts import Model\nfrom core.registry import ModelDefinition\nMODEL = ModelDefinition("Incomplete", "Not implemented", Model)\n')
    (registry.directory / '_ignored.py').write_text('raise RuntimeError("must not import")')
    catalog = registry.catalog()
    assert len(catalog['models']) == 1
    assert {item['file'] for item in catalog['unavailable']} == {'missing.py', 'broken.py', 'abstract.py'}


def test_old_runtime_settings_gain_default_model_without_losing_limits(tmp_path):
    settings = TradingSettings(universe={'AAA': 'XLK', 'BBB': 'XLF'}, max_daily_trades=4,
                               maximum_order_notional=37.5, trading_enabled=False)
    old = '\n'.join(line for line in serialize_settings(settings).splitlines() if not line.startswith('TRADER_MODEL=')) + '\n'
    store = LiveSettingsStore(tmp_path)
    store.path.write_text(old)
    recovered = store.read().settings
    assert recovered == settings
    assert recovered.model_id == 'cross_sectional'


def test_dashboard_lists_model_metadata_and_rejects_unavailable_selection(registry, tmp_path):
    settings = TradingSettings(universe={'AAA': 'XLK', 'BBB': 'XLF'}, model_id='alpha')
    store = LiveSettingsStore(tmp_path / 'control', lambda: settings, registry=registry)
    with TestClient(create_app(store, tmp_path, 'key', allowed_hosts=['testserver'])) as client:
        assert client.get('/api/models').status_code == 401
        headers = {'Authorization': 'Bearer key'}
        assert client.get('/api/models', headers=headers).json()['models'][0]['name'] == 'Alpha strategy'
        page = client.get('/').text
        assert 'model-options' in page and 'ONE ACTIVE MODEL' in page
        current = client.get('/api/settings', headers=headers).json()
        assert current['models'][0]['description'] == 'First model explanation.'
        current['settings']['model_id'] = 'missing'
        response = client.put('/api/settings', headers=headers,
                              json={'revision': current['revision'], 'settings': current['settings']})
        assert response.status_code == 422
        assert not store.path.exists()


def test_live_switch_prepares_new_model_preserving_journal_and_deduplication(registry, tmp_path):
    (registry.directory / 'beta.py').write_text(PLUGIN.replace('NAME', 'Beta').replace('DESCRIPTION', 'Other algorithm.'))
    settings = TradingSettings(universe={'AAA': 'XLK', 'BBB': 'XLF'}, model_id='alpha')
    store = LiveSettingsStore(tmp_path / 'control', lambda: settings, registry=registry)
    history = Mock()
    history.feed = DataFeed.IEX
    history.load.return_value = MarketHistory({}, [], {})
    signals = DailySignals(history, settings.universe, registry=registry, model_id='alpha')
    broker = Mock()
    broker.get_clock.return_value = SimpleNamespace(is_open=True, timestamp=datetime(2026, 9, 14, 10, tzinfo=NY))
    broker.get_orders.return_value = []
    broker.get_account.return_value = SimpleNamespace(trading_blocked=False, equity='1000', buying_power='1000')
    broker.get_all_positions.return_value = []
    broker.get_asset.return_value = SimpleNamespace(tradable=True, fractionable=True)
    runner = Autotrader(SimpleNamespace(trading_client=broker), None, settings.universe,
                       tmp_path, signals=signals, settings=settings, settings_store=store)
    runner.run_once()
    first_model = signals.model
    assert first_model.prepared
    assert broker.submit_order.call_count == 1
    updated = TradingSettings.model_validate({**settings.model_dump(), 'model_id': 'beta'})
    store.save(updated, store.read().revision)
    runner.run_once()
    assert signals.model is not first_model and signals.model_id == 'beta'
    assert signals.model.prepared
    assert history.load.call_count == 2
    assert runner.executor.model is signals.model
    assert broker.submit_order.call_count == 1
    assert len(runner.journal.read('2026-09-14')['attempts']) == 1


def test_production_executor_uses_trader_conflict_guard(tmp_path):
    client = Mock()
    journal = OrderJournal(tmp_path / 'orders.json')
    executor = OrderExecutor(SimpleNamespace(trading_client=client), journal)
    assert isinstance(executor, Trader)
    model = Mock(spec=Model)
    model.decide_to_buy.return_value = True
    model.decide_to_sell.return_value = True
    candidate = TradeCandidate('AAA', .02, 1, 100, 'XLK', .1, portfolio_value=1000)
    with pytest.raises(ValueError, match='same time'):
        executor.submit_next([candidate], model, journal.read('2026-09-14'), 1000)
    client.submit_order.assert_not_called()
    assert not journal.state_path.exists()
    assert executor._state is None


def test_unavailable_model_does_not_replace_active_model(registry):
    settings = TradingSettings(universe={'AAA': 'XLK', 'BBB': 'XLF'}, model_id='alpha')
    history = Mock()
    history.feed = DataFeed.IEX
    signals = DailySignals(history, settings.universe, registry=registry, model_id='alpha')
    previous = signals.model
    updated = TradingSettings.model_validate({**settings.model_dump(), 'model_id': 'missing'})
    with pytest.raises(UnknownModel):
        signals.configure(updated)
    assert signals.model is previous and signals.model_id == 'alpha'


def test_constructor_requiring_arguments_is_not_offered(registry):
    broken = PLUGIN.replace('def __init__(self):', 'def __init__(self, required_argument):')
    (registry.directory / 'requires_argument.py').write_text(broken)
    catalog = registry.catalog()
    assert [item['id'] for item in catalog['models']] == ['alpha']
    assert catalog['unavailable'][0]['file'] == 'requires_argument.py'
