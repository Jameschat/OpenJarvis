"""Paper-only strategy backtests for Jarvis Markets Bot Lab."""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["DCAConfig", "backtest_dca", "backtest_dca_from_history"]
