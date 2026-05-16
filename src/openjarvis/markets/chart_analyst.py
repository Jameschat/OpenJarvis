"""Chart analyst — operator pastes a screenshot, gets back a real
technical analysis with the levels marked.

The honest architecture (not "ask gpt-4o to read RSI off a JPEG"):

  1. Vision call identifies the asset + timeframe from the screenshot
  2. We FETCH real OHLCV for that asset at the requested timeframe
     (Kraken 1h → resampled to 2h for majors; CoinGecko 4h fallback
     for long-tail top-100 coins where Kraken has no pair)
  3. We COMPUTE indicators ourselves: EMA(20/50/200), RSI(14),
     ATR(14), swing high/low support/resistance. Deterministic.
  4. We RENDER a NEW annotated chart (matplotlib) with EMA overlays,
     RSI panel, marked levels, and suggested entry/stop/target zones
  5. gpt-4o synthesises a research note citing the REAL computed
     values + pattern recognition from the original screenshot
  6. Markdown analysis lands in the vault, annotated PNG appears as
     a HUD overlay + chat-history image card

Compliance framing: this is technical analysis, not advice. Suggested
entry/stop/target levels are mechanically derived from the indicators
(EMA bands + ATR-sized stops + swing-level targets), not predictions.

Crypto-only on Day-1. Equity expansion deferred.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault paths
# ---------------------------------------------------------------------------

def _charts_dir() -> Path:
    try:
        from openjarvis.tools.obsidian_brain import BRAIN_ROOT
    except Exception:
        BRAIN_ROOT = Path(os.path.expanduser("~/Obsidian/Claude/Brain"))
    p = Path(BRAIN_ROOT) / "Trading" / "Research" / "Charts"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# 1. Vision identification
# ---------------------------------------------------------------------------

def _identify_chart(image_path: Path) -> Optional[Dict[str, Any]]:
    """Ask gpt-4o-mini vision: what crypto / timeframe is this chart of?
    Returns {ticker, timeframe, confidence, notes} or None."""
    print("[CHART] step 1/5: vision identify (gpt-4o-mini)", flush=True)
    try:
        from openjarvis.cli.llm_fallback import _get_openai_client
    except Exception as exc:
        print(f"[CHART] identify: client import failed: {exc}", flush=True)
        return None
    client = _get_openai_client()
    if client is None:
        print("[CHART] identify: no OPENAI_API_KEY", flush=True)
        return None
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except Exception as exc:
        print(f"[CHART] identify: image read failed: {exc}", flush=True)
        return None
    # Detect mime from extension; default to PNG
    ext = image_path.suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
            "webp": "webp", "gif": "gif"}.get(ext, "png")

    prompt = (
        "This is a screenshot of a crypto trading chart. Identify:\n"
        "  - ticker (the coin symbol — BTC, ETH, SOL, XRP, ADA, etc.)\n"
        "  - timeframe (the candle interval — 15m, 1h, 2h, 4h, 1d, "
        "1w; if not obviously labelled, say 'unknown')\n"
        "  - any obvious patterns visible to the naked eye (head and "
        "shoulders, ascending triangle, double top, range, etc.)\n"
        "  - rough current price if visible\n\n"
        "Respond as JSON only, no prose:\n"
        '  {"ticker": "BTC", "timeframe": "2h", "confidence": "high"|'
        '"medium"|"low", "patterns": [...], "current_price": <number or null>, '
        '"notes": "<short>"}\n'
        "If you cannot identify the ticker, set ticker to null."
    )
    t0 = time.time()
    try:
        # gpt-4o-mini for identification — 3x faster than gpt-4o, plenty
        # accurate for "what coin and what timeframe is this chart of".
        # gpt-4o reserved for the synthesis step where vision quality
        # actually matters.
        resp = client.chat.completions.create(
            model=os.environ.get(
                "OPENJARVIS_VISION_IDENT_MODEL", "gpt-4o-mini"),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                ],
            }],
            max_tokens=400,
            temperature=0.1,
        )
        text = (resp.choices[0].message.content or "").strip()
        print(f"[CHART] identify: ok in {time.time()-t0:.1f}s "
              f"({len(text)} chars)", flush=True)
    except Exception as exc:
        logger.exception("chart_analyst: vision identify failed")
        print(f"[CHART] identify: API call failed: {exc}", flush=True)
        return None
    # Strip ```json fences if present
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("chart_analyst: vision returned non-JSON: %s",
                       text[:200])
        print(f"[CHART] identify: non-JSON reply: {text[:140]}", flush=True)
        return None
    print(f"[CHART] identify: ticker={data.get('ticker')} "
          f"tf={data.get('timeframe')}", flush=True)
    return data


# ---------------------------------------------------------------------------
# 2. OHLCV fetch — Kraken (resampled) for majors, CoinGecko fallback
# ---------------------------------------------------------------------------

# Symbols where Kraken has a deep USD pair we can get 1h bars for.
# Lowercase for comparison; matches the symbol the operator sees in
# their chart screenshot. Expand as needed.
_KRAKEN_USD_SYMBOLS = {
    "btc": "XXBTZUSD", "xbt": "XXBTZUSD",
    "eth": "XETHZUSD",
    "sol": "SOLUSD",
    "xrp": "XXRPZUSD", "ada": "ADAUSD", "doge": "XDGUSD",
    "dot": "DOTUSD", "link": "LINKUSD", "matic": "MATICUSD",
    "ltc": "XLTCZUSD", "avax": "AVAXUSD", "atom": "ATOMUSD",
    "bch": "BCHUSD", "etc": "XETCZUSD", "xlm": "XXLMZUSD",
    "uni": "UNIUSD", "near": "NEARUSD", "fil": "FILUSD",
    "icp": "ICPUSD", "apt": "APTUSD", "arb": "ARBUSD",
    "op": "OPUSD", "inj": "INJUSD", "aave": "AAVEUSD",
    "mkr": "MKRUSD", "ldo": "LDOUSD", "rndr": "RNDRUSD",
    "render": "RNDRUSD", "sui": "SUIUSD", "tia": "TIAUSD",
    "sei": "SEIUSD", "pepe": "PEPEUSD", "shib": "SHIBUSD",
    "trx": "TRXUSD",
}


def _fetch_for_ticker(ticker: str, timeframe: str
                      ) -> Tuple[List[Dict[str, Any]], str, str]:
    """Returns (bars, source_label, actual_timeframe). bars is empty
    on failure. actual_timeframe may differ from requested when we
    have to fall back (e.g. CoinGecko 4h for long-tail coins)."""
    print(f"[CHART] step 2/5: fetch OHLCV for {ticker} @ {timeframe}",
          flush=True)
    sym_lower = (ticker or "").lower().strip()
    tf = (timeframe or "2h").lower().strip()

    # Try Kraken if we know the pair
    pair = _KRAKEN_USD_SYMBOLS.get(sym_lower)
    if pair:
        t0 = time.time()
        bars = _fetch_kraken(pair, tf)
        if bars:
            print(f"[CHART] fetch: kraken {pair} -> {len(bars)} bars "
                  f"in {time.time()-t0:.1f}s", flush=True)
            return bars, f"kraken:{pair}", tf
        print(f"[CHART] fetch: kraken {pair} returned nothing, "
              f"falling back to coingecko", flush=True)

    # Fall back to CoinGecko OHLC at 4h granularity
    try:
        from openjarvis.markets.sources import coingecko
        t0 = time.time()
        bars_cg = coingecko.fetch_history(ticker, range_str="1mo")
        if bars_cg:
            print(f"[CHART] fetch: coingecko {ticker} -> {len(bars_cg)} "
                  f"bars in {time.time()-t0:.1f}s (4h granularity)",
                  flush=True)
            return bars_cg, "coingecko (4h granularity)", "4h"
        print(f"[CHART] fetch: coingecko {ticker} returned nothing",
              flush=True)
    except Exception as exc:
        print(f"[CHART] fetch: coingecko exception: {exc}", flush=True)
        logger.debug("coingecko fallback failed", exc_info=True)
    return [], "no-data", tf


def _fetch_kraken(pair: str, timeframe: str) -> List[Dict[str, Any]]:
    """Fetch 1h Kraken bars and resample to the requested timeframe.
    For 1h request, no resampling. For 2h, group by 2. For 4h, by 4.
    For 1d we use Kraken's native 1440 interval."""
    try:
        import httpx  # type: ignore
    except Exception:
        return []
    # Native intervals Kraken supports
    if timeframe in ("1h", "60m"):
        kraken_interval = 60
        group = 1
    elif timeframe in ("2h", "120m"):
        kraken_interval = 60
        group = 2
    elif timeframe in ("4h", "240m"):
        kraken_interval = 240
        group = 1
    elif timeframe in ("1d", "d", "daily"):
        kraken_interval = 1440
        group = 1
    elif timeframe in ("30m",):
        kraken_interval = 30
        group = 1
    elif timeframe in ("15m",):
        kraken_interval = 15
        group = 1
    else:
        # Default: assume 2h
        kraken_interval = 60
        group = 2
    # We want enough bars for EMA(200): need at least 200 + a margin.
    # Kraken returns up to ~720 bars per call. For 1h that's 30 days.
    since = int(time.time()) - (45 * 86400)   # 45 days plenty for 1h
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get("https://api.kraken.com/0/public/OHLC", params={
                "pair": pair, "interval": str(kraken_interval),
                "since": str(since),
            })
            if r.status_code != 200:
                return []
            data = r.json()
            if data.get("error"):
                return []
            block = next(
                (v for k, v in (data.get("result") or {}).items()
                 if k != "last" and isinstance(v, list)), None
            )
            if not block:
                return []
    except Exception:
        return []
    raw = []
    for row in block:
        try:
            raw.append({
                "ts": float(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low":  float(row[3]),
                "close": float(row[4]),
                "volume": float(row[6]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    if group == 1:
        return raw
    # Resample by group consecutive bars
    out = []
    for i in range(0, len(raw) - group + 1, group):
        chunk = raw[i:i + group]
        if not chunk:
            continue
        out.append({
            "ts": chunk[0]["ts"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low":  min(b["low"]  for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        })
    return out


# ---------------------------------------------------------------------------
# 3. Indicators — pure python, no extra deps
# ---------------------------------------------------------------------------

def _ema(values: List[float], period: int) -> List[Optional[float]]:
    if not values or period <= 1:
        return [None] * len(values)
    k = 2.0 / (period + 1.0)
    out: List[Optional[float]] = [None] * len(values)
    # Seed with simple average of first `period` values
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1.0 - k)
    return out


def _rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(0.0, change)
        loss = max(0.0, -change)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr(bars: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """Average True Range over the last `period` bars. Returns None
    if not enough data."""
    if len(bars) <= period:
        return None
    trs = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]; l = bars[i]["low"]; pc = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _swing_levels(bars: List[Dict[str, Any]], window: int = 5
                  ) -> Tuple[List[float], List[float]]:
    """Find local maxima/minima over `window` bars on each side.
    Returns (highs, lows) with the most recent first."""
    highs, lows = [], []
    for i in range(window, len(bars) - window):
        h = bars[i]["high"]; l = bars[i]["low"]
        if all(bars[i + d]["high"] <= h for d in range(-window, window + 1)
               if d != 0):
            highs.append(h)
        if all(bars[i + d]["low"] >= l for d in range(-window, window + 1)
               if d != 0):
            lows.append(l)
    # Most-recent first
    highs.reverse(); lows.reverse()
    return highs, lows


def _compute_indicators(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    closes = [b["close"] for b in bars]
    last = closes[-1] if closes else None
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    rsi = _rsi(closes, 14)
    atr = _atr(bars, 14)
    highs, lows = _swing_levels(bars, window=5)
    # Pick top 3 nearest support / resistance from current price
    if last is not None:
        resistance = sorted([h for h in highs if h > last])[:3]
        support = sorted([l for l in lows if l < last], reverse=True)[:3]
    else:
        resistance, support = [], []
    return {
        "last": last,
        "ema20_series": ema20, "ema20_last": _last(ema20),
        "ema50_series": ema50, "ema50_last": _last(ema50),
        "ema200_series": ema200, "ema200_last": _last(ema200),
        "rsi_series": rsi, "rsi_last": _last(rsi),
        "atr": atr,
        "support": support, "resistance": resistance,
        "swing_highs_recent": highs[:5],
        "swing_lows_recent": lows[:5],
    }


def _last(xs: List[Optional[float]]) -> Optional[float]:
    for v in reversed(xs):
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# 4. Render annotated chart — matplotlib
# ---------------------------------------------------------------------------

def _forecast_overlay_series(
    last_ts: float,
    step_seconds: int,
    forecast: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not forecast or not forecast.get("available"):
        return None
    scenarios = forecast.get("scenarios") or []
    paths: Dict[str, List[float]] = {}
    for scenario in scenarios:
        key = str(scenario.get("key") or "").lower()
        points = scenario.get("path") or []
        vals = []
        for point in points:
            try:
                vals.append(float(point["price"]))
            except (KeyError, TypeError, ValueError):
                continue
        if key and vals:
            paths[key] = vals
    if not paths:
        return None
    max_len = max(len(v) for v in paths.values())
    if max_len < 2:
        return None
    times = [
        datetime.fromtimestamp(float(last_ts), timezone.utc).replace(tzinfo=None) +
        timedelta(seconds=max(60, int(step_seconds)) * i)
        for i in range(max_len)
    ]
    range_low = []
    range_high = []
    for i in range(max_len):
        vals_i = [vals[i] for vals in paths.values() if i < len(vals)]
        range_low.append(min(vals_i))
        range_high.append(max(vals_i))
    return {
        "times": times,
        "paths": paths,
        "range_low": range_low,
        "range_high": range_high,
    }


def _render_chart(ticker: str, timeframe: str,
                  bars: List[Dict[str, Any]],
                  ind: Dict[str, Any],
                  suggested: Optional[Dict[str, Any]] = None,
                  forecast: Optional[Dict[str, Any]] = None,
                  ) -> Optional[Path]:
    """Render a candlestick chart with EMA overlays + RSI panel +
    marked support/resistance + suggested entry/stop/target zones.
    Returns the saved PNG path or None if matplotlib is unavailable."""
    print(f"[CHART] step 4/5: render chart ({len(bars)} bars)", flush=True)
    try:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")           # no GUI
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib.dates as mdates  # type: ignore
        from matplotlib.patches import Rectangle  # type: ignore
    except Exception as exc:
        logger.warning("chart_analyst: matplotlib unavailable; skipping chart")
        print(f"[CHART] render: matplotlib unavailable ({exc}) — "
              f"install with: uv pip install matplotlib", flush=True)
        return None

    if not bars:
        return None
    # Trim to a sensible window — last 200 bars max
    bars = bars[-200:]
    ts = [datetime.utcfromtimestamp(b["ts"]) for b in bars]
    opens  = [b["open"]  for b in bars]
    highs  = [b["high"]  for b in bars]
    lows   = [b["low"]   for b in bars]
    closes = [b["close"] for b in bars]
    step_seconds = 4 * 3600
    if len(bars) >= 2:
        step_seconds = max(60, int(bars[-1]["ts"] - bars[-2]["ts"]))

    fig = plt.figure(figsize=(13, 8), facecolor="#0a1422")
    gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 0.6], hspace=0.05)
    ax_p = fig.add_subplot(gs[0])
    ax_r = fig.add_subplot(gs[1], sharex=ax_p)
    ax_v = fig.add_subplot(gs[2], sharex=ax_p)

    # Dark theme
    for ax in (ax_p, ax_r, ax_v):
        ax.set_facecolor("#0c1628")
        ax.tick_params(colors="#7a90b0", labelsize=8)
        for s in ax.spines.values():
            s.set_color("#1e3554")
        ax.grid(True, color="#1a2c45", linewidth=0.5, linestyle="-", alpha=0.7)

    # Candles
    width_days = (ts[1] - ts[0]).total_seconds() / 86400.0 * 0.7 if len(ts) > 1 else 0.05
    for i, (t, o, h, l, c) in enumerate(zip(ts, opens, highs, lows, closes)):
        up = c >= o
        col = "#22c55e" if up else "#ef4444"
        # Wick
        ax_p.plot([t, t], [l, h], color=col, linewidth=0.8)
        # Body — Rectangle (we lose vector niceness but it's clear)
        body_lo = min(o, c)
        body_hi = max(o, c)
        ax_p.add_patch(Rectangle(
            (mdates.date2num(t) - width_days / 2, body_lo),
            width_days, max(body_hi - body_lo, (h - l) * 0.001),
            facecolor=col, edgecolor=col,
        ))

    # EMA overlays
    ema_series = [
        ("EMA 20",  ind.get("ema20_series"),  "#ffd166"),
        ("EMA 50",  ind.get("ema50_series"),  "#06d6a0"),
        ("EMA 200", ind.get("ema200_series"), "#c77dff"),
    ]
    for label, series, colour in ema_series:
        if series is None:
            continue
        # Trim to the rendered window
        series = series[-len(ts):]
        ax_p.plot(ts, series, color=colour, linewidth=1.2,
                  label=label, alpha=0.95)
    ax_p.legend(loc="upper left", facecolor="#0a1422",
                edgecolor="#1e3554", labelcolor="#cfe7ff", fontsize=8)

    # Support / resistance horizontal bands
    for r in (ind.get("resistance") or []):
        ax_p.axhline(r, color="#ef4444", linewidth=0.7, linestyle="--",
                     alpha=0.55)
        ax_p.text(ts[-1], r, f"  R {r:,.4g}", color="#ef4444",
                  fontsize=7, va="center", ha="left")
    for s in (ind.get("support") or []):
        ax_p.axhline(s, color="#22c55e", linewidth=0.7, linestyle="--",
                     alpha=0.55)
        ax_p.text(ts[-1], s, f"  S {s:,.4g}", color="#22c55e",
                  fontsize=7, va="center", ha="left")

    # Suggested entry/stop/target bands (ATR-derived; see _derive_suggested)
    if suggested:
        for label, v, colour in [
            ("Entry",  suggested.get("entry"),  "#5ed0e0"),
            ("Stop",   suggested.get("stop"),   "#ff7a85"),
            ("TP1",    suggested.get("tp1"),    "#a7f3c8"),
            ("TP2",    suggested.get("tp2"),    "#a7f3c8"),
        ]:
            if v is None:
                continue
            ax_p.axhline(v, color=colour, linewidth=1.2, alpha=0.9)
            ax_p.text(ts[0], v, f"{label} {v:,.4g}  ", color=colour,
                      fontsize=8, fontweight="bold", va="center", ha="right",
                      bbox={"facecolor": "#0a1422", "edgecolor": colour,
                            "linewidth": 0.5, "pad": 2})

    ax_p.set_title(
        f"{ticker} · {timeframe}  (suggested levels are mechanical "
        f"TA outputs, not predictions)",
        color="#cfe7ff", fontsize=11, pad=10,
    )

    overlay = _forecast_overlay_series(bars[-1]["ts"], step_seconds, forecast)
    if overlay:
        times_f = overlay["times"]
        paths = overlay["paths"]
        colours = {"base": "#5ed0e0", "bull": "#22c55e", "bear": "#ef4444"}
        labels = {"base": "Base forecast", "bull": "Bull forecast",
                  "bear": "Bear forecast"}
        range_low = overlay.get("range_low")
        range_high = overlay.get("range_high")
        if range_low and range_high:
            ax_p.fill_between(times_f, range_low, range_high,
                              color="#5ed0e0", alpha=0.08,
                              label="Forecast range")
        ax_p.axvline(times_f[0], color="#7a90b0", linewidth=0.8,
                     linestyle=":", alpha=0.7)
        for key in ("base", "bull", "bear"):
            vals = paths.get(key)
            if not vals:
                continue
            ax_p.plot(times_f, vals, color=colours[key], linewidth=1.6,
                      linestyle=(0, (2, 3)), alpha=0.95,
                      label=labels[key])
            ax_p.text(times_f[-1], vals[-1], f"  {key.upper()}",
                      color=colours[key], fontsize=7, va="center",
                      fontweight="bold")
        ax_p.set_xlim(ts[0], times_f[-1])
        ax_p.legend(loc="upper left", facecolor="#0a1422",
                    edgecolor="#1e3554", labelcolor="#cfe7ff", fontsize=8)

    # RSI panel
    rsi = (ind.get("rsi_series") or [])[-len(ts):]
    ax_r.plot(ts, rsi, color="#5ed0e0", linewidth=1.0)
    ax_r.axhline(70, color="#ef4444", linewidth=0.5, linestyle="--", alpha=0.5)
    ax_r.axhline(30, color="#22c55e", linewidth=0.5, linestyle="--", alpha=0.5)
    ax_r.fill_between(ts, 70, 100, color="#ef4444", alpha=0.05)
    ax_r.fill_between(ts, 0, 30, color="#22c55e", alpha=0.05)
    ax_r.set_ylim(0, 100)
    ax_r.set_ylabel("RSI(14)", color="#7a90b0", fontsize=8)
    rsi_last = ind.get("rsi_last")
    if rsi_last is not None:
        ax_r.text(ts[-1], rsi_last, f"  {rsi_last:.1f}",
                  color="#5ed0e0", fontsize=8, va="center")

    # Volume
    vols = [b.get("volume") or 0.0 for b in bars]
    bar_cols = ["#22c55e" if c >= o else "#ef4444"
                for o, c in zip(opens, closes)]
    ax_v.bar(ts, vols, width=width_days, color=bar_cols, alpha=0.6)
    ax_v.set_ylabel("Vol", color="#7a90b0", fontsize=8)

    # Date formatting
    ax_v.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b %H:%M"))
    plt.setp(ax_v.get_xticklabels(), rotation=0)
    plt.setp(ax_p.get_xticklabels(), visible=False)
    plt.setp(ax_r.get_xticklabels(), visible=False)

    out_dir = _charts_dir()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    sym_safe = "".join(c for c in ticker if c.isalnum())
    out_path = out_dir / f"{stamp}-{sym_safe}-{timeframe}.png"
    fig.savefig(out_path, dpi=120, facecolor=fig.get_facecolor(),
                bbox_inches="tight")
    plt.close(fig)
    print(f"[CHART] render: ok -> {out_path.name}", flush=True)
    return out_path


# ---------------------------------------------------------------------------
# Suggested levels (mechanical TA, not predictions)
# ---------------------------------------------------------------------------

def _derive_suggested(ind: Dict[str, Any]) -> Dict[str, Any]:
    """Mechanical entry / stop / take-profit suggestions from the
    indicators. NOT predictions — these are the TA-textbook levels
    a chart-reader would mark up. The LLM synthesis layer wraps them
    in appropriate hedging language."""
    last = ind.get("last")
    atr = ind.get("atr")
    ema20 = ind.get("ema20_last")
    ema50 = ind.get("ema50_last")
    rsi = ind.get("rsi_last")
    support = ind.get("support") or []
    resistance = ind.get("resistance") or []
    if last is None:
        return {}

    # Bias: bullish if last > EMA50 and RSI > 50
    bias = None
    if ema50 is not None:
        if last > ema50 and (rsi is None or rsi > 50):
            bias = "long"
        elif last < ema50 and (rsi is None or rsi < 50):
            bias = "short"

    out: Dict[str, Any] = {"bias": bias}

    if bias == "long":
        # Entry zone — pullback to EMA20 if reasonable; else current
        entry = ema20 if (ema20 is not None and ema20 < last
                          and (last - ema20) / last < 0.03) else last
        out["entry"] = round(entry, 6)
        # Stop — 1.5×ATR below entry, or below nearest support, whichever's tighter
        if atr is not None:
            atr_stop = entry - 1.5 * atr
            sup_stop = (max(support) - 0.001 * entry) if support else atr_stop
            stop = min(atr_stop, sup_stop) if support else atr_stop
            out["stop"] = round(stop, 6)
        # Targets — nearest resistances above entry
        if resistance:
            out["tp1"] = round(resistance[0], 6)
            if len(resistance) > 1:
                out["tp2"] = round(resistance[1], 6)
    elif bias == "short":
        entry = ema20 if (ema20 is not None and ema20 > last
                          and (ema20 - last) / last < 0.03) else last
        out["entry"] = round(entry, 6)
        if atr is not None:
            atr_stop = entry + 1.5 * atr
            res_stop = (min(resistance) + 0.001 * entry) if resistance else atr_stop
            stop = max(atr_stop, res_stop) if resistance else atr_stop
            out["stop"] = round(stop, 6)
        if support:
            out["tp1"] = round(support[0], 6)
            if len(support) > 1:
                out["tp2"] = round(support[1], 6)
    if "entry" in out and "stop" in out:
        risk = abs(out["entry"] - out["stop"])
        if "tp1" in out:
            reward = abs(out["tp1"] - out["entry"])
            if risk > 0:
                out["rr_to_tp1"] = round(reward / risk, 2)
    return out


# ---------------------------------------------------------------------------
# 5. LLM synthesis — vision + computed indicators
# ---------------------------------------------------------------------------

_SYNTH_PROMPT = """\
You are Jarvis-ChartAnalyst, a technical analyst writing a research \
note for a single operator's paper-trading account. You are NOT \
giving advice. You are reading a chart and naming what the indicators \
actually show.

The operator just attached a screenshot of a {ticker} chart on the \
{timeframe} timeframe.

You have been given:
  1. The original screenshot (for pattern recognition)
  2. REAL computed indicators from {source} OHLCV data (authoritative
     for any numerical claim — do NOT read numbers off the screenshot,
     use these)
  3. Suggested mechanical entry/stop/target zones derived from the
     indicators

REAL INDICATORS:
  - Last close: {last}
  - EMA(20) / EMA(50) / EMA(200): {ema20} / {ema50} / {ema200}
  - RSI(14): {rsi}
  - ATR(14): {atr}
  - Nearest support: {support}
  - Nearest resistance: {resistance}

SUGGESTED LEVELS (mechanical, not predictions):
  - Bias: {bias}
  - Entry: {entry}
  - Stop: {stop}
  - TP1: {tp1}
  - TP2: {tp2}
  - R:R to TP1: {rr}

Write a 220-word research note in this EXACT structure (the first \
line MUST be the verdict heading — operator scans this first):

{risk_block}## Verdict: LONG | SHORT | NEUTRAL — <conviction: low|mid|high>

ONE sentence stating the bias derived from the indicators. \
Use the supplied bias field as the starting point but you may \
downgrade to NEUTRAL if EMAs are tangled, RSI is mid-range, or the \
screenshot pattern contradicts the math. NEVER use "high" conviction \
unless trend, EMA alignment, RSI, AND a screenshot pattern all agree.

## Read
1-2 sentences on what the chart shows — trend direction, where price \
sits relative to the EMAs, RSI regime (oversold/neutral/overbought), \
any obvious pattern visible in the screenshot.

## Levels
- Support: <numbers>
- Resistance: <numbers>
- Suggested entry zone: <number or range>
- Suggested stop: <number>
- Suggested take-profit 1: <number>
- Suggested take-profit 2: <number>
- Risk/reward to TP1: <number>

## Setup quality
ONE paragraph. Be honest. If RSI is mid-range, EMAs are tangled, and \
no clean structure is visible, say "no high-conviction setup; sit out". \
Use "the indicators suggest" / "the chart shows" — never "you should buy" \
or "BUY NOW".

## Invalidation
ONE sentence. What price action makes this read wrong.

End. No disclaimers — they're added by the persistence layer.
"""


def _risk_for_ticker(ticker: str, source: str) -> Optional[Dict[str, Any]]:
    """Return a risk dict for a crypto ticker, or None for non-crypto.

    Pulls the bulk row from CoinGecko's warm cache (populated by Pulse
    or the 1000-coin backfill). Falls back to a single-coin quote if
    the cache is cold. Lazily fetches /coins/{id} for the age penalty —
    cheap because it's one call per analyzed chart, not per Pulse row.
    """
    is_crypto = (
        ticker == "BTC" or ticker == "ETH"
        or "kraken" in (source or "").lower()
        or "coingecko" in (source or "").lower()
    )
    if not is_crypto:
        return None
    try:
        from openjarvis.markets.sources import coingecko
        from openjarvis.markets import risk as _risk
    except Exception:
        return None

    bulk = coingecko.fetch_top_n(1000) or coingecko.fetch_top_100() or []
    coin: Optional[Dict[str, Any]] = None
    for c in bulk:
        if (c.get("symbol") or "").upper() == ticker.upper():
            coin = c
            break

    if coin is None:
        q = coingecko.fetch_quote(ticker)
        if not q:
            return None
        coin = {
            "name": q.get("ticker") or ticker,
            "symbol": ticker,
            "market_cap": None, "volume_24h": q.get("volume"),
            "last": q.get("last"),
            "change_24h_pct": q.get("change_24h_pct"),
            "sparkline_7d": [],
        }

    detail = None
    if coin.get("id"):
        try:
            detail = coingecko.fetch_coin_detail(coin["id"])
        except Exception:
            detail = None

    return _risk.score_coin(coin, detail=detail)


def _format_risk_block(risk: Optional[Dict[str, Any]]) -> str:
    """Inline directive for the synth prompt — empty when clean."""
    if not risk:
        return ""
    key = risk.get("label_key")
    if key == "rugpull":
        reasons = "; ".join(risk.get("reasons") or []) or "multiple risk signals"
        return (
            "RUGPULL OVERRIDE — risk score "
            f"{risk.get('score')} / 100 ({reasons}). This coin has a high "
            "probability of being a pump-and-dump or rug-pull. The verdict "
            "MUST be NEUTRAL with conviction low. State explicitly in the "
            "Read section: 'High scam-risk profile — refusing directional "
            "verdict regardless of TA setup'. Do NOT issue LONG.\n\n"
        )
    if key == "high":
        reasons = "; ".join(risk.get("reasons") or [])
        return (
            f"HIGH-RISK ADVISORY — risk score {risk.get('score')} / 100 "
            f"({reasons}). Downgrade conviction by one band (high→mid, "
            "mid→low). Mention the risk profile in the Setup quality "
            "paragraph.\n\n"
        )
    if key == "caution":
        return (
            f"Caution flag — risk score {risk.get('score')} / 100. Mention "
            "the elevated risk profile briefly in Setup quality.\n\n"
        )
    return ""


def _synthesize(image_path: Path, ticker: str, timeframe: str,
                ind: Dict[str, Any], source: str,
                suggested: Dict[str, Any],
                risk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    print("[CHART] step 5/5: synthesise (gpt-4o vision)", flush=True)
    try:
        from openjarvis.cli.llm_fallback import _get_openai_client
    except Exception:
        return {"headline": "(synthesis unavailable)", "body": ""}
    client = _get_openai_client()
    if client is None:
        return {"headline": "(no OPENAI_API_KEY)", "body": ""}
    t0 = time.time()
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except Exception:
        b64 = ""
    ext = image_path.suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
            "webp": "webp"}.get(ext, "png")
    prompt = _SYNTH_PROMPT.format(
        ticker=ticker, timeframe=timeframe, source=source,
        last=_fmt(ind.get("last")),
        ema20=_fmt(ind.get("ema20_last")),
        ema50=_fmt(ind.get("ema50_last")),
        ema200=_fmt(ind.get("ema200_last")),
        rsi=_fmt(ind.get("rsi_last"), 1),
        atr=_fmt(ind.get("atr")),
        support=", ".join(_fmt(s) for s in (ind.get("support") or [])) or "—",
        resistance=", ".join(_fmt(r) for r in (ind.get("resistance") or [])) or "—",
        bias=suggested.get("bias") or "neutral / no clean setup",
        entry=_fmt(suggested.get("entry")),
        stop=_fmt(suggested.get("stop")),
        tp1=_fmt(suggested.get("tp1")),
        tp2=_fmt(suggested.get("tp2")),
        rr=_fmt(suggested.get("rr_to_tp1"), 2),
        risk_block=_format_risk_block(risk),
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    if b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{mime};base64,{b64}"},
        })
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("OPENJARVIS_VISION_MODEL", "gpt-4o"),
            messages=[{"role": "user", "content": content}],
            max_tokens=900,
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        print(f"[CHART] synth: ok in {time.time()-t0:.1f}s "
              f"({len(text)} chars)", flush=True)
    except Exception as exc:
        logger.exception("chart_analyst: synthesis call failed")
        print(f"[CHART] synth: API call failed: {exc}", flush=True)
        return {"headline": "(synthesis failed)", "body": ""}
    # Headline = the Verdict line if present, else first prose sentence
    headline = "TA read"
    verdict: Optional[str] = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        ls = line.strip()
        if ls.lower().startswith("## verdict"):
            # Pull the verdict label after the colon
            after = ls.split(":", 1)[-1].strip(" *#-")
            verdict = after[:80]
            # Headline = verdict + the next non-empty prose sentence
            for nxt in lines[i + 1:]:
                ns = nxt.strip()
                if ns and not ns.startswith("#") and not ns.startswith("*") \
                        and not ns.startswith("-"):
                    headline = f"{after}: {ns[:120]}"
                    break
            else:
                headline = after
            break
    if verdict is None:
        # Fall back to first prose sentence
        for line in lines:
            ls = line.strip()
            if ls and not ls.startswith("#") and not ls.startswith("*") \
                    and not ls.startswith("-"):
                headline = ls[:140]
                break
    return {"headline": headline, "body": text, "verdict": verdict}


def _fmt(v: Optional[float], decimals: int = 4) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:,.2f}"
    return f"{v:.{decimals}f}"


def _format_forecast_markdown(forecast: Optional[Dict[str, Any]]) -> str:
    if not forecast or not forecast.get("available"):
        return ""

    timeframe = str(forecast.get("timeframe") or "4h").upper()
    horizon = str(forecast.get("horizon") or "3d").upper()
    confidence = forecast.get("confidence")
    regime = str(forecast.get("regime") or "unknown").replace("_", " ")
    scenarios = forecast.get("scenarios") or []

    lines = [
        f"## {timeframe} Forecast Scenarios",
        "",
        (
            f"- Horizon: {horizon}"
            f" | Confidence: {confidence}%"
            f" | Regime: {regime}"
        ),
        (
            f"- Range: {_fmt(forecast.get('range_low'))}"
            f" to {_fmt(forecast.get('range_high'))}"
        ),
        "",
    ]

    for scenario in scenarios:
        label = str(scenario.get("label") or scenario.get("key") or "Scenario")
        probability = scenario.get("probability")
        lines.extend([
            f"### {label}",
            (
                f"- Probability: {probability}%"
                f" | Bias: {scenario.get('bias') or 'n/a'}"
                f" | R/R: {_fmt(scenario.get('rr'), 2)}"
            ),
            (
                f"- Trigger: {_fmt(scenario.get('trigger'))}"
                f" | Target: {_fmt(scenario.get('target'))}"
                f" | Stop: {_fmt(scenario.get('stop'))}"
                f" | Invalidation: {_fmt(scenario.get('invalidation'))}"
            ),
        ])
        reason = scenario.get("reason")
        if reason:
            lines.append(f"- Reason: {reason}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n\n"


# ---------------------------------------------------------------------------
# 6. Persist + emit widget
# ---------------------------------------------------------------------------

def _persist_analysis(ticker: str, timeframe: str,
                      analysis: Dict[str, Any],
                      chart_path: Optional[Path],
                      ind: Dict[str, Any],
                      suggested: Dict[str, Any],
                      source: str,
                      original_screenshot: Path,
                      risk: Optional[Dict[str, Any]] = None,
                      forecast: Optional[Dict[str, Any]] = None) -> Path:
    out_dir = _charts_dir()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    sym_safe = "".join(c for c in ticker if c.isalnum())
    md_path = out_dir / f"{stamp}-{sym_safe}-{timeframe}.md"
    chart_md = ""
    if chart_path is not None:
        # Relative reference for Obsidian
        try:
            rel = chart_path.name
            chart_md = f"\n\n![[{rel}]]\n\n"
        except Exception:
            chart_md = ""
    body = analysis.get("body") or "_(no analysis text generated)_"
    forecast_md = _format_forecast_markdown(forecast)

    # Risk banner — prepended above the analysis body so it's the
    # first thing visible. Only rendered for non-clean coins.
    risk_md = ""
    risk_fm = ""
    if risk and risk.get("label_key") and risk["label_key"] != "clean":
        key = risk["label_key"]
        emoji = risk.get("label_emoji") or ""
        label = risk.get("label") or key.upper()
        score = risk.get("score")
        reasons = "; ".join(risk.get("reasons") or [])
        prefix = "> [!danger]" if key == "rugpull" else "> [!warning]"
        risk_md = (
            f"\n{prefix} {emoji} {label} — risk score {score}/100\n"
            f"> {reasons}\n\n"
        )
        risk_fm = f"risk_label: {key}\nrisk_score: {score}\n"

    md = (
        "---\n"
        f"type: chart-analysis\n"
        f"date: {date.today().isoformat()}\n"
        f"ticker: {ticker}\n"
        f"timeframe: {timeframe}\n"
        f"source: {source}\n"
        + risk_fm +
        "---\n\n"
        f"# Chart analysis — {ticker} · {timeframe}\n\n"
        "*Personal technical-analysis note for the operator. Levels "
        "are mechanically derived from indicators, not predictions or "
        "advice. Not for any third party.*\n"
        + risk_md
        + chart_md
        + body
        + "\n\n## Audit footer\n\n"
        + f"- Generated: {datetime.now().isoformat(timespec='seconds')}\n"
        + f"- OHLCV source: {source}\n"
        + f"- Original screenshot: `{original_screenshot.name}`\n"
        + f"- Indicators: EMA(20)={_fmt(ind.get('ema20_last'))}  "
          f"EMA(50)={_fmt(ind.get('ema50_last'))}  "
          f"EMA(200)={_fmt(ind.get('ema200_last'))}  "
          f"RSI(14)={_fmt(ind.get('rsi_last'), 1)}  "
          f"ATR(14)={_fmt(ind.get('atr'))}\n"
        + f"- Suggested levels: entry={_fmt(suggested.get('entry'))}  "
          f"stop={_fmt(suggested.get('stop'))}  "
          f"tp1={_fmt(suggested.get('tp1'))}  "
          f"tp2={_fmt(suggested.get('tp2'))}  "
          f"R:R={_fmt(suggested.get('rr_to_tp1'), 2)}\n"
    )
    md_path.write_text(md, encoding="utf-8")
    return md_path


def _emit_chart_widget(chart_path: Optional[Path], caption: str) -> None:
    if chart_path is None:
        print("[CHART] emit_widget: no chart_path (matplotlib missing?)",
              flush=True)
        return
    try:
        from openjarvis.cli.brain_server import emit_widget
        with open(chart_path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        url = f"data:image/png;base64,{b64}"
        emit_widget("image", {"url": url, "caption": caption})
        print(f"[CHART] emit_widget: ok ({len(data)} bytes)", flush=True)
    except Exception as exc:
        logger.debug("chart widget emit failed", exc_info=True)
        print(f"[CHART] emit_widget: FAILED {exc}", flush=True)


# ---------------------------------------------------------------------------
# Top-level entry — registered as a tool in markets_tools.py
# ---------------------------------------------------------------------------

def analyze_chart(image_path: str,
                  ticker_hint: Optional[str] = None,
                  timeframe: str = "2h",
                  forecast_horizon: str = "3d") -> str:
    """Main entry. Returns a JSON string with summary + paths."""
    print(f"[CHART] === analyze_chart start: {image_path!r} "
          f"hint={ticker_hint} tf={timeframe}", flush=True)
    started = time.time()
    p = Path(image_path)
    if not p.is_file():
        print(f"[CHART] FAIL: image not found at {image_path}", flush=True)
        return json.dumps({
            "ok": False, "error": f"image not found: {image_path}",
        })

    # 1. Identify (vision) — only if no hint supplied
    ticker = (ticker_hint or "").strip().upper() or None
    vision_notes: Dict[str, Any] = {}
    if ticker is None:
        ident = _identify_chart(p)
        if ident:
            vision_notes = ident
            if ident.get("ticker"):
                ticker = str(ident["ticker"]).upper()
            if ident.get("timeframe") and ident["timeframe"] != "unknown":
                # Trust the vision read of the timeframe over the default
                timeframe = str(ident["timeframe"])
    if not ticker:
        return json.dumps({
            "ok": False,
            "error": ("could not identify ticker from screenshot. "
                      "Pass ticker_hint=... e.g. ticker_hint='BTC'."),
            "vision_notes": vision_notes,
        })

    # 2. Fetch real OHLCV
    bars, source, actual_tf = _fetch_for_ticker(ticker, timeframe)
    if not bars:
        return json.dumps({
            "ok": False,
            "error": f"no OHLCV available for {ticker} (tried Kraken + CoinGecko)",
            "ticker": ticker,
        })

    # 3. Compute indicators
    ind = _compute_indicators(bars)
    suggested = _derive_suggested(ind)

    # 3b. Risk profile (crypto only) — drives RUGPULL banner + verdict override
    risk = _risk_for_ticker(ticker, source)
    if risk:
        print(f"[CHART] risk: {ticker} score={risk.get('score')} "
              f"label={risk.get('label_key')} "
              f"reasons={'; '.join(risk.get('reasons') or [])}",
              flush=True)

    # 3c. Three-path probabilistic forecast (deterministic TA, not LLM)
    forecast = None
    try:
        from openjarvis.markets.forecast import generate_forecast
        forecast = generate_forecast(
            bars,
            ind,
            timeframe=actual_tf,
            horizon=forecast_horizon,
            risk=risk if isinstance(risk, dict) else None,
        )
    except Exception:
        logger.exception("chart_analyst: forecast generation failed")
        forecast = None

    # 4. Render annotated chart
    chart_path = _render_chart(ticker, actual_tf, bars, ind, suggested,
                               forecast=forecast)

    # 5. Synthesise
    analysis = _synthesize(p, ticker, actual_tf, ind, source, suggested,
                           risk=risk)

    # 5b. Hard verdict override on rugpull — refuse LONG no matter what
    # the LLM said. We've already prepended a directive in the prompt;
    # this is the belt-and-braces post-process.
    if risk and risk.get("label_key") == "rugpull":
        v = (analysis.get("verdict") or "").upper()
        if "LONG" in v or "BUY" in v:
            print(f"[CHART] risk-override: stripping LONG verdict ({v!r})",
                  flush=True)
            forced = "NEUTRAL — low (forced by RUGPULL risk override)"
            analysis["verdict"] = forced
            analysis["headline"] = forced + " · high scam-risk profile"

    # 6. Persist
    md_path = _persist_analysis(ticker, actual_tf, analysis, chart_path,
                                ind, suggested, source, p, risk=risk,
                                forecast=forecast)

    # 7. Emit widget for HUD
    headline = analysis.get("headline") or "TA read"
    risk_caption = ""
    if risk and risk.get("label_key") == "rugpull":
        risk_caption = " · 🚨 RUGPULL"
    elif risk and risk.get("label_key") == "high":
        risk_caption = " · ⚠ HIGH RISK"
    print(f"[CHART] step 6/6: emit widget", flush=True)
    _emit_chart_widget(chart_path,
                       f"{ticker} · {actual_tf}{risk_caption} · {headline}")
    print(f"[CHART] === analyze_chart DONE in "
          f"{time.time()-started:.1f}s · {ticker}/{actual_tf} · "
          f"verdict={analysis.get('verdict')} · widget={'yes' if chart_path else 'no'}",
          flush=True)

    return json.dumps({
        "ok": True,
        "ticker": ticker,
        "timeframe_requested": timeframe,
        "timeframe_actual": actual_tf,
        "source": source,
        "verdict": analysis.get("verdict") or (suggested.get("bias") or "neutral").upper(),
        "risk": risk,
        "forecast": forecast,
        "vision_notes": vision_notes,
        "indicators": {
            "last":    ind.get("last"),
            "ema20":   ind.get("ema20_last"),
            "ema50":   ind.get("ema50_last"),
            "ema200":  ind.get("ema200_last"),
            "rsi":     ind.get("rsi_last"),
            "atr":     ind.get("atr"),
            "support": ind.get("support"),
            "resistance": ind.get("resistance"),
        },
        "suggested": suggested,
        "summary": headline,
        "chart_image": str(chart_path) if chart_path else None,
        "vault_note": str(md_path),
        "wall_seconds": round(time.time() - started, 1),
    })


__all__ = ["analyze_chart"]
