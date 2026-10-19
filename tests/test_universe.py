import json

from dotenv import dotenv_values

from configuration.settings import LiveSettingsStore, TradingSettings
from configuration.universe import apply_candidates, candidate_catalog, merge_candidates


def test_catalog_has_balanced_categories_and_requested_symbols():
    catalog, candidates = candidate_catalog()
    assert len(candidates) == 230
    assert {k: len(v) for k, v in catalog['categories'].items()} == {
        'equity_etfs': 58, 'established_stocks': 136, 'specialist_stocks': 29, 'commodity_funds': 7}
    assert {'AAPL', 'NVDA', 'ASML', 'GOOGL', 'MSFT', 'GLD', 'SLV'} <= candidates.keys()
    assert 'GOOG' not in candidates  # Avoid deliberately doubling one company's share classes.


def test_existing_benchmarks_and_custom_symbols_are_preserved():
    result = merge_candidates({'AAPL': 'QQQ', 'CUSTOM': 'SPY'})
    assert result['AAPL'] == 'QQQ' and result['CUSTOM'] == 'SPY'
    assert len(result) == 231


def test_apply_keeps_secrets_risk_settings_and_runtime_precedence(tmp_path):
    env = tmp_path / '.env'
    env.write_text('APCA_API_SECRET_KEY=private-fixture\nTRADER_ENABLED=false\nTRADER_UNIVERSE=\'{"AAA":"SPY","BBB":"SPY"}\'\n')
    store = LiveSettingsStore(tmp_path / 'control', lambda: TradingSettings(universe={'AAA': 'SPY', 'BBB': 'SPY'}))
    settings = TradingSettings(universe={'AAPL': 'QQQ', 'CUSTOM': 'SPY'}, trading_enabled=False,
                               max_daily_trades=2, maximum_order_notional=50.0)
    store.save(settings, store.read().revision)
    total, added = apply_candidates(env, store.directory)
    values = dotenv_values(env)
    assert values['APCA_API_SECRET_KEY'] == 'private-fixture'
    assert values['TRADER_ENABLED'] == 'false'
    assert len(json.loads(values['TRADER_UNIVERSE'])) == total == 232
    assert added == 230
    updated = store.read().settings
    assert updated.universe['AAPL'] == 'QQQ'
    assert updated.universe['CUSTOM'] == 'SPY'
    assert not updated.trading_enabled and updated.max_daily_trades == 2
    assert updated.maximum_order_notional == 50


def test_known_ticker_rename_preserves_explicit_benchmark():
    result = merge_candidates({'MMC': 'CUSTOM', 'AAA': 'SPY'})
    assert 'MMC' not in result
    assert result['MRSH'] == 'CUSTOM'
    result = merge_candidates({'MMC': 'CUSTOM', 'MRSH': 'XLF'})
    assert result['MRSH'] == 'XLF'
