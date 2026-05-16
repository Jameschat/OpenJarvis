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


def fetch_history(ticker: str, range_str: str = "3mo",
                  interval: str = "1d", *,
                  timeout: float = 12.0) -> Optional[list]:
    """Fetch OHLCV history. Returns a list of bar dicts:

        [{ts, open, high, low, close, volume}, ...]

    or None on failure. ts is a Unix timestamp (seconds) at the bar
    start. range_str: "1d","5d","1mo","3mo","6mo","1y","2y","5y".
    interval: "1m","5m","15m","30m","1h","1d","1wk","1mo".
    """
    if not ticker:
        return None
    sym = ticker.strip().upper()
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + sym, params={
                "interval": interval, "range": range_str,
                "includePrePost": "false",
            })
            if r.status_code != 200:
                logger.debug("yf history %s: HTTP %d", sym, r.status_code)
                return None
            data = r.json()
    except Exception as exc:
        logger.debug("yf history %s: fetch failed: %s", sym, exc)
        return None
    try:
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return None
        block = result[0]
        timestamps = block.get("timestamp") or []
        ind = (block.get("indicators") or {}).get("quote") or [{}]
        q = ind[0] if ind else {}
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        volumes = q.get("volume") or []
    except Exception as exc:
        logger.debug("yf history %s: parse failed: %s", sym, exc)
        return None
    bars = []
    for i, ts in enumerate(timestamps):
        # Yahoo emits null entries on illiquid bars. Drop those.
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        bars.append({
            "ts": float(ts),
            "open": float(opens[i]) if i < len(opens) and opens[i] is not None else None,
            "high": float(highs[i]) if i < len(highs) and highs[i] is not None else None,
            "low":  float(lows[i])  if i < len(lows)  and lows[i]  is not None else None,
            "close": float(c),
            "volume": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
        })
    return bars


def is_available() -> bool:
    """Cheap availability check — does the Yahoo endpoint respond at all?"""
    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=3.0, headers=_HEADERS) as client:
            r = client.get(_BASE + "AAPL", params={"interval": "1d", "range": "1d"})
            return r.status_code == 200
    except Exception:
        return False


__all__ = ["fetch_quote", "fetch_history", "is_available"]
