"""LLM-callable tools for the markets subsystem.

Registered into ``cli/tool_use.py`` so gpt-4o can call them as native
function calls. Day-1 surface is intentionally narrow:

  - stock_price(ticker)         live US/UK quote via yfinance
  - crypto_price(ticker)        live BTC/ETH quote via Kraken
  - watchlist_get()             current watchlist + cached prices
  - watchlist_add(ticker, market)
  - watchlist_remove(ticker)

Bias toward facts. Tools fetch + show; the LLM synthesises. There is
no ``analyze_and_recommend`` super-tool — that's where hallucinations
hide. The recommendation pipeline (next session) is a separate call
path with its own validators.

Every tool returns a JSON-serialisable dict the LLM can quote. On
failure we return a dict with an ``error`` field rather than raising
— the LLM handles that more usefully than a stack trace.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from openjarvis.markets import store
from openjarvis.markets.sources import yf, kraken

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Price tools
# ---------------------------------------------------------------------------

def stock_price(ticker: str) -> str:
    """Fetch a live equity quote (US or UK). Updates the local price
    cache so the watchlist HUD reflects the freshly-fetched figure."""
    if not ticker:
        return json.dumps({"error": "ticker required"})
    quote = yf.fetch_quote(ticker)
    if quote is None:
        # Try Kraken in case the operator typed a crypto ticker into
        # the wrong tool — graceful fallthrough.
        quote = kraken.fetch_quote(ticker)
    if quote is None:
        return json.dumps({"error": f"no quote for {ticker!r}"})
    try:
        store.upsert_price_latest(
            ticker=quote["ticker"],
            last=quote["last"],
            change_pct=quote.get("change_pct"),
            change_24h_pct=quote.get("change_24h_pct"),
            volume=quote.get("volume"),
            currency=quote.get("currency"),
            source=quote.get("source"),
            ts=quote.get("ts"),
        )
    except Exception:
        logger.debug("stock_price: cache update failed", exc_info=True)
    return json.dumps(quote)


def crypto_price(ticker: str = "BTC") -> str:
    """Fetch a live crypto quote (BTC or ETH on Kraken GBP pairs)."""
    quote = kraken.fetch_quote(ticker)
    if quote is None:
        return json.dumps({"error": f"no quote for {ticker!r}"})
    try:
        store.upsert_price_latest(
            ticker=quote["ticker"],
            last=quote["last"],
            change_pct=quote.get("change_pct"),
            change_24h_pct=quote.get("change_24h_pct"),
            volume=quote.get("volume"),
            currency=quote.get("currency"),
            source=quote.get("source"),
            ts=quote.get("ts"),
        )
    except Exception:
        logger.debug("crypto_price: cache update failed", exc_info=True)
    return json.dumps(quote)


# ---------------------------------------------------------------------------
# Watchlist tools
# ---------------------------------------------------------------------------

def watchlist_get() -> str:
    """Return the operator's current watchlist with cached last prices."""
    items = store.watchlist_get()
    return json.dumps({"items": items, "count": len(items)})


def watchlist_add(ticker: str, market: str = "US") -> str:
    """Add a ticker to the watchlist. ``market`` is one of
    ``US`` / ``UK`` / ``CRYPTO``. UK tickers should include the ``.L``
    suffix (e.g. ``SHEL.L``); crypto should be plain symbol
    (``BTC``, ``ETH``)."""
    ok = store.watchlist_add(ticker, market=market)
    if not ok:
        return json.dumps({
            "ok": False,
            "error": "invalid ticker or market (use US|UK|CRYPTO)",
        })
    # Trigger a fetch so the watchlist HUD shows a price immediately,
    # not "—" until the next ingestion tick.
    try:
        if market.upper() == "CRYPTO":
            crypto_price(ticker)
        else:
            stock_price(ticker)
    except Exception:
        logger.debug("watchlist_add: opportunistic price fetch failed",
                     exc_info=True)
    return json.dumps({"ok": True, "ticker": ticker.upper(), "market": market.upper()})


def watchlist_remove(ticker: str) -> str:
    """Remove a ticker from the watchlist. Idempotent — succeeds
    quietly if the ticker wasn't on the list."""
    removed = store.watchlist_remove(ticker)
    return json.dumps({"ok": True, "removed": removed,
                       "ticker": (ticker or "").upper()})


# ---------------------------------------------------------------------------
# OpenAI function schemas — registered in tool_use.py via TOOL_SCHEMAS
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "stock_price",
            "description": (
                "Fetch a LIVE quote for a US or UK equity ticker. "
                "Returns last price, intraday % change, volume, and "
                "currency. CALL THIS for any 'how is X', 'what's X "
                "doing', 'price of X' question about a specific stock. "
                "Do NOT answer from training data — equity prices are "
                "stale within seconds. UK tickers include the '.L' "
                "suffix (e.g. SHEL.L)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "Stock ticker. NYSE/NASDAQ symbols plain "
                            "(AAPL, NVDA), LSE symbols with .L suffix "
                            "(SHEL.L, VOD.L)."
                        ),
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crypto_price",
            "description": (
                "Fetch a LIVE crypto quote in GBP via Kraken. CALL "
                "THIS for any 'price of bitcoin / ethereum / BTC / ETH' "
                "question. Returns last price, 24h % change, volume. "
                "Do NOT answer from training data — crypto prices "
                "drift within seconds. Day-1 supports BTC and ETH only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "BTC or ETH (case-insensitive).",
                        "default": "BTC",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watchlist_get",
            "description": (
                "Return the operator's current Markets watchlist with "
                "the latest cached price per ticker. Use when the "
                "operator asks 'what's on my watchlist', 'show me my "
                "watchlist', 'how are my picks doing'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watchlist_add",
            "description": (
                "Add a ticker to the operator's Markets watchlist. "
                "Use when the operator says 'add NVDA to my watchlist' "
                "/ 'watch BP.L' / 'put bitcoin on my watchlist'. "
                "ALWAYS pass the right market: US for NYSE/NASDAQ, UK "
                "for LSE (.L suffix on the ticker), CRYPTO for BTC/ETH."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "market": {
                        "type": "string",
                        "enum": ["US", "UK", "CRYPTO"],
                        "default": "US",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watchlist_remove",
            "description": (
                "Remove a ticker from the operator's Markets watchlist. "
                "Idempotent. Use when the operator says 'remove X from "
                "my watchlist' / 'stop watching X' / 'drop X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                },
                "required": ["ticker"],
            },
        },
    },
]


# Dispatch map for the registry in tool_use.py (matches the existing
# _TOOL_DISPATCH pattern).
TOOL_DISPATCH = {
    "stock_price":      stock_price,
    "crypto_price":     crypto_price,
    "watchlist_get":    watchlist_get,
    "watchlist_add":    watchlist_add,
    "watchlist_remove": watchlist_remove,
}


__all__ = [
    "TOOL_SCHEMAS", "TOOL_DISPATCH",
    "stock_price", "crypto_price",
    "watchlist_get", "watchlist_add", "watchlist_remove",
]
