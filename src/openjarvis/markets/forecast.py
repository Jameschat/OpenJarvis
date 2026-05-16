"""Deterministic three-path price forecast for Financial Jarvis.

This is probabilistic technical-analysis scaffolding, not a price oracle.
The LLM may explain this output, but it must not invent the forecast.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

_HORIZON_STEPS = {
    "24h": 6,
    "3d": 18,
    "7d": 42,
    "30d": 180,
}


def _fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        pass
    return default


def _round_price(value: float) -> float:
    if abs(value) >= 100:
        return round(value, 2)
    if abs(value) >= 1:
        return round(value, 4)
    return round(value, 8)


def _nearest_above(levels: List[float], price: float, fallback: float) -> float:
    above = sorted([_fnum(x) for x in levels if _fnum(x) > price])
    return above[0] if above else fallback


def _nearest_below(levels: List[float], price: float, fallback: float) -> float:
    below = sorted([_fnum(x) for x in levels if _fnum(x) < price], reverse=True)
    return below[0] if below else fallback


def _normalise_probs(base: float, bull: float, bear: float) -> Dict[str, int]:
    vals = {"base": max(1.0, base), "bull": max(1.0, bull), "bear": max(1.0, bear)}
    total = sum(vals.values())
    raw = {k: int(round(v / total * 100.0)) for k, v in vals.items()}
    drift = 100 - sum(raw.values())
    if drift:
        key = max(raw, key=raw.get)
        raw[key] += drift
    return raw


def _path(start: float, end: float, steps: int, wobble: float, phase: float) -> List[Dict[str, float]]:
    out = []
    for i in range(steps + 1):
        t = i / max(1, steps)
        smooth = t * t * (3.0 - 2.0 * t)
        wave = math.sin(i * 1.7 + phase) * wobble * math.sin(math.pi * t)
        price = start + (end - start) * smooth + wave
        out.append({"i": i, "price": _round_price(price)})
    out[0]["price"] = _round_price(start)
    out[-1]["price"] = _round_price(end)
    return out


def _regime(last: float, ema20: float, ema50: float, rsi: float, atr_pct: float) -> str:
    if atr_pct > 0.08:
        return "volatile"
    if ema20 > ema50 and last >= ema20 and rsi >= 52:
        return "trend_up"
    if ema20 < ema50 and last <= ema20 and rsi <= 48:
        return "trend_down"
    return "range"


def generate_forecast(
    bars: List[Dict[str, Any]],
    indicators: Dict[str, Any],
    *,
    timeframe: str = "4h",
    horizon: str = "3d",
    risk: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    steps = _HORIZON_STEPS.get((horizon or "3d").lower(), 18)
    last = _fnum(indicators.get("last") or (bars[-1].get("close") if bars else None))
    if len(bars) < 20 or last <= 0:
        return {
            "available": False,
            "reason": "not enough OHLCV bars for forecast",
            "timeframe": timeframe,
            "horizon": horizon,
            "steps": steps,
            "scenarios": [],
        }

    atr = _fnum(indicators.get("atr"), last * 0.02)
    if atr <= 0:
        atr = last * 0.02
    ema20 = _fnum(indicators.get("ema20_last"), last)
    ema50 = _fnum(indicators.get("ema50_last"), last)
    rsi = _fnum(indicators.get("rsi_last"), 50.0)
    support = [_fnum(x) for x in (indicators.get("support") or [])]
    resistance = [_fnum(x) for x in (indicators.get("resistance") or [])]
    atr_pct = atr / last
    regime = _regime(last, ema20, ema50, rsi, atr_pct)

    width = atr * math.sqrt(max(1, steps))
    width = min(width, last * 0.35)
    bull_target = _nearest_above(resistance, last, last + width * 1.15)
    bear_target = _nearest_below(support, last, last - width * 1.15)

    if regime == "trend_up":
        base_target = min(bull_target, last + width * 0.65)
        probs = _normalise_probs(38, 42, 20)
        base_bias = "up"
    elif regime == "trend_down":
        base_target = max(bear_target, last - width * 0.65)
        probs = _normalise_probs(38, 20, 42)
        base_bias = "down"
    elif regime == "volatile":
        base_target = last + (ema20 - last) * 0.35
        probs = _normalise_probs(44, 28, 28)
        base_bias = "chop"
    else:
        base_target = last + (ema20 - last) * 0.45
        probs = _normalise_probs(50, 25, 25)
        base_bias = "chop"

    risk_label = ((risk or {}).get("label_key") or "").lower()
    confidence = 64
    if timeframe.lower() != "4h":
        confidence -= 8
    if len(bars) < 60:
        confidence -= 10
    if regime == "volatile":
        confidence -= 10
    if risk_label in {"rugpull", "danger", "high"}:
        confidence -= 15
    confidence = max(20, min(78, confidence))

    scenarios = [
        {
            "key": "base",
            "label": "Base path",
            "probability": probs["base"],
            "bias": base_bias,
            "trigger": _round_price(ema20),
            "invalidation": _round_price(bear_target if base_bias == "up" else bull_target if base_bias == "down" else last - width * 0.55),
            "target": _round_price(base_target),
            "target_low": _round_price(min(base_target, last) - atr * 0.35),
            "target_high": _round_price(max(base_target, last) + atr * 0.35),
            "stop": _round_price(last - atr * 1.25 if base_bias != "down" else last + atr * 1.25),
            "rr": 0.0,
            "path": _path(last, base_target, steps, atr * 0.35, 0.0),
            "reason": f"Base path follows the current {regime} regime using EMA/ATR structure.",
        },
        {
            "key": "bull",
            "label": "Bull path",
            "probability": probs["bull"],
            "bias": "up",
            "trigger": _round_price(_nearest_above(resistance, last, last + atr * 0.75)),
            "invalidation": _round_price(last - atr * 1.35),
            "target": _round_price(bull_target),
            "target_low": _round_price(bull_target - atr * 0.45),
            "target_high": _round_price(bull_target + atr * 0.45),
            "stop": _round_price(last - atr * 1.5),
            "rr": 0.0,
            "path": _path(last, bull_target, steps, atr * 0.45, 1.7),
            "reason": "Bull path requires acceptance above nearby resistance or continuation above EMA support.",
        },
        {
            "key": "bear",
            "label": "Bear path",
            "probability": probs["bear"],
            "bias": "down",
            "trigger": _round_price(_nearest_below(support, last, last - atr * 0.75)),
            "invalidation": _round_price(last + atr * 1.35),
            "target": _round_price(bear_target),
            "target_low": _round_price(bear_target - atr * 0.45),
            "target_high": _round_price(bear_target + atr * 0.45),
            "stop": _round_price(last + atr * 1.5),
            "rr": 0.0,
            "path": _path(last, bear_target, steps, atr * 0.45, 3.1),
            "reason": "Bear path activates on loss of nearby support or rejection under EMA resistance.",
        },
    ]

    for s in scenarios:
        risk_size = abs(last - _fnum(s["stop"], last))
        reward_size = abs(_fnum(s["target"], last) - last)
        s["rr"] = round(reward_size / risk_size, 2) if risk_size > 0 else None

    prices = [p["price"] for s in scenarios for p in s["path"]]
    return {
        "available": True,
        "timeframe": timeframe,
        "horizon": horizon,
        "steps": steps,
        "current_price": _round_price(last),
        "range_low": _round_price(min(prices)),
        "range_high": _round_price(max(prices)),
        "confidence": confidence,
        "regime": regime,
        "scenarios": scenarios,
    }
