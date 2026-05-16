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
import re
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

# Wider universe cache keyed by n (e.g. 250, 500, 1000). Same TTL as top_100.
_top_n_cache: Dict[int, List[Dict[str, Any]]] = {}
_top_n_cached_at: Dict[int, float] = {}
_TOP_N_TTL_S = 600.0
_categories_cache: List[Dict[str, str]] = []
_categories_cached_at: float = 0.0
_CATEGORIES_TTL_S = 24 * 3600.0


def _normalise_market_coin(c: Dict[str, Any], *, now: float, vs_currency: str) -> Optional[Dict[str, Any]]:
    try:
        return {
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
            "market_cap_rank": c.get("market_cap_rank"),
            "volume_24h": (
                float(c["total_volume"]) if c.get("total_volume") is not None else None
            ),
            "sparkline_7d": (
                list(c.get("sparkline_in_7d", {}).get("price", []))
                if c.get("sparkline_in_7d") else []
            ),
            "image": c.get("image"),
            "ts": now,
            "currency": vs_currency.upper(),
            "source": "coingecko",
        }
    except (TypeError, ValueError, KeyError):
        return None


def _slugify_coin_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")


def _coin_matches(c: Dict[str, Any], s_upper: str, slug: str) -> bool:
    return (
        (c.get("symbol") or "").upper() == s_upper
        or (c.get("id") or "").lower() == slug
        or _slugify_coin_id(c.get("name") or "") == slug
    )


def _resolve_id(symbol: str) -> Optional[str]:
    """Return the CoinGecko coin id for a ticker, coin name, or slug."""
    if not symbol:
        return None
    raw = symbol.strip()
    s = raw.upper()
    for sfx in ("-GBP", "/GBP", "GBP", "-USD", "/USD", "USD"):
        if s.endswith(sfx) and s != sfx:
            s = s[: -len(sfx)] or s
            raw = raw[: -len(sfx)] or raw
            break
    slug = _slugify_coin_id(raw)
    with _lock:
        for c in _top_100_cache:
            if _coin_matches(c, s, slug):
                return c.get("id")
        for n in sorted(_top_n_cache.keys(), reverse=True):
            for c in _top_n_cache[n]:
                if _coin_matches(c, s, slug):
                    return c.get("id")
    if s in _FALLBACK_SYMBOL_TO_ID:
        return _FALLBACK_SYMBOL_TO_ID.get(s)
    searched = _search_coin_id(raw)
    if searched:
        return searched
    if slug and "-" in slug:
        return slug
    return None


def _search_coin_id(query: str, *, timeout: float = 8.0) -> Optional[str]:
    """Resolve long-tail symbols/names through CoinGecko /search.

    Needed for coins beyond the cached top-1000, e.g. Purple Pepe
    (rank ~1600, symbol PURPE, id purple-pepe).
    """
    q = (query or "").strip()
    if not q:
        return None
    q_upper = q.upper()
    q_slug = _slugify_coin_id(q)
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/search", params={"query": q})
            if r.status_code != 200:
                logger.debug("coingecko search %s: HTTP %d", q, r.status_code)
                return None
            coins = (r.json() or {}).get("coins") or []
    except Exception as exc:
        logger.debug("coingecko search %s: %s", q, exc)
        return None
    if not coins:
        return None
    for c in coins:
        if (c.get("symbol") or "").upper() == q_upper:
            return c.get("id")
    for c in coins:
        if (c.get("id") or "").lower() == q_slug:
            return c.get("id")
    for c in coins:
        if _slugify_coin_id(c.get("name") or "") == q_slug:
            return c.get("id")
    return coins[0].get("id")


def _search_coin_ids(query: str, *, limit: int = 100, timeout: float = 8.0) -> List[str]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        import httpx  # type: ignore
    except Exception:
        return []
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/search", params={"query": q})
            if r.status_code != 200:
                return []
            coins = (r.json() or {}).get("coins") or []
    except Exception as exc:
        logger.debug("coingecko search ids %s: %s", q, exc)
        return []
    ids: List[str] = []
    seen = set()
    for c in coins:
        coin_id = c.get("id")
        if not coin_id or coin_id in seen:
            continue
        seen.add(coin_id)
        ids.append(str(coin_id))
        if len(ids) >= limit:
            break
    return ids


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
            row = _normalise_market_coin(c, now=now, vs_currency=vs_currency)
            if row:
                out.append(row)
        except (TypeError, ValueError, KeyError):
            continue
    with _lock:
        _top_100_cache.clear()
        _top_100_cache.extend(out)
        _top_100_cached_at = now
    return list(out)


def fetch_top_n(n: int = 1000, *, vs_currency: str = "gbp",
                timeout: float = 15.0,
                page_gap_s: float = 2.5) -> List[Dict[str, Any]]:
    """Fetch the top-n coins by market cap, paginating /coins/markets.

    CoinGecko caps per_page at 250, so n=1000 → 4 sequential calls.
    Stays under the free-tier 30/min ceiling with ``page_gap_s``
    between pages. Cached for 10 minutes per ``n``. Returns a list of
    dicts in the same shape as :func:`fetch_top_100`.
    """
    if n <= 100:
        # Reuse the dedicated 100-cache so we don't double-paginate.
        return fetch_top_100(vs_currency=vs_currency, timeout=timeout)[:n]

    now = time.time()
    with _lock:
        cached = _top_n_cache.get(n)
        cached_at = _top_n_cached_at.get(n, 0.0)
        if cached and (now - cached_at) < _TOP_N_TTL_S:
            return list(cached)

    try:
        import httpx  # type: ignore
    except Exception:
        with _lock:
            return list(_top_n_cache.get(n) or [])

    per_page = 250
    pages = (n + per_page - 1) // per_page
    out: List[Dict[str, Any]] = []
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            for page in range(1, pages + 1):
                if page > 1:
                    time.sleep(page_gap_s)
                r = client.get(_BASE + "/coins/markets", params={
                    "vs_currency": vs_currency,
                    "order": "market_cap_desc",
                    "per_page": str(per_page),
                    "page": str(page),
                    "sparkline": "true",
                    "price_change_percentage": "24h,7d,30d",
                })
                if r.status_code != 200:
                    logger.debug("coingecko top_n page %d: HTTP %d",
                                 page, r.status_code)
                    break
                data = r.json() or []
                if not data:
                    break
                for c in data:
                    try:
                        row = _normalise_market_coin(c, now=now, vs_currency=vs_currency)
                        if row:
                            out.append(row)
                    except (TypeError, ValueError, KeyError):
                        continue
    except Exception as exc:
        logger.debug("coingecko top_n: fetch failed: %s", exc)
        if not out:
            with _lock:
                return list(_top_n_cache.get(n) or [])

    out = out[:n]
    with _lock:
        _top_n_cache[n] = list(out)
        _top_n_cached_at[n] = now
    return list(out)


def fetch_markets_page(
    *,
    page: int = 1,
    per_page: int = 100,
    vs_currency: str = "gbp",
    category: str | None = None,
    query: str | None = None,
    sparkline: bool = False,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Fetch one paginated page of CoinGecko market prices.

    This is the 3Commas-style broad price-table path: callers can page
    through the whole CoinGecko markets universe instead of being capped
    at Jarvis's top-1000 pulse scan.
    """
    page = max(1, int(page or 1))
    per_page = min(250, max(1, int(per_page or 100)))
    ccy = (vs_currency or "gbp").strip().lower()
    if not re.fullmatch(r"[a-z]{3,5}", ccy):
        ccy = "gbp"
    now = time.time()
    try:
        import httpx  # type: ignore
    except Exception:
        return {"ok": False, "error": "httpx unavailable", "coins": []}
    params = {
        "vs_currency": ccy,
        "order": "market_cap_desc",
        "per_page": str(per_page),
        "page": str(page),
        "sparkline": "true" if sparkline else "false",
        "price_change_percentage": "24h,7d,30d",
    }
    if category:
        params["category"] = category
    if query:
        ids = _search_coin_ids(query, limit=max(per_page * page, per_page), timeout=timeout)
        start = (page - 1) * per_page
        ids_page = ids[start:start + per_page]
        if not ids_page:
            return {
                "ok": True,
                "coins": [],
                "page": page,
                "per_page": per_page,
                "has_next": False,
                "currency": ccy.upper(),
                "category": category,
                "query": query,
                "source": "coingecko",
                "ts": now,
            }
        params["ids"] = ",".join(ids_page)
        params.pop("category", None)
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/coins/markets", params=params)
            if r.status_code != 200:
                logger.debug("coingecko markets page %d: HTTP %d", page, r.status_code)
                return {"ok": False, "error": f"coingecko HTTP {r.status_code}", "coins": []}
            data = r.json() or []
    except Exception as exc:
        logger.debug("coingecko markets page %d: %s", page, exc)
        return {"ok": False, "error": str(exc), "coins": []}
    coins = []
    for c in data:
        row = _normalise_market_coin(c, now=now, vs_currency=ccy)
        if row:
            coins.append(row)
    return {
        "ok": True,
        "coins": coins,
        "page": page,
        "per_page": per_page,
        "has_next": len(coins) == per_page,
        "currency": ccy.upper(),
        "category": category,
        "query": query,
        "source": "coingecko",
        "ts": now,
    }


def fetch_categories_list(*, timeout: float = 10.0) -> List[Dict[str, str]]:
    """CoinGecko category ids for price-page filters."""
    global _categories_cached_at
    now = time.time()
    with _lock:
        if _categories_cache and (now - _categories_cached_at) < _CATEGORIES_TTL_S:
            return list(_categories_cache)
    try:
        import httpx  # type: ignore
    except Exception:
        return list(_categories_cache)
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/coins/categories/list")
            if r.status_code != 200:
                return list(_categories_cache)
            data = r.json() or []
    except Exception as exc:
        logger.debug("coingecko categories: %s", exc)
        return list(_categories_cache)
    out = [
        {"id": str(row.get("category_id") or ""), "name": str(row.get("name") or "")}
        for row in data
        if row.get("category_id") and row.get("name")
    ]
    with _lock:
        _categories_cache.clear()
        _categories_cache.extend(out)
        _categories_cached_at = now
    return list(out)


def fetch_coin_detail(coin_id: str, *,
                      timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Per-coin detail call — used for fields the bulk /coins/markets
    endpoint omits, notably ``genesis_date`` for age-based risk scoring.

    Expensive (1 call per coin), so callers should cache aggressively.
    """
    if not coin_id:
        return None
    try:
        import httpx  # type: ignore
    except Exception:
        return None
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + f"/coins/{coin_id}", params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            })
            if r.status_code != 200:
                logger.debug("coingecko coin_detail %s: HTTP %d",
                             coin_id, r.status_code)
                return None
            return r.json() or None
    except Exception as exc:
        logger.debug("coingecko coin_detail %s: %s", coin_id, exc)
        return None


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


def _bars_from_market_chart(data: Dict[str, Any], *, bucket_s: int = 4 * 3600) -> List[Dict[str, Any]]:
    prices = data.get("prices") or []
    volumes = data.get("total_volumes") or []
    vol_by_bucket: Dict[int, float] = {}
    for row in volumes:
        try:
            b = int((float(row[0]) / 1000.0) // bucket_s) * bucket_s
            vol_by_bucket[b] = vol_by_bucket.get(b, 0.0) + float(row[1] or 0.0)
        except (IndexError, TypeError, ValueError):
            continue
    buckets: Dict[int, List[float]] = {}
    for row in prices:
        try:
            ts = float(row[0]) / 1000.0
            price = float(row[1])
            b = int(ts // bucket_s) * bucket_s
            buckets.setdefault(b, []).append(price)
        except (IndexError, TypeError, ValueError):
            continue
    bars: List[Dict[str, Any]] = []
    for ts in sorted(buckets):
        vals = buckets[ts]
        if not vals:
            continue
        bars.append({
            "ts": float(ts),
            "open": vals[0],
            "high": max(vals),
            "low": min(vals),
            "close": vals[-1],
            "volume": vol_by_bucket.get(ts),
        })
    return bars


def fetch_history(ticker: str, range_str: str = "3mo", *,
                  timeout: float = 12.0) -> Optional[list]:
    """OHLCV history for a CoinGecko coin.

    Prefer /ohlc when available. For long-tail coins where CoinGecko has
    chart data but /ohlc returns no rows, fall back to /market_chart and
    synthesize 4h OHLC bars from the price series.
    """
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
            bars = []
            if r.status_code == 200:
                for row in r.json() or []:
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
            else:
                logger.debug("coingecko ohlc %s: HTTP %d", coin_id, r.status_code)
            if bars:
                return bars

            r2 = client.get(_BASE + f"/coins/{coin_id}/market_chart", params={
                "vs_currency": "gbp", "days": str(days),
            })
            if r2.status_code != 200:
                logger.debug("coingecko market_chart %s: HTTP %d", coin_id, r2.status_code)
                return None
            fallback = _bars_from_market_chart(r2.json() or {})
            return fallback or None
    except Exception as exc:
        logger.debug("coingecko history %s: %s", coin_id, exc)
        return None


_global_cache: Dict[str, Any] = {}
_global_cached_at: float = 0.0
_GLOBAL_TTL_S = 60.0


def fetch_global(*, vs_currency: str = "gbp",
                 timeout: float = 8.0) -> Dict[str, Any]:
    """CoinGecko /global — total market cap, BTC dominance, 24h volume.
    Cached for 60s. Returns empty dict on failure."""
    global _global_cached_at
    now = time.time()
    with _lock:
        if _global_cache and (now - _global_cached_at) < _GLOBAL_TTL_S:
            return dict(_global_cache)
    try:
        import httpx  # type: ignore
    except Exception:
        return dict(_global_cache)
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(_BASE + "/global")
            if r.status_code != 200:
                return dict(_global_cache)
            data = (r.json() or {}).get("data", {}) or {}
    except Exception as exc:
        logger.debug("coingecko global: %s", exc)
        return dict(_global_cache)
    ccy = vs_currency.lower()
    out = {
        "total_market_cap":   (data.get("total_market_cap") or {}).get(ccy),
        "total_volume_24h":   (data.get("total_volume") or {}).get(ccy),
        "market_cap_pct_24h": data.get("market_cap_change_percentage_24h_usd"),
        "btc_dominance":      (data.get("market_cap_percentage") or {}).get("btc"),
        "eth_dominance":      (data.get("market_cap_percentage") or {}).get("eth"),
        "active_cryptocurrencies": data.get("active_cryptocurrencies"),
        "ts": now,
        "currency": vs_currency.upper(),
    }
    with _lock:
        _global_cache.clear()
        _global_cache.update(out)
        _global_cached_at = now
    return dict(out)


def is_available() -> bool:
    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=4.0, headers=_HEADERS) as client:
            r = client.get(_BASE + "/ping")
            return r.status_code == 200
    except Exception:
        return False


__all__ = [
    "fetch_top_100", "fetch_top_n", "fetch_markets_page", "fetch_categories_list", "fetch_coin_detail",
    "fetch_quote", "fetch_history", "fetch_global",
    "is_available",
]
