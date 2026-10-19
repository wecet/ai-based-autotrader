"""Expanding walk-forward evaluation with next-session-open execution proxies."""
from dataclasses import asdict, dataclass
import math

import numpy as np

from core.types import MarketHistory, PortfolioLimits


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    warmup_sessions: int = 260
    rebalance_sessions: int = 1
    delay_sessions: int = 0
    spread_bps: float = 5.0
    slippage_bps: float = 5.0
    fee_bps: float = 0.0
    volume_participation: float = .01

    def __post_init__(self):
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("Initial cash must be finite and positive")
        if any(type(value) is not int for value in (self.warmup_sessions, self.rebalance_sessions, self.delay_sessions)):
            raise ValueError("Evaluation cadences must be integers")
        if self.warmup_sessions < 1 or self.rebalance_sessions < 1 or self.delay_sessions < 0:
            raise ValueError("Invalid evaluation cadence")
        costs = (self.spread_bps, self.slippage_bps, self.fee_bps)
        if any(not math.isfinite(x) or x < 0 for x in costs) or sum(costs) >= 10_000:
            raise ValueError("Costs must be finite, non-negative and below 100%")
        if not 0 < self.volume_participation <= 1:
            raise ValueError("Volume participation must be in (0, 1]")


def validate_history(history):
    for bars in [history.spy, *history.stocks.values(), *history.sectors.values()]:
        dates = [b.date for b in bars]
        if dates != sorted(set(dates)):
            raise ValueError("Bars must have unique, increasing dates")
        for bar in bars:
            prices = (bar.open, bar.high, bar.low, bar.close)
            if any(not math.isfinite(p) or p <= 0 for p in prices):
                raise ValueError("OHLC prices must be finite and positive")
            if not math.isfinite(bar.volume) or bar.volume < 0:
                raise ValueError("Volume must be finite and non-negative")
            if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
                raise ValueError("OHLC prices are inconsistent")


def history_before(history, day):
    def past(bars):
        return [b for b in bars if b.date < day]
    spy = past(history.spy)
    dates = [b.date for b in spy]
    sectors = {symbol: past(bars) for symbol, bars in history.sectors.items()}
    stocks = {symbol: past(bars) for symbol, bars in history.stocks.items()}
    stocks = {symbol: bars for symbol, bars in stocks.items() if [b.date for b in bars] == dates}
    return MarketHistory(stocks, spy, sectors)


def metrics(snapshots, fills, initial_cash):
    equity = np.asarray([initial_cash, *[s["equity"] for s in snapshots]], dtype=float)
    returns = np.diff(equity) / equity[:-1]
    deviation = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    peaks = np.maximum.accumulate(equity)
    total = float(equity[-1] / equity[0] - 1)
    return {
        "net_return": total,
        "annualized_return": float((equity[-1] / equity[0]) ** (252 / len(returns)) - 1),
        "annualized_volatility": deviation * math.sqrt(252),
        "sharpe_zero_cash_rate": float(np.mean(returns) / deviation * math.sqrt(252)) if deviation > 0 else None,
        "maximum_drawdown": float(np.min(equity / peaks - 1)),
        "worst_session_return": float(np.min(returns)),
        "turnover_over_initial_equity": sum(f["notional"] for f in fills) / initial_cash,
        "average_exposure": float(np.mean([s["exposure"] for s in snapshots])),
        "fees": sum(f["fee"] for f in fills),
        "execution_impact": sum(f["impact"] for f in fills),
        "fill_count": len(fills),
    }


def run_backtest(model_factory, history, settings, config=BacktestConfig(), *, order_allowed=None):
    """Fit on past bars only; model exceptions are explicit abstention events.

    Split-adjusted OHLCV is an execution proxy, not a corporate-action-aware
    ledger. Daily bars cannot resolve intraday order timing or actual fillability.
    """
    validate_history(history)
    if len(history.spy) <= config.warmup_sessions + config.delay_sessions:
        raise ValueError("Need evaluation sessions after warmup and execution delay")
    universe = settings.universe
    limits = PortfolioLimits(settings.maximum_total_exposure, settings.maximum_sector_exposure,
                             settings.maximum_position, settings.top_n)
    lookup = {symbol: {b.date: b for b in bars} for symbol, bars in history.stocks.items()}
    cash, positions, marks = config.initial_cash, {}, {}
    fills, snapshots, events = [], [], []
    model = None
    ready = False
    impact_rate = (config.spread_bps + config.slippage_bps) / 10_000
    fee_rate = config.fee_bps / 10_000
    first = config.warmup_sessions + config.delay_sessions
    for index in range(first, len(history.spy)):
        day = history.spy[index].date
        signal_day = history.spy[index - config.delay_sessions].date
        today = {symbol: bars[day] for symbol, bars in lookup.items() if day in bars}
        # At decision/execution time only the open is known. End-of-day marks
        # are used below solely for end-of-session accounting.
        open_marks = {**marks, **{symbol: bar.open for symbol, bar in today.items()}}
        equity_open = cash + sum(qty * open_marks[symbol] for symbol, qty in positions.items())
        if (index - first) % config.rebalance_sessions == 0:
            model = model_factory()
            model.configure(settings)
            snapshot = history_before(history, signal_day)
            # Match the live provider's requirement that candidate and group
            # histories contain exactly the benchmark's completed sessions.
            dates = [b.date for b in snapshot.spy]
            snapshot = MarketHistory({s: bars for s, bars in snapshot.stocks.items()
                                      if s in universe and [b.date for b in snapshot.sectors.get(universe[s], [])] == dates},
                                     snapshot.spy, snapshot.sectors)
            try:
                model.prepare(snapshot, universe)
                ready = True
            except ValueError as error:
                ready = False
                events.append({"date": day.isoformat(), "reason": "prepare_unavailable", "detail": str(error)})
        if ready and model is not None and settings.trading_enabled:
            weights = {s: qty * open_marks[s] / equity_open for s, qty in positions.items()}
            candidates = model.candidates(weights, equity_open, limits)
            candidates = sorted(candidates, key=lambda c: (c.desired_weight >= c.current_weight, c.symbol))
            attempts, seen = 0, set()
            for candidate in candidates:
                buy, sell = model.decide_to_buy(candidate), model.decide_to_sell(candidate)
                if buy and sell:
                    raise ValueError("Conflicting model decisions")
                if not (buy or sell) or attempts >= settings.max_daily_trades:
                    continue
                if not math.isfinite(candidate.desired_weight) or candidate.desired_weight < 0:
                    raise ValueError("Invalid target weight")
                fingerprint = (candidate.symbol, "buy" if buy else "sell", round(candidate.desired_weight, 8))
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                bar = today.get(candidate.symbol)
                if bar is None:
                    events.append({"date": day.isoformat(), "symbol": candidate.symbol, "reason": "missing_execution_bar"})
                    continue
                side = "buy" if buy else "sell"
                price = bar.open * (1 + impact_rate if buy else 1 - impact_rate)
                notional = candidate.order_notional
                if settings.maximum_order_notional is not None:
                    notional = min(notional, settings.maximum_order_notional)
                # Use previous completed volume, never the future session's
                # volume, for the participation cap available at the open.
                prior = [b for b in history.stocks[candidate.symbol] if b.date < day]
                capacity = prior[-1].volume * config.volume_participation if prior else 0
                quantity = min(notional / price, capacity)
                quantity = min(quantity, cash / (price * (1 + fee_rate)) if buy else positions.get(candidate.symbol, 0))
                notional = math.floor(quantity * price * 100) / 100
                if notional < settings.minimum_order_notional:
                    events.append({"date": day.isoformat(), "symbol": candidate.symbol, "reason": "below_minimum_or_no_capacity"})
                    continue
                quantity = notional / price
                attempts += 1
                if order_allowed is not None and not order_allowed(candidate, day):
                    events.append({"date": day.isoformat(), "symbol": candidate.symbol, "reason": "rejected_order"})
                    continue
                fee = notional * fee_rate
                cash += -notional - fee if buy else notional - fee
                positions[candidate.symbol] = positions.get(candidate.symbol, 0) + (quantity if buy else -quantity)
                if positions[candidate.symbol] < 1e-10:
                    positions.pop(candidate.symbol)
                fills.append({"date": day.isoformat(), "signal_cutoff": signal_day.isoformat(),
                              "symbol": candidate.symbol, "side": side, "quantity": quantity,
                              "price": price, "notional": notional, "fee": fee,
                              "impact": quantity * abs(price - bar.open),
                              "partial": notional + .01 < candidate.order_notional})
        marks.update({symbol: bar.close for symbol, bar in today.items()})
        value = sum(qty * marks[symbol] for symbol, qty in positions.items())
        equity = cash + value
        if cash < -1e-6 or equity <= 0 or not math.isfinite(equity):
            raise ValueError("Invalid portfolio accounting")
        snapshots.append({"date": day.isoformat(), "cash": cash, "equity": equity,
                          "positions": dict(positions), "exposure": value / equity,
                          "stale_marks": sorted(s for s in positions if s not in today)})
    result = metrics(snapshots, fills, config.initial_cash)
    result["spy_price_return"] = history.spy[-1].close / history.spy[first].open - 1
    result["excess_price_return"] = result["net_return"] - result["spy_price_return"]
    return {"config": asdict(config), "settings": settings.model_dump(), "metrics": result,
            "snapshots": snapshots, "fills": fills, "events": events,
            "limitations": ["Static universe: survivorship bias is not corrected.",
                            "Split-adjusted price returns omit dividends, taxes and cash interest.",
                            "Daily opens approximate fills; intraday timing, market impact and broker rejection probabilities are not modeled.",
                            "Liquidity uses prior completed volume; partial orders are not carried as pending broker orders.",
                            "These walk-forward results are development evidence; preserve an untouched dataset for final evaluation."]}
