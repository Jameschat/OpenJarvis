"""Paper-only strategy backtests for Jarvis Markets Bot Lab."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Iterable


@dataclass(frozen=True)
class DCAConfig:
    ticker: str
    initial_cash_gbp: float = 1000.0
    base_order_gbp: float = 100.0
    safety_order_gbp: float = 100.0
    max_safety_orders: int = 3
    safety_order_deviation_pct: float = 3.0
    take_profit_pct: float = 2.0
    stop_loss_pct: float | None = None
    fee_rate: float = 0.001
    slippage_pct: float = 0.05


@dataclass(frozen=True)
class GridConfig:
    ticker: str
    initial_cash_gbp: float = 1000.0
    lower_price: float = 90.0
    upper_price: float = 110.0
    grid_count: int = 10
    order_gbp: float = 100.0
    fee_rate: float = 0.001
    slippage_pct: float = 0.05


def _as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _normalise_bars(bars: Iterable[dict[str, Any]]) -> list[dict[str, float]]:
    normalised: list[dict[str, float]] = []
    for bar in bars:
        normalised.append(
            {
                "ts": _as_float(bar.get("ts"), "bar.ts"),
                "open": _as_float(bar.get("open"), "bar.open"),
                "high": _as_float(bar.get("high"), "bar.high"),
                "low": _as_float(bar.get("low"), "bar.low"),
                "close": _as_float(bar.get("close"), "bar.close"),
                "volume": _as_float(bar.get("volume", 0.0), "bar.volume"),
            }
        )
    normalised.sort(key=lambda item: item["ts"])
    return normalised


def _validate(config: DCAConfig, bars: list[dict[str, float]]) -> None:
    if len(bars) < 2:
        raise ValueError("DCA backtest requires at least two bars")
    if not config.ticker:
        raise ValueError("ticker is required")
    if config.initial_cash_gbp <= 0:
        raise ValueError("initial_cash_gbp must be greater than zero")
    if config.base_order_gbp <= 0:
        raise ValueError("base_order_gbp must be greater than zero")
    if config.safety_order_gbp < 0:
        raise ValueError("safety_order_gbp must be zero or greater")
    if config.max_safety_orders < 0:
        raise ValueError("max_safety_orders must be zero or greater")
    if config.safety_order_deviation_pct <= 0:
        raise ValueError("safety_order_deviation_pct must be greater than zero")
    if config.take_profit_pct <= 0:
        raise ValueError("take_profit_pct must be greater than zero")
    if config.stop_loss_pct is not None and config.stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be greater than zero when set")
    if not 0 <= config.fee_rate < 1:
        raise ValueError("fee_rate must be between 0 and 1")
    if config.slippage_pct < 0:
        raise ValueError("slippage_pct must be zero or greater")


def _validate_grid(config: GridConfig, bars: list[dict[str, float]]) -> None:
    if len(bars) < 2:
        raise ValueError("Grid backtest requires at least two bars")
    if not config.ticker:
        raise ValueError("ticker is required")
    if config.initial_cash_gbp <= 0:
        raise ValueError("initial_cash_gbp must be greater than zero")
    if config.lower_price <= 0:
        raise ValueError("lower_price must be greater than zero")
    if config.upper_price <= config.lower_price:
        raise ValueError("upper_price must be greater than lower_price")
    if config.grid_count < 2:
        raise ValueError("grid_count must be at least 2")
    if config.order_gbp <= 0:
        raise ValueError("order_gbp must be greater than zero")
    if not 0 <= config.fee_rate < 1:
        raise ValueError("fee_rate must be between 0 and 1")
    if config.slippage_pct < 0:
        raise ValueError("slippage_pct must be zero or greater")


def _round_money(value: float) -> float:
    return round(value, 2)


def _round_price(value: float) -> float:
    return round(value, 8)


def _round_qty(value: float) -> float:
    return round(value, 10)


def _buy(cash: float, amount_gbp: float, price: float, config: DCAConfig) -> tuple[float, float, float]:
    amount = min(cash, amount_gbp)
    fee = amount * config.fee_rate
    quantity = (amount - fee) / price
    return cash - amount, quantity, fee


def _sell(quantity: float, price: float, config: DCAConfig) -> tuple[float, float, float]:
    gross = quantity * price
    fee = gross * config.fee_rate
    return gross - fee, gross, fee


def _trade(ts: float, side: str, kind: str, price: float, quantity: float, gross: float, fee: float, deal_id: int) -> dict[str, Any]:
    return {
        "ts": int(ts),
        "side": side,
        "kind": kind,
        "price": _round_price(price),
        "quantity": _round_qty(quantity),
        "gross_gbp": _round_money(gross),
        "fee_gbp": _round_money(fee),
        "deal_id": deal_id,
    }


def backtest_dca(bars: Iterable[dict[str, Any]], config: DCAConfig) -> dict[str, Any]:
    """Backtest a simple one-deal-at-a-time DCA bot against OHLCV bars.

    This is intentionally paper-only. It estimates fills from candle highs/lows
    and should be treated as a strategy filter before paper trading, not proof
    of live profitability.
    """

    series = _normalise_bars(bars)
    _validate(config, series)

    slip = config.slippage_pct / 100.0
    cash = config.initial_cash_gbp
    deal_id = 1
    base_entry = series[0]["close"] * (1.0 + slip)
    cash, base_quantity, base_fee = _buy(cash, config.base_order_gbp, base_entry, config)
    deal = {
        "deal_id": deal_id,
        "opened_ts": int(series[0]["ts"]),
        "base_entry_price": base_entry,
        "quantity": base_quantity,
        "cost_gbp": config.base_order_gbp,
        "fees_gbp": base_fee,
        "safety_orders_used": 0,
        "max_unrealized_loss_gbp": 0.0,
    }
    trades = [
        _trade(series[0]["ts"], "buy", "base_order", base_entry, base_quantity, config.base_order_gbp, base_fee, deal_id)
    ]
    deals: list[dict[str, Any]] = []
    peak_equity = config.initial_cash_gbp
    max_drawdown_pct = 0.0
    max_floating_drawdown_pct = 0.0
    max_capital_at_risk = deal["cost_gbp"]

    for bar in series[1:]:
        avg_entry = deal["cost_gbp"] / deal["quantity"]
        stop_price = avg_entry * (1.0 - (config.stop_loss_pct or 0.0) / 100.0)
        tp_price = avg_entry * (1.0 + config.take_profit_pct / 100.0)

        if config.stop_loss_pct is not None and bar["low"] <= stop_price:
            fill_price = stop_price * (1.0 - slip)
            net, gross, fee = _sell(deal["quantity"], fill_price, config)
            cash += net
            pnl = net - deal["cost_gbp"]
            trades.append(_trade(bar["ts"], "sell", "stop_loss", fill_price, deal["quantity"], gross, fee, deal_id))
            deal.update(
                {
                    "closed_ts": int(bar["ts"]),
                    "close_reason": "stop_loss",
                    "exit_price": fill_price,
                    "realized_pnl_gbp": pnl,
                    "fees_gbp": deal["fees_gbp"] + fee,
                }
            )
            deals.append(_summarise_deal(deal))
            deal = None
        elif bar["high"] >= tp_price:
            fill_price = tp_price * (1.0 - slip)
            net, gross, fee = _sell(deal["quantity"], fill_price, config)
            cash += net
            pnl = net - deal["cost_gbp"]
            trades.append(_trade(bar["ts"], "sell", "take_profit", fill_price, deal["quantity"], gross, fee, deal_id))
            deal.update(
                {
                    "closed_ts": int(bar["ts"]),
                    "close_reason": "take_profit",
                    "exit_price": fill_price,
                    "realized_pnl_gbp": pnl,
                    "fees_gbp": deal["fees_gbp"] + fee,
                }
            )
            deals.append(_summarise_deal(deal))
            deal = None
        elif deal["safety_orders_used"] < config.max_safety_orders and config.safety_order_gbp > 0:
            next_order = deal["safety_orders_used"] + 1
            trigger = deal["base_entry_price"] * (1.0 - (config.safety_order_deviation_pct * next_order) / 100.0)
            if bar["low"] <= trigger and cash > 0:
                fill_price = trigger * (1.0 + slip)
                amount = min(cash, config.safety_order_gbp)
                cash, quantity, fee = _buy(cash, amount, fill_price, config)
                deal["quantity"] += quantity
                deal["cost_gbp"] += amount
                deal["fees_gbp"] += fee
                deal["safety_orders_used"] = next_order
                trades.append(_trade(bar["ts"], "buy", "safety_order", fill_price, quantity, amount, fee, deal_id))
                max_capital_at_risk = max(max_capital_at_risk, deal["cost_gbp"])

        if deal is None:
            equity = cash
        else:
            mark_value = deal["quantity"] * bar["close"] * (1.0 - config.fee_rate)
            equity = cash + mark_value
            floating_pnl = mark_value - deal["cost_gbp"]
            deal["max_unrealized_loss_gbp"] = min(deal["max_unrealized_loss_gbp"], floating_pnl)
            if deal["cost_gbp"] > 0:
                max_floating_drawdown_pct = max(max_floating_drawdown_pct, abs(min(0.0, floating_pnl)) / deal["cost_gbp"] * 100.0)

        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak_equity - equity) / peak_equity * 100.0)

    open_deals = 0
    unrealized_pnl = 0.0
    capital_locked = 0.0
    if deal is not None:
        last = series[-1]
        mark_value = deal["quantity"] * last["close"] * (1.0 - config.fee_rate)
        unrealized_pnl = mark_value - deal["cost_gbp"]
        capital_locked = deal["cost_gbp"]
        open_deals = 1
        open_summary = _summarise_deal(
            {
                **deal,
                "closed_ts": None,
                "close_reason": "open",
                "exit_price": last["close"],
                "realized_pnl_gbp": 0.0,
                "unrealized_pnl_gbp": unrealized_pnl,
            }
        )
        deals.append(open_summary)

    realized_pnl = sum(item["realized_pnl_gbp"] for item in deals if item["close_reason"] != "open")
    ending_equity = cash + capital_locked + unrealized_pnl
    closed_pnls = [item["realized_pnl_gbp"] for item in deals if item["close_reason"] != "open"]
    wins = [pnl for pnl in closed_pnls if pnl > 0]
    losses = [pnl for pnl in closed_pnls if pnl <= 0]
    roi_pct = (ending_equity - config.initial_cash_gbp) / config.initial_cash_gbp * 100.0

    return {
        "ok": True,
        "strategy": "dca",
        "ticker": config.ticker,
        "bars": len(series),
        "first_ts": int(series[0]["ts"]),
        "last_ts": int(series[-1]["ts"]),
        "initial_cash_gbp": _round_money(config.initial_cash_gbp),
        "ending_equity_gbp": _round_money(ending_equity),
        "cash_gbp": _round_money(cash),
        "realized_pnl_gbp": _round_money(realized_pnl),
        "unrealized_pnl_gbp": _round_money(unrealized_pnl),
        "roi_pct": round(roi_pct, 2),
        "closed_deals": len(closed_pnls),
        "open_deals": open_deals,
        "win_rate_pct": round((len(wins) / len(closed_pnls) * 100.0) if closed_pnls else 0.0, 2),
        "avg_win_gbp": _round_money(sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss_gbp": _round_money(sum(losses) / len(losses)) if losses else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "max_floating_drawdown_pct": round(max_floating_drawdown_pct, 2),
        "capital_locked_gbp": _round_money(capital_locked),
        "max_capital_at_risk_gbp": _round_money(max_capital_at_risk),
        "trades": trades,
        "deals": deals,
        "warning": "Backtest estimate only. Results depend on candle granularity, assumed fees, assumed slippage, and do not prove live profitability.",
    }


def _summarise_deal(deal: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "deal_id": deal["deal_id"],
        "opened_ts": deal["opened_ts"],
        "closed_ts": deal.get("closed_ts"),
        "close_reason": deal.get("close_reason", "open"),
        "base_entry_price": _round_price(deal["base_entry_price"]),
        "exit_price": _round_price(deal.get("exit_price", 0.0)),
        "quantity": _round_qty(deal["quantity"]),
        "capital_gbp": _round_money(deal["cost_gbp"]),
        "fees_gbp": _round_money(deal["fees_gbp"]),
        "safety_orders_used": deal["safety_orders_used"],
        "realized_pnl_gbp": _round_money(deal.get("realized_pnl_gbp", 0.0)),
        "max_unrealized_loss_gbp": _round_money(deal.get("max_unrealized_loss_gbp", 0.0)),
    }
    if "unrealized_pnl_gbp" in deal:
        summary["unrealized_pnl_gbp"] = _round_money(deal["unrealized_pnl_gbp"])
    return summary


def backtest_dca_from_history(ticker: str, since_ts: int | None = None, limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
    from openjarvis.markets import store

    bars = store.get_history(ticker, since_ts=since_ts, limit=limit)
    config = DCAConfig(ticker=ticker, **kwargs)
    return backtest_dca(bars, config)


def backtest_grid(bars: Iterable[dict[str, Any]], config: GridConfig) -> dict[str, Any]:
    """Backtest a simple fixed-range spot grid bot against OHLCV bars."""

    series = _normalise_bars(bars)
    _validate_grid(config, series)

    slip = config.slippage_pct / 100.0
    step = (config.upper_price - config.lower_price) / config.grid_count
    buy_levels = [config.lower_price + step * i for i in range(config.grid_count)]
    cash = config.initial_cash_gbp
    open_units: dict[float, dict[str, float]] = {}
    trades: list[dict[str, Any]] = []
    realized_pnl = 0.0
    closed_trades = 0
    peak_equity = config.initial_cash_gbp
    max_drawdown_pct = 0.0
    max_capital_at_risk = 0.0

    for bar in series:
        for level in sorted(list(open_units.keys())):
            target = level + step
            if bar["low"] <= target <= bar["high"]:
                unit = open_units.pop(level)
                fill_price = target * (1.0 - slip)
                net, gross, fee = _sell(unit["quantity"], fill_price, config)
                cash += net
                pnl = net - unit["cost_gbp"]
                realized_pnl += pnl
                closed_trades += 1
                trades.append(_trade(bar["ts"], "sell", "grid_sell", fill_price, unit["quantity"], gross, fee, closed_trades))

        for level in buy_levels:
            if level in open_units:
                continue
            if bar["low"] <= level <= bar["high"] and cash > 0:
                fill_price = level * (1.0 + slip)
                amount = min(cash, config.order_gbp)
                cash, quantity, fee = _buy(cash, amount, fill_price, config)
                open_units[level] = {
                    "quantity": quantity,
                    "cost_gbp": amount,
                    "entry_price": fill_price,
                    "fee_gbp": fee,
                }
                trades.append(_trade(bar["ts"], "buy", "grid_buy", fill_price, quantity, amount, fee, len(trades) + 1))
                max_capital_at_risk = max(max_capital_at_risk, sum(unit["cost_gbp"] for unit in open_units.values()))

        mark_value = sum(unit["quantity"] * bar["close"] * (1.0 - config.fee_rate) for unit in open_units.values())
        equity = cash + mark_value
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak_equity - equity) / peak_equity * 100.0)

    last_close = series[-1]["close"]
    capital_locked = sum(unit["cost_gbp"] for unit in open_units.values())
    mark_value = sum(unit["quantity"] * last_close * (1.0 - config.fee_rate) for unit in open_units.values())
    unrealized_pnl = mark_value - capital_locked
    ending_equity = cash + mark_value
    roi_pct = (ending_equity - config.initial_cash_gbp) / config.initial_cash_gbp * 100.0

    return {
        "ok": True,
        "strategy": "grid",
        "ticker": config.ticker,
        "bars": len(series),
        "first_ts": int(series[0]["ts"]),
        "last_ts": int(series[-1]["ts"]),
        "lower_price": _round_price(config.lower_price),
        "upper_price": _round_price(config.upper_price),
        "grid_count": config.grid_count,
        "grid_step": _round_price(step),
        "initial_cash_gbp": _round_money(config.initial_cash_gbp),
        "ending_equity_gbp": _round_money(ending_equity),
        "cash_gbp": _round_money(cash),
        "realized_pnl_gbp": _round_money(realized_pnl),
        "unrealized_pnl_gbp": _round_money(unrealized_pnl),
        "roi_pct": round(roi_pct, 2),
        "closed_grid_trades": closed_trades,
        "open_grid_orders": len(open_units),
        "capital_locked_gbp": _round_money(capital_locked),
        "max_capital_at_risk_gbp": _round_money(max_capital_at_risk),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "trades": trades,
        "open_orders": [
            {
                "buy_level": _round_price(level),
                "sell_level": _round_price(level + step),
                "quantity": _round_qty(unit["quantity"]),
                "capital_gbp": _round_money(unit["cost_gbp"]),
                "entry_price": _round_price(unit["entry_price"]),
            }
            for level, unit in sorted(open_units.items())
        ],
        "warning": "Grid backtest estimate only. Candle order inside each bar is unknown; results depend on assumed fills, fees, slippage, and cached history quality.",
    }


def backtest_grid_from_history(ticker: str, since_ts: int | None = None, limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
    from openjarvis.markets import store

    bars = store.get_history(ticker, since_ts=since_ts, limit=limit)
    config = GridConfig(ticker=ticker, **kwargs)
    return backtest_grid(bars, config)


def _coerce_values(values: Iterable[Any] | None, default: list[Any], name: str, *, max_items: int = 8) -> list[Any]:
    if values is None:
        return list(default)
    out = list(values)
    if not out:
        return list(default)
    if len(out) > max_items:
        raise ValueError(f"{name} supports at most {max_items} values")
    return out


def _score_result(result: dict[str, Any]) -> float:
    return round(float(result.get("roi_pct") or 0.0) - float(result.get("max_drawdown_pct") or 0.0) * 0.5, 4)


def _sweep_item(config: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": _score_result(result),
        "config": config,
        "roi_pct": result.get("roi_pct"),
        "realized_pnl_gbp": result.get("realized_pnl_gbp"),
        "unrealized_pnl_gbp": result.get("unrealized_pnl_gbp"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "closed_trades": result.get("closed_deals", result.get("closed_grid_trades", 0)),
        "open_trades": result.get("open_deals", result.get("open_grid_orders", 0)),
        "capital_locked_gbp": result.get("capital_locked_gbp"),
    }


def sweep_dca(
    bars: Iterable[dict[str, Any]],
    *,
    ticker: str,
    take_profit_pct_values: Iterable[float] | None = None,
    safety_order_deviation_pct_values: Iterable[float] | None = None,
    max_safety_orders_values: Iterable[int] | None = None,
    base_order_gbp: float = 100.0,
    safety_order_gbp: float = 100.0,
    initial_cash_gbp: float = 1000.0,
    fee_rate: float = 0.001,
    slippage_pct: float = 0.05,
    top_n: int = 5,
) -> dict[str, Any]:
    series = _normalise_bars(bars)
    take_profits = _coerce_values(take_profit_pct_values, [1.0, 2.0, 3.0], "take_profit_pct_values")
    deviations = _coerce_values(safety_order_deviation_pct_values, [2.0, 3.0, 5.0], "safety_order_deviation_pct_values")
    safety_counts = _coerce_values(max_safety_orders_values, [1, 2, 3], "max_safety_orders_values")
    combos = list(product(take_profits, deviations, safety_counts))
    if len(combos) > 128:
        raise ValueError("DCA sweep supports at most 128 runs")
    results = []
    for take_profit, deviation, max_safety in combos:
        config = {
            "take_profit_pct": float(take_profit),
            "safety_order_deviation_pct": float(deviation),
            "max_safety_orders": int(max_safety),
            "base_order_gbp": float(base_order_gbp),
            "safety_order_gbp": float(safety_order_gbp),
        }
        backtest = backtest_dca(
            series,
            DCAConfig(
                ticker=ticker,
                initial_cash_gbp=initial_cash_gbp,
                fee_rate=fee_rate,
                slippage_pct=slippage_pct,
                **config,
            ),
        )
        results.append(_sweep_item(config, backtest))
    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "ok": True,
        "strategy": "dca_sweep",
        "ticker": ticker,
        "runs": len(results),
        "top_results": results[: max(1, int(top_n))],
        "warning": "Sweep results are historical estimates only. A high score can be overfit to the selected cached history.",
    }


def sweep_grid(
    bars: Iterable[dict[str, Any]],
    *,
    ticker: str,
    lower_price_values: Iterable[float] | None = None,
    upper_price_values: Iterable[float] | None = None,
    grid_count_values: Iterable[int] | None = None,
    order_gbp_values: Iterable[float] | None = None,
    initial_cash_gbp: float = 1000.0,
    fee_rate: float = 0.001,
    slippage_pct: float = 0.05,
    top_n: int = 5,
) -> dict[str, Any]:
    series = _normalise_bars(bars)
    last_close = series[-1]["close"] if series else 100.0
    lower_prices = _coerce_values(lower_price_values, [last_close * 0.9], "lower_price_values")
    upper_prices = _coerce_values(upper_price_values, [last_close * 1.1], "upper_price_values")
    grid_counts = _coerce_values(grid_count_values, [6, 10, 14], "grid_count_values")
    order_values = _coerce_values(order_gbp_values, [50.0, 100.0], "order_gbp_values")
    combos = list(product(lower_prices, upper_prices, grid_counts, order_values))
    if len(combos) > 128:
        raise ValueError("Grid sweep supports at most 128 runs")
    results = []
    for lower, upper, grid_count, order_gbp in combos:
        config = {
            "lower_price": float(lower),
            "upper_price": float(upper),
            "grid_count": int(grid_count),
            "order_gbp": float(order_gbp),
        }
        try:
            backtest = backtest_grid(
                series,
                GridConfig(
                    ticker=ticker,
                    initial_cash_gbp=initial_cash_gbp,
                    fee_rate=fee_rate,
                    slippage_pct=slippage_pct,
                    **config,
                ),
            )
        except ValueError:
            continue
        results.append(_sweep_item(config, backtest))
    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "ok": True,
        "strategy": "grid_sweep",
        "ticker": ticker,
        "runs": len(results),
        "top_results": results[: max(1, int(top_n))],
        "warning": "Sweep results are historical estimates only. A high score can be overfit to the selected cached history.",
    }


def sweep_dca_from_history(ticker: str, since_ts: int | None = None, limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
    from openjarvis.markets import store

    bars = store.get_history(ticker, since_ts=since_ts, limit=limit)
    return sweep_dca(bars, ticker=ticker, **kwargs)


def sweep_grid_from_history(ticker: str, since_ts: int | None = None, limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
    from openjarvis.markets import store

    bars = store.get_history(ticker, since_ts=since_ts, limit=limit)
    return sweep_grid(bars, ticker=ticker, **kwargs)


__all__ = [
    "DCAConfig",
    "GridConfig",
    "backtest_dca",
    "backtest_dca_from_history",
    "backtest_grid",
    "backtest_grid_from_history",
    "sweep_dca",
    "sweep_dca_from_history",
    "sweep_grid",
    "sweep_grid_from_history",
]
