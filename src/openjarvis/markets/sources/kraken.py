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


__all__ = ["fetch_quote", "is_available"]
