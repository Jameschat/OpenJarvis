"""CoinGecko public source — top-100 crypto coverage in GBP.

Free, no API key (Demo plan, ~30 calls/min). Used as the primary
source for the crypto universe so the operator can monitor / paper-
trade across the full top-100, not just BTC + ETH on Kraken.

Public API base: https://api.coingecko.com/api/v3

Three functions:
  - fetch_top_100()  → list of coin dicts (id, symbol, name, price,
                       24h change, market cap, sparkline 7d)
  - fetch_quote(sym) → single normalised quote dict (matching yf shape)
  - fetch_history(sym, range_str)
                     → OHLCV-ish bars (CoinGecko's /coins/{id}/ohlc)

The symbol→id mapping is cached in-process from the top-100 list and
falls back to a tiny hardcoded map for the obvious aliases (btc→bitcoin,
eth→ethereum, etc.) so quote lookups work before the first top-100
fetch happens.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE = "https://api.coingecko.com/api/v3"
_HEADERS = {
    "User-Agent": "OpenJarvis/1.0 (jarvis personal assistant)",
    "Accept": "application/json",
}

# Hardcoded fallback for symbol→coingecko-id when the cache is cold.
# Top ~30 by mcap as of 2026-04 — the rest get filled in once
# fetch_top_100 has been called once.
_FALLBACK_SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "BNB": "binancecoin",
    "SOL": "solana",
    "USDC": "usd-coin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "TON": "the-open-network",
    "ADA": "cardano",
    "TRX": "tron",
    "AVAX": "avalanche-2",
    "SHIB": "shiba-inu",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "BCH": "bitcoin-cash",
    "MATIC": "matic-network",
    "POL": "matic-network",          # POL = MATIC v2 ticker on some venues
    "NEAR": "near",
    "LTC": "litecoin",
    "UNI": "uniswap",
    "ICP": "internet-computer",
    "DAI": "dai",
    "ETC": "ethereum-classic",
    "ATOM": "cosmos",
    "APT": "aptos",
    "RNDR": "render-token",
    "RENDER": "render-token",
    "HBAR": "hedera-hashgraph",
    "FIL": "filecoin",
    "ARB": "arbitrum",
    "OP": "optimism",
    "STX": "blockstream",            # rare collision — verify if used
    "INJ": "injective-protocol",
    "GRT": "the-graph",
    "MKR": "maker",
    "SUI": "sui",
    "AAVE": "aave",
    "LDO": "lido-dao",
    "PEPE": "pepe",
}

_lock = threading.RLock()
_top_100_cache: List[Dict[str, Any]] = []
_top_100_cached_at: float = 0.0
_TOP_100_TTL_S = 600.0   # refresh every 10 min


def _resolve_id(symbol: str) -> Optional[str]:
    """Return the CoinGecko coin id for a human ticker like 'BTC'."""
    if not symbol:
        return None
    s = symbol.strip().upper()
    # Strip common GBP/USD suffixes
    for sfx in ("-GBP", "/GBP", "GBP", "-USD", "/USD", "USD"):
        if s.endswith(sfx) and s != sfx:
            s = s[: -len(sfx)] or s
            break
    with _lock:
        for c in _top_100_cache:
            if c.get("symbol", "").upper() == s:
                return c.get("id")
    return _FALLBACK_SYMBOL_TO_ID.get(s)


def fetch_top_100(*, vs_currency: str = "gbp",
                  timeout: float = 12.0) -> List[Dict[str, Any]]:
    """Fetch the top-100 coins by market cap. Cached for 10 minutes.

    Returns a list of:
        {id, symbol, name, last, change_pct, change_24h_pct,
         market_cap, volume_24h, sparkline_7d, ts}
    """
    global _top_100_cached_at
    now = time.time()
    with _lock:
        if _top_100_cache and (now - _top_100_cached_at) < _TOP_100_TTL_S:
            return list(_top_100_cache)
    try:
        import httpx  # type: ignore
    except Exception:
        return list(_top_100_cache)
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/coins/markets", params={
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": "100",
                "page": "1",
                "sparkline": "true",
                "price_change_percentage": "24h,7d,30d",
            })
            if r.status_code != 200:
                logger.debug("coingecko top_100: HTTP %d", r.status_code)
                return list(_top_100_cache)
            data = r.json() or []
    except Exception as exc:
        logger.debug("coingecko top_100: fetch failed: %s", exc)
        return list(_top_100_cache)
    out = []
    for c in data:
        try:
            out.append({
                "id": c.get("id"),
                "symbol": (c.get("symbol") or "").upper(),
                "name": c.get("name"),
                "last": float(c.get("current_price") or 0.0),
                "change_pct": (
                    float(c["price_change_percentage_24h"])
                    if c.get("price_change_percentage_24h") is not None else None
                ),
                "change_24h_pct": (
                    float(c["price_change_percentage_24h"])
                    if c.get("price_change_percentage_24h") is not None else None
                ),
                "change_7d_pct": (
                    float(c["price_change_percentage_7d_in_currency"])
                    if c.get("price_change_percentage_7d_in_currency") is not None else None
                ),
                "change_30d_pct": (
                    float(c["price_change_percentage_30d_in_currency"])
                    if c.get("price_change_percentage_30d_in_currency") is not None else None
                ),
                "market_cap": (
                    float(c["market_cap"]) if c.get("market_cap") is not None else None
                ),
                "volume_24h": (
                    float(c["total_volume"]) if c.get("total_volume") is not None else None
                ),
                "sparkline_7d": (
                    list(c.get("sparkline_in_7d", {}).get("price", []))
                    if c.get("sparkline_in_7d") else []
                ),
                "ts": now,
                "currency": vs_currency.upper(),
                "source": "coingecko",
            })
        except (TypeError, ValueError, KeyError):
            continue
    with _lock:
        _top_100_cache.clear()
        _top_100_cache.extend(out)
        _top_100_cached_at = now
    return list(out)


def fetch_quote(ticker: str, *, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """Single-coin quote in GBP, normalised to match the yf/kraken
    shape so callers don't care which source ran."""
    coin_id = _resolve_id(ticker)
    if coin_id is None:
        return None
    # Try the warm top-100 first — usually the answer
    with _lock:
        for c in _top_100_cache:
            if c.get("id") == coin_id:
                return {
                    "ticker": (c.get("symbol") or ticker).upper(),
                    "last": c.get("last"),
                    "change_pct": c.get("change_24h_pct"),
                    "change_24h_pct": c.get("change_24h_pct"),
                    "volume": c.get("volume_24h"),
                    "currency": "GBP",
                    "market": "CRYPTO",
                    "source": "coingecko",
                    "ts": c.get("ts") or time.time(),
                }
    # Cold cache — single-coin call
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/simple/price", params={
                "ids": coin_id,
                "vs_currencies": "gbp",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            })
            if r.status_code != 200:
                return None
            d = r.json() or {}
            row = d.get(coin_id)
            if not row:
                return None
            return {
                "ticker": (ticker or "").upper(),
                "last": float(row.get("gbp") or 0.0),
                "change_pct": (
                    float(row["gbp_24h_change"])
                    if row.get("gbp_24h_change") is not None else None
                ),
                "change_24h_pct": (
                    float(row["gbp_24h_change"])
                    if row.get("gbp_24h_change") is not None else None
                ),
                "volume": (
                    float(row["gbp_24h_vol"])
                    if row.get("gbp_24h_vol") is not None else None
                ),
                "currency": "GBP",
                "market": "CRYPTO",
                "source": "coingecko",
                "ts": time.time(),
            }
    except Exception as exc:
        logger.debug("coingecko quote %s: %s", coin_id, exc)
        return None


def fetch_history(ticker: str, range_str: str = "3mo", *,
                  timeout: float = 12.0) -> Optional[list]:
    """OHLCV history. CoinGecko returns 4-hourly bars for ranges 8-90
    days; daily bars for >90 days. Returned in the same bar shape as
    yf/kraken: [{ts, open, high, low, close, volume}, ...]."""
    coin_id = _resolve_id(ticker)
    if coin_id is None:
        return None
    days_map = {"1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}
    days = days_map.get(range_str, 90)
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + f"/coins/{coin_id}/ohlc", params={
                "vs_currency": "gbp", "days": str(days),
            })
            if r.status_code != 200:
                logger.debug("coingecko ohlc %s: HTTP %d", coin_id, r.status_code)
                return None
            data = r.json() or []
    except Exception as exc:
        logger.debug("coingecko ohlc %s: %s", coin_id, exc)
        return None
    bars = []
    # CoinGecko OHLC row: [timestamp_ms, open, high, low, close]
    # No volume on this endpoint — set to None.
    for row in data:
        try:
            bars.append({
                "ts": float(row[0]) / 1000.0,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": None,
            })
        except (IndexError, ValueError, TypeError):
            continue
    return bars


def is_available() -> bool:
    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=4.0, headers=_HEADERS) as client:
            r = client.get(_BASE + "/ping")
            return r.status_code == 200
    except Exception:
        return False


__all__ = [
    "fetch_top_100", "fetch_quote", "fetch_history", "is_available",
]
