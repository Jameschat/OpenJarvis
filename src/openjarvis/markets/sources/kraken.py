"""Kraken public REST source — crypto quotes (BTC + ETH initially).

Free, no API key required for public endpoints. Kraken is the picked
crypto venue for a Jersey-resident operator: deepest GBP-pair
liquidity, Faster Payments rails, decent fees, accepts Jersey
residents.

Uses Kraken's classic REST API (Trade ticker pairs are XBTGBP /
ETHGBP — note BTC is "XBT" internally on Kraken). REST polling only
in v0; WebSocket comes in the daemon (next session).

Public Kraken docs: https://docs.kraken.com/rest/#tag/Market-Data

Single function ``fetch_quote(ticker)`` returns the same normalised
shape as the yfinance source so the caller doesn't care which feed
ran. ``ticker`` accepts the human form ("BTC", "ETH", "BTC-GBP",
"BTC/GBP") and the Kraken form ("XXBTZGBP") interchangeably.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BASE = "https://api.kraken.com/0/public/Ticker"

# Map common human tickers to Kraken's pair codes for GBP quotes. Add
# more entries when the operator extends the watchlist (only BTC + ETH
# in v0 per Day-1 scope).
_HUMAN_TO_KRAKEN_PAIR = {
    "BTC":      "XXBTZGBP",
    "BTC-GBP":  "XXBTZGBP",
    "BTC/GBP":  "XXBTZGBP",
    "XBT":      "XXBTZGBP",
    "XBTGBP":   "XXBTZGBP",
    "XXBTZGBP": "XXBTZGBP",
    "ETH":      "XETHZGBP",
    "ETH-GBP":  "XETHZGBP",
    "ETH/GBP":  "XETHZGBP",
    "ETHGBP":   "XETHZGBP",
    "XETHZGBP": "XETHZGBP",
}

_KRAKEN_PAIR_TO_HUMAN = {
    "XXBTZGBP": "BTC",
    "XETHZGBP": "ETH",
}


def _resolve_pair(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    return _HUMAN_TO_KRAKEN_PAIR.get(ticker.strip().upper())


def fetch_quote(ticker: str, *, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """Fetch a crypto quote. Returns the normalised quote dict (same
    shape as ``sources.yf.fetch_quote``) or ``None`` on failure."""
    pair = _resolve_pair(ticker)
    if pair is None:
        logger.debug("kraken: unknown ticker %r", ticker)
        return None
    try:
        import httpx  # type: ignore
    except Exception as exc:
        logger.warning("kraken source: httpx unavailable: %s", exc)
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(_BASE, params={"pair": pair})
            if r.status_code != 200:
                logger.debug("kraken %s: HTTP %d", pair, r.status_code)
                return None
            data = r.json()
    except Exception as exc:
        logger.debug("kraken %s: fetch failed: %s", pair, exc)
        return None

    if data.get("error"):
        logger.debug("kraken %s: api error %s", pair, data["error"])
        return None

    try:
        result = data.get("result") or {}
        # Kraken returns the result keyed by the canonical pair code
        block = result.get(pair) or next(iter(result.values()), {})
        # c = last trade [price, volume]; o = today's opening price;
        # v = volume [today, last 24h]
        last = float(block["c"][0])
        open_price = float(block["o"])
        vol_24h = float(block["v"][1])
    except Exception as exc:
        logger.debug("kraken %s: parse failed: %s", pair, exc)
        return None

    change_24h_pct = None
    if open_price > 0:
        change_24h_pct = (last - open_price) / open_price * 100.0

    human = _KRAKEN_PAIR_TO_HUMAN.get(pair, pair)
    return {
        "ticker": human,
        "last": last,
        "change_pct": change_24h_pct,         # crypto trades 24/7 → same number
        "change_24h_pct": change_24h_pct,
        "volume": vol_24h,
        "currency": "GBP",
        "market": "CRYPTO",
        "source": "kraken",
        "ts": time.time(),
    }


_OHLC_BASE = "https://api.kraken.com/0/public/OHLC"

# Kraken interval is in MINUTES. 1440 = 1 day.
_KRAKEN_INTERVAL_MIN = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1wk": 10080,
}


def fetch_history(ticker: str, range_str: str = "3mo",
                  interval: str = "1d", *,
                  timeout: float = 10.0) -> Optional[list]:
    """Fetch OHLCV history from Kraken's public OHLC endpoint.

    Returns the same list-of-bar-dicts shape as ``yf.fetch_history``
    or None on failure. Kraken's OHLC endpoint takes a `since`
    timestamp; we compute it from range_str and the interval.
    """
    pair = _resolve_pair(ticker)
    if pair is None:
        return None
    minutes = _KRAKEN_INTERVAL_MIN.get(interval, 1440)
    # Translate range_str to a since-seconds value
    range_days_map = {
        "1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 184,
        "1y": 366, "2y": 732, "5y": 1830,
    }
    days = range_days_map.get(range_str, 92)
    since = int(time.time()) - (days * 86400)
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(_OHLC_BASE, params={
                "pair": pair, "interval": str(minutes), "since": str(since),
            })
            if r.status_code != 200:
                logger.debug("kraken history %s: HTTP %d", pair, r.status_code)
                return None
            data = r.json()
    except Exception as exc:
        logger.debug("kraken history %s: fetch failed: %s", pair, exc)
        return None
    if data.get("error"):
        logger.debug("kraken history %s: api error %s", pair, data["error"])
        return None
    try:
        result = data.get("result") or {}
        # Result is keyed by canonical pair; OHLC array is the matching value
        block = next(
            (v for k, v in result.items() if k != "last" and isinstance(v, list)),
            None,
        )
        if not block:
            return None
    except Exception:
        return None
    bars = []
    # Kraken row format: [ts, open, high, low, close, vwap, volume, count]
    for row in block:
        try:
            bars.append({
                "ts": float(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low":  float(row[3]),
                "close": float(row[4]),
                "volume": float(row[6]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    return bars


def is_available() -> bool:
    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=3.0) as client:
            r = client.get(_BASE, params={"pair": "XXBTZGBP"})
            if r.status_code != 200:
                return False
            return not (r.json() or {}).get("error")
    except Exception:
        return False


__all__ = ["fetch_quote", "fetch_history", "is_available"]
