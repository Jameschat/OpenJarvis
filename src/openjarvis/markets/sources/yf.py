"""yfinance source — US + UK equity quotes.

Free, no API key, undocumented endpoints — historically breaks every
4-6 months when Yahoo changes their backend. Acceptable as the v0
primary because zero cost; swap to Polygon/IBKR when value is proven
and the £29-80/mo per source is justified.

Single function ``fetch_quote(ticker)`` returns a normalised dict
or None on failure. UK tickers should be passed with the ``.L``
suffix Yahoo expects (e.g. ``SHEL.L``, ``VOD.L``).

We intentionally do NOT depend on yfinance the package — even
``yfinance==0.2.x`` pulls in pandas + numpy + lxml which the Jarvis
runtime probably already has, but adding it as a hard import would
take this module down whenever yfinance breaks. Instead we hit the
v8 chart endpoint directly via httpx; the contract is small and
reverse-engineerable. If Yahoo nukes v8 we add yfinance as a fallback.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BASE = "https://query2.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {
    # Yahoo's v8 endpoint blocks empty / obvious-bot UAs. Use a
    # current-ish desktop Chrome string.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def fetch_quote(ticker: str, *, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """Fetch a single quote. Returns:

        {ticker, last, change_pct, change_24h_pct, volume,
         currency, market, source, ts}

    or ``None`` on any failure (network, parse, missing fields).
    """
    if not ticker:
        return None
    sym = ticker.strip().upper()
    try:
        import httpx  # type: ignore
    except Exception as exc:
        logger.warning("yfinance source: httpx unavailable: %s", exc)
        return None
    url = _BASE + sym
    params = {
        "interval": "1m",
        "range": "1d",
        "includePrePost": "false",
    }
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(url, params=params)
            if r.status_code != 200:
                logger.debug("yf %s: HTTP %d", sym, r.status_code)
                return None
            data = r.json()
    except Exception as exc:
        logger.debug("yf %s: fetch failed: %s", sym, exc)
        return None

    try:
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        last = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        currency = meta.get("currency")
        market = _classify_market(meta.get("exchangeName") or "", sym)
        volume = meta.get("regularMarketVolume")
    except Exception as exc:
        logger.debug("yf %s: parse failed: %s", sym, exc)
        return None

    if last is None:
        return None
    change_pct = None
    if prev_close and prev_close > 0:
        try:
            change_pct = (float(last) - float(prev_close)) / float(prev_close) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            change_pct = None

    return {
        "ticker": sym,
        "last": float(last),
        "change_pct": change_pct,
        "change_24h_pct": change_pct,    # equity 24h is intraday for v0
        "volume": float(volume) if volume is not None else None,
        "currency": currency,
        "market": market,
        "source": "yfinance",
        "ts": time.time(),
    }


def _classify_market(exchange: str, ticker: str) -> str:
    e = (exchange or "").upper()
    if "LSE" in e or "LON" in e or ticker.endswith(".L"):
        return "UK"
    if any(x in e for x in ("NYSE", "NASDAQ", "AMEX", "ARCA", "NMS", "NCM", "NGM")):
        return "US"
    if ticker.endswith(".L"):
        return "UK"
    return "US"


def is_available() -> bool:
    """Cheap availability check — does the Yahoo endpoint respond at all?"""
    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=3.0, headers=_HEADERS) as client:
            r = client.get(_BASE + "AAPL", params={"interval": "1d", "range": "1d"})
            return r.status_code == 200
    except Exception:
        return False


__all__ = ["fetch_quote", "is_available"]
