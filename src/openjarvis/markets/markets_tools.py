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
from openjarvis.markets.sources import yf, kraken, coingecko
from openjarvis.markets import bot_lab as _bot_lab
from openjarvis.markets import chart_analyst as _chart_analyst
from openjarvis.markets import paper_broker as _paper_broker

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
    """Fetch a live crypto quote in GBP. Coverage = top-100 by market
    cap via CoinGecko (BTC, ETH, SOL, XRP, ADA, DOGE, … any of the top
    100). Kraken is used as a tightness fallback for BTC + ETH where
    its GBP pairs have deeper liquidity."""
    sym = (ticker or "").strip().upper()
    quote = coingecko.fetch_quote(sym)
    if quote is None and sym in ("BTC", "ETH"):
        # Tightness fallback — Kraken's BTC/GBP and ETH/GBP pairs are
        # the deepest GBP pricing for these two specifically.
        quote = kraken.fetch_quote(sym)
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


def crypto_top_1000() -> str:
    """Return the top 1000 cryptos by market cap with risk labels."""
    from openjarvis.markets import risk as _risk
    coins = coingecko.fetch_top_n(1000)
    if not coins:
        return json.dumps({"error": "coingecko unavailable"})
    annotated = _risk.annotate(coins)
    slim = [{
        "rank": i + 1, "symbol": c.get("symbol"), "name": c.get("name"),
        "last": c.get("last"), "change_24h_pct": c.get("change_24h_pct"),
        "market_cap": c.get("market_cap"), "volume_24h": c.get("volume_24h"),
        "risk": c.get("risk"),
    } for i, c in enumerate(annotated)]
    return json.dumps({"coins": slim, "count": len(slim),
                       "currency": "GBP", "source": "coingecko"})


def crypto_prices_page(
    page: int = 1,
    per_page: int = 100,
    currency: str = "GBP",
    category: str = "",
    query: str = "",
) -> str:
    """Return one paginated page of the broad crypto price universe."""
    result = coingecko.fetch_markets_page(
        page=page,
        per_page=per_page,
        vs_currency=(currency or "GBP").lower(),
        category=(category or "").strip() or None,
        query=(query or "").strip() or None,
    )
    return json.dumps(result)


def crypto_top_100() -> str:
    """Return the top 100 cryptos by market cap (CoinGecko, GBP).
    Refreshed every 10 minutes server-side. Useful for the LLM to
    reason over the full crypto universe rather than just the
    operator's watchlist."""
    coins = coingecko.fetch_top_100()
    if not coins:
        return json.dumps({"error": "coingecko unavailable"})
    # Trim sparkline to keep the LLM payload manageable
    slim = [{
        "rank": i + 1,
        "symbol": c.get("symbol"),
        "name": c.get("name"),
        "last": c.get("last"),
        "change_24h_pct": c.get("change_24h_pct"),
        "change_7d_pct": c.get("change_7d_pct"),
        "change_30d_pct": c.get("change_30d_pct"),
        "market_cap": c.get("market_cap"),
        "volume_24h": c.get("volume_24h"),
    } for i, c in enumerate(coins)]
    return json.dumps({"coins": slim, "count": len(slim),
                       "currency": "GBP", "source": "coingecko"})


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



def paper_buy(ticker: str, gbp_amount: float,
              stop: float = None, tp1: float = None, tp2: float = None) -> str:
    """Open a simulated paper crypto position. No broker API call."""
    return json.dumps(_paper_broker.paper_buy(
        ticker, gbp_amount, stop=stop, tp1=tp1, tp2=tp2,
    ))


def paper_sell(ticker: str, reason: str = "closed_manually") -> str:
    """Close an open simulated paper crypto position."""
    return json.dumps(_paper_broker.paper_sell(ticker, reason=reason))


def paper_portfolio() -> str:
    """Return simulated paper portfolio cash, equity, positions, and P&L."""
    _paper_broker.check_open_positions()
    return json.dumps(_paper_broker.paper_portfolio())


def backtest_dca_bot(
    ticker: str,
    initial_cash_gbp: float = 1000.0,
    base_order_gbp: float = 100.0,
    safety_order_gbp: float = 100.0,
    max_safety_orders: int = 3,
    safety_order_deviation_pct: float = 3.0,
    take_profit_pct: float = 2.0,
    stop_loss_pct: float | None = None,
    fee_rate: float = 0.001,
    slippage_pct: float = 0.05,
    since_ts: int | None = None,
    limit: int | None = 500,
) -> str:
    """Run a PAPER-ONLY DCA bot backtest against cached OHLCV history."""
    try:
        result = _bot_lab.backtest_dca_from_history(
            ticker=ticker,
            since_ts=since_ts,
            limit=limit,
            initial_cash_gbp=initial_cash_gbp,
            base_order_gbp=base_order_gbp,
            safety_order_gbp=safety_order_gbp,
            max_safety_orders=max_safety_orders,
            safety_order_deviation_pct=safety_order_deviation_pct,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            fee_rate=fee_rate,
            slippage_pct=slippage_pct,
        )
        return json.dumps(result)
    except Exception as exc:
        logger.debug("backtest_dca_bot failed", exc_info=True)
        return json.dumps({"ok": False, "error": str(exc), "ticker": (ticker or "").upper()})


def backtest_grid_bot(
    ticker: str,
    initial_cash_gbp: float = 1000.0,
    lower_price: float = 90.0,
    upper_price: float = 110.0,
    grid_count: int = 10,
    order_gbp: float = 100.0,
    fee_rate: float = 0.001,
    slippage_pct: float = 0.05,
    since_ts: int | None = None,
    limit: int | None = 500,
) -> str:
    """Run a PAPER-ONLY fixed-range grid bot backtest."""
    try:
        result = _bot_lab.backtest_grid_from_history(
            ticker=ticker,
            since_ts=since_ts,
            limit=limit,
            initial_cash_gbp=initial_cash_gbp,
            lower_price=lower_price,
            upper_price=upper_price,
            grid_count=grid_count,
            order_gbp=order_gbp,
            fee_rate=fee_rate,
            slippage_pct=slippage_pct,
        )
        return json.dumps(result)
    except Exception as exc:
        logger.debug("backtest_grid_bot failed", exc_info=True)
        return json.dumps({"ok": False, "error": str(exc), "ticker": (ticker or "").upper()})


def sweep_dca_bot(
    ticker: str,
    take_profit_pct_values: list[float] | None = None,
    safety_order_deviation_pct_values: list[float] | None = None,
    max_safety_orders_values: list[int] | None = None,
    initial_cash_gbp: float = 1000.0,
    base_order_gbp: float = 100.0,
    safety_order_gbp: float = 100.0,
    fee_rate: float = 0.001,
    slippage_pct: float = 0.05,
    since_ts: int | None = None,
    limit: int | None = 500,
) -> str:
    """Run a bounded PAPER-ONLY DCA parameter sweep."""
    try:
        result = _bot_lab.sweep_dca_from_history(
            ticker=ticker,
            since_ts=since_ts,
            limit=limit,
            take_profit_pct_values=take_profit_pct_values,
            safety_order_deviation_pct_values=safety_order_deviation_pct_values,
            max_safety_orders_values=max_safety_orders_values,
            initial_cash_gbp=initial_cash_gbp,
            base_order_gbp=base_order_gbp,
            safety_order_gbp=safety_order_gbp,
            fee_rate=fee_rate,
            slippage_pct=slippage_pct,
        )
        return json.dumps(result)
    except Exception as exc:
        logger.debug("sweep_dca_bot failed", exc_info=True)
        return json.dumps({"ok": False, "error": str(exc), "ticker": (ticker or "").upper()})


def analyze_chart(image_path: str, ticker_hint: str = "",
                  timeframe: str = "2h") -> str:
    """Analyse a crypto chart screenshot. See chart_analyst.analyze_chart
    for the full pipeline. Schema in TOOL_SCHEMAS below."""
    hint = (ticker_hint or "").strip() or None
    return _chart_analyst.analyze_chart(
        image_path=image_path, ticker_hint=hint, timeframe=timeframe,
    )


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
                "Fetch a LIVE crypto quote in GBP. Coverage = top-100 "
                "cryptos by market cap via CoinGecko (BTC, ETH, SOL, "
                "XRP, ADA, DOGE, AVAX, DOT, LINK, MATIC, NEAR, etc.). "
                "Returns last price, 24h % change, volume. Do NOT "
                "answer from training data — crypto prices drift "
                "within seconds. For 'how is the crypto market doing' "
                "questions, use crypto_top_100 instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "Crypto symbol from the top 100 (case-"
                            "insensitive). Examples: BTC, ETH, SOL, "
                            "XRP, ADA, DOGE."
                        ),
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
            "name": "crypto_top_100",
            "description": (
                "Return the top 100 cryptocurrencies by market cap "
                "with live GBP prices, 24h / 7d / 30d % changes, "
                "market cap, and 24h volume. Use for 'what's hot in "
                "crypto', 'crypto market overview', 'biggest movers', "
                "'top performing crypto this week' style questions. "
                "Source: CoinGecko (free, refreshed every 10 minutes)."
            ),
            "parameters": {"type": "object", "properties": {}},
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
    {
        "type": "function",
        "function": {
            "name": "crypto_top_1000",
            "description": (
                "Return the top 1000 cryptocurrencies by market cap with live GBP prices "
                "and rug-pull/pump-and-dump risk labels. Use for long-tail crypto scans."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "paper_buy",
            "description": (
                "Open a PAPER-ONLY simulated crypto position. No real broker order is placed. "
                "Use when the operator asks to take a paper trade."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "gbp_amount": {"type": "number"},
                    "stop": {"type": "number"},
                    "tp1": {"type": "number"},
                    "tp2": {"type": "number"},
                },
                "required": ["ticker", "gbp_amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "paper_sell",
            "description": "Close an open PAPER-ONLY simulated crypto position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "reason": {"type": "string", "default": "closed_manually"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "paper_portfolio",
            "description": "Return the paper portfolio, open positions, realised P&L and hit rate.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crypto_prices_page",
            "description": (
                "Return a paginated CoinGecko-powered crypto price page, like a broad "
                "coin-price table. Use this when the operator asks for all crypto prices, "
                "page-by-page market coverage, category filters, or coins beyond the top 1000."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "per_page": {"type": "integer", "default": 100, "maximum": 250},
                    "currency": {
                        "type": "string",
                        "enum": ["USD", "EUR", "GBP", "JPY", "BRL", "INR", "BTC", "ETH"],
                        "default": "GBP",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional CoinGecko category id such as artificial-intelligence or meme-token.",
                        "default": "",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional coin name or symbol search, such as qwen, bitcoin, or TAO.",
                        "default": "",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_dca_bot",
            "description": (
                "Run a PAPER-ONLY DCA trading bot backtest on cached OHLCV history. "
                "Use this before any bot idea, profit estimate, or strategy comparison. "
                "Returns realised/unrealised P&L, ROI, drawdown, win rate, deals, and trades. "
                "This never places live orders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "initial_cash_gbp": {"type": "number", "default": 1000.0},
                    "base_order_gbp": {"type": "number", "default": 100.0},
                    "safety_order_gbp": {"type": "number", "default": 100.0},
                    "max_safety_orders": {"type": "integer", "default": 3},
                    "safety_order_deviation_pct": {"type": "number", "default": 3.0},
                    "take_profit_pct": {"type": "number", "default": 2.0},
                    "stop_loss_pct": {"type": "number"},
                    "fee_rate": {"type": "number", "default": 0.001},
                    "slippage_pct": {"type": "number", "default": 0.05},
                    "since_ts": {"type": "integer"},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_grid_bot",
            "description": (
                "Run a PAPER-ONLY fixed-range grid trading bot backtest on cached OHLCV history. "
                "Use for sideways/ranging strategy tests. Returns realised/unrealised P&L, ROI, "
                "drawdown, closed grid trades, open grid inventory, and simulated trades. "
                "This never places live orders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "initial_cash_gbp": {"type": "number", "default": 1000.0},
                    "lower_price": {"type": "number"},
                    "upper_price": {"type": "number"},
                    "grid_count": {"type": "integer", "default": 10},
                    "order_gbp": {"type": "number", "default": 100.0},
                    "fee_rate": {"type": "number", "default": 0.001},
                    "slippage_pct": {"type": "number", "default": 0.05},
                    "since_ts": {"type": "integer"},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["ticker", "lower_price", "upper_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sweep_dca_bot",
            "description": (
                "Run a bounded PAPER-ONLY DCA parameter sweep and rank settings by ROI minus drawdown penalty. "
                "Use when the operator asks what DCA settings looked best historically. Never places live orders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "take_profit_pct_values": {"type": "array", "items": {"type": "number"}},
                    "safety_order_deviation_pct_values": {"type": "array", "items": {"type": "number"}},
                    "max_safety_orders_values": {"type": "array", "items": {"type": "integer"}},
                    "initial_cash_gbp": {"type": "number", "default": 1000.0},
                    "base_order_gbp": {"type": "number", "default": 100.0},
                    "safety_order_gbp": {"type": "number", "default": 100.0},
                    "fee_rate": {"type": "number", "default": 0.001},
                    "slippage_pct": {"type": "number", "default": 0.05},
                    "since_ts": {"type": "integer"},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_chart",
            "description": (
                "MANDATORY when the operator attaches a crypto chart "
                "screenshot AND asks for analysis ('analyse this', "
                "'what does this chart show', 'should I buy this', "
                "'is this a good entry', 'what do you think'). The "
                "tool: (1) uses vision to identify the coin + timeframe "
                "from the screenshot, (2) fetches REAL OHLCV from "
                "Kraken/CoinGecko, (3) computes EMA(20/50/200), RSI(14), "
                "ATR(14), support/resistance, (4) renders an annotated "
                "chart with marked levels, (5) writes a research note "
                "to the vault. Returns a JSON summary with the computed "
                "indicators + suggested entry/stop/take-profit zones. "
                "Crypto-only on Day-1. Default timeframe 2h."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": (
                            "Absolute filesystem path to the screenshot. "
                            "When the operator attaches an image to the "
                            "chat composer it lands at "
                            "Brain/Inbox/<timestamp> - <name> — pass "
                            "that exact path."
                        ),
                    },
                    "ticker_hint": {
                        "type": "string",
                        "description": (
                            "Optional — if you can already tell the "
                            "operator's chart is for a specific coin "
                            "(BTC, ETH, SOL, etc.) pass it here to "
                            "skip the vision-identification step."
                        ),
                        "default": "",
                    },
                    "timeframe": {
                        "type": "string",
                        "description": (
                            "Candle interval. Defaults to 2h. The vision "
                            "layer overrides this if it can read the "
                            "timeframe from the screenshot."
                        ),
                        "default": "2h",
                    },
                },
                "required": ["image_path"],
            },
        },
    },
]


# Dispatch map for the registry in tool_use.py (matches the existing
# _TOOL_DISPATCH pattern).
TOOL_DISPATCH = {
    "stock_price":      stock_price,
    "crypto_price":     crypto_price,
    "crypto_prices_page": crypto_prices_page,
    "crypto_top_100":   crypto_top_100,
    "crypto_top_1000":  crypto_top_1000,
    "paper_buy":        paper_buy,
    "paper_sell":       paper_sell,
    "paper_portfolio":  paper_portfolio,
    "backtest_dca_bot": backtest_dca_bot,
    "backtest_grid_bot": backtest_grid_bot,
    "sweep_dca_bot": sweep_dca_bot,
    "watchlist_get":    watchlist_get,
    "watchlist_add":    watchlist_add,
    "watchlist_remove": watchlist_remove,
    "analyze_chart":    analyze_chart,
}


__all__ = [
    "TOOL_SCHEMAS", "TOOL_DISPATCH",
    "stock_price", "crypto_price", "crypto_prices_page", "crypto_top_100", "crypto_top_1000",
    "watchlist_get", "watchlist_add", "watchlist_remove",
    "paper_buy", "paper_sell", "paper_portfolio",
    "backtest_dca_bot", "backtest_grid_bot", "sweep_dca_bot", "analyze_chart",
]
