"""Per-ticker trend signals computed from the historical OHLCV bars
in the prices store.

These are the inputs to the briefing generator. Each metric has
research support (where claimed below); see Investment Researcher's
2026-04-30 brief for citations.

Output schema for one ticker:

    {
      "ticker", "n_bars", "first_ts", "last_ts",
      "last", "trend_3m_pct",
      "high_50d", "low_50d", "drawdown_from_high_pct",
      "sma20", "sma50", "above_sma20", "above_sma50",
      "sma_cross",                # "golden" / "death" / null
      "vol_annualised_pct", "sharpe_proxy",
      "composite_score"           # bounded [-1, +1]
    }

All functions are pure — no I/O — so they can be unit-tested cheaply.
The store handles persistence; the briefing generator handles LLM
context assembly. This module just does maths.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _closes(bars: List[Dict[str, Any]]) -> List[float]:
    return [float(b["close"]) for b in bars
            if b.get("close") is not None]


def _sma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / float(window)


def _stdev(values: List[float]) -> Optional[float]:
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _daily_returns(closes: List[float]) -> List[float]:
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        out.append((closes[i] - prev) / prev)
    return out


def compute(ticker: str, bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the full signal block for one ticker. Bars must be
    oldest-first (matching ``store.get_history``'s default order).

    Returns a dict with the schema documented at module top. Numeric
    fields may be None when there isn't enough history."""
    base: Dict[str, Any] = {
        "ticker": (ticker or "").upper(),
        "n_bars": len(bars or []),
        "first_ts": None, "last_ts": None,
        "last": None, "trend_3m_pct": None,
        "high_50d": None, "low_50d": None,
        "drawdown_from_high_pct": None,
        "sma20": None, "sma50": None,
        "above_sma20": None, "above_sma50": None,
        "sma_cross": None,
        "vol_annualised_pct": None,
        "sharpe_proxy": None,
        "composite_score": None,
    }
    if not bars:
        return base
    base["first_ts"] = bars[0].get("ts")
    base["last_ts"] = bars[-1].get("ts")
    closes = _closes(bars)
    if not closes:
        return base
    last = closes[-1]
    base["last"] = last

    # 3-month return — first close vs last
    first = closes[0]
    if first > 0:
        base["trend_3m_pct"] = (last - first) / first * 100.0

    # 50-day high / low + drawdown
    last_50 = closes[-50:] if len(closes) >= 50 else closes
    if last_50:
        hi50 = max(last_50)
        lo50 = min(last_50)
        base["high_50d"] = hi50
        base["low_50d"] = lo50
        if hi50 > 0:
            base["drawdown_from_high_pct"] = (last - hi50) / hi50 * 100.0

    # Moving averages
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    base["sma20"] = sma20
    base["sma50"] = sma50
    if sma20 is not None:
        base["above_sma20"] = last >= sma20
    if sma50 is not None:
        base["above_sma50"] = last >= sma50
    # SMA cross: compare current 20/50 to 20/50 5 bars ago
    if (sma20 is not None and sma50 is not None
            and len(closes) >= 55):
        prev_20 = sum(closes[-25:-5]) / 20.0
        prev_50 = sum(closes[-55:-5]) / 50.0
        if prev_20 < prev_50 and sma20 > sma50:
            base["sma_cross"] = "golden"   # 20 crossing above 50
        elif prev_20 > prev_50 and sma20 < sma50:
            base["sma_cross"] = "death"

    # Realised volatility (annualised, equity-day convention 252)
    rets = _daily_returns(closes)
    if rets:
        sd = _stdev(rets)
        if sd is not None:
            ann = sd * math.sqrt(252.0)
            base["vol_annualised_pct"] = ann * 100.0
            mean_ret = sum(rets) / len(rets)
            if sd > 0:
                # Crude Sharpe — annualised mean / annualised vol.
                # Risk-free rate ignored; this is a sort key not a
                # paper-publication metric.
                base["sharpe_proxy"] = (mean_ret * 252.0) / (sd * math.sqrt(252.0))

    # Composite score in [-1, +1] — equal-weighted blend of:
    #   * 3m trend sign × magnitude (capped at ±25%)
    #   * Above SMA50 (+0.2 / -0.2)
    #   * Above SMA20 (+0.1 / -0.1)
    #   * Recent drawdown penalty (-0.3 if drawdown > -10%)
    #   * Vol penalty (-0.2 if annualised vol > 60%)
    score = 0.0
    weight = 0.0
    t3m = base["trend_3m_pct"]
    if t3m is not None:
        # Map ±25% → ±1.0
        capped = max(-25.0, min(25.0, t3m))
        score += capped / 25.0 * 0.4
        weight += 0.4
    if base["above_sma50"] is True:
        score += 0.2; weight += 0.2
    elif base["above_sma50"] is False:
        score -= 0.2; weight += 0.2
    if base["above_sma20"] is True:
        score += 0.1; weight += 0.1
    elif base["above_sma20"] is False:
        score -= 0.1; weight += 0.1
    dd = base["drawdown_from_high_pct"]
    if dd is not None and dd < -10.0:
        score -= 0.3; weight += 0.3
    if base["vol_annualised_pct"] is not None and base["vol_annualised_pct"] > 60.0:
        score -= 0.2; weight += 0.2
    if weight > 0:
        # Renormalise to [-1, +1]
        base["composite_score"] = max(-1.0, min(1.0, score))
    return base


def rank(signals: List[Dict[str, Any]], *,
         top_n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Sort signals by composite_score descending. None scores sink
    to the bottom. ``top_n`` clamps the return."""
    def key(s):
        v = s.get("composite_score")
        return (0, v) if v is not None else (1, 0)
    out = sorted(signals or [], key=key, reverse=True)
    # Move None-score rows to the end
    out = [s for s in out if s.get("composite_score") is not None] + \
          [s for s in out if s.get("composite_score") is None]
    if top_n is not None:
        return out[:top_n]
    return out


__all__ = ["compute", "rank"]
