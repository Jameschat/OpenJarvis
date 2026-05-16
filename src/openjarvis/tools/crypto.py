"""Crypto market tool — live prices, trends, gainers/losers, and analysis."""

from __future__ import annotations

from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 15


def _get(path: str, **params: Any) -> Any:
    resp = httpx.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _format_price(coin: dict) -> str:
    """Format a single coin's market data."""
    sym = coin["symbol"].upper()
    name = coin["name"]
    price = coin["current_price"]
    ch24 = coin.get("price_change_percentage_24h") or 0
    ch7d = coin.get("price_change_percentage_7d_in_currency") or 0
    mcap = coin.get("market_cap") or 0
    vol = coin.get("total_volume") or 0
    ath = coin.get("ath") or 0
    ath_pct = coin.get("ath_change_percentage") or 0

    # Price formatting
    if price >= 1:
        p_str = f"${price:,.2f}"
    elif price >= 0.01:
        p_str = f"${price:.4f}"
    else:
        p_str = f"${price:.8f}"

    arrow_24 = "+" if ch24 >= 0 else ""
    arrow_7d = "+" if ch7d >= 0 else ""

    lines = [
        f"{sym} ({name}): {p_str}",
        f"  24h: {arrow_24}{ch24:.1f}%  7d: {arrow_7d}{ch7d:.1f}%",
        f"  Market Cap: ${mcap:,.0f}  Volume(24h): ${vol:,.0f}",
        f"  ATH: ${ath:,.2f} ({ath_pct:.0f}% from ATH)",
    ]
    return "\n".join(lines)


def _cmd_top(count: int = 10) -> str:
    """Top coins by market cap."""
    data = _get(
        "/coins/markets",
        vs_currency="usd",
        order="market_cap_desc",
        per_page=min(count, 50),
        page=1,
        sparkline="false",
        price_change_percentage="24h,7d",
    )
    lines = [f"Top {len(data)} Cryptocurrencies by Market Cap:\n"]
    for i, coin in enumerate(data, 1):
        sym = coin["symbol"].upper()
        price = coin["current_price"]
        ch24 = coin.get("price_change_percentage_24h") or 0
        mcap = coin.get("market_cap") or 0
        p_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
        arrow = "+" if ch24 >= 0 else ""
        lines.append(
            f"  {i}. {sym:>6} {p_str:>12}  24h: {arrow}{ch24:.1f}%  MCap: ${mcap:,.0f}"
        )
    return "\n".join(lines)


def _cmd_price(coin_id: str) -> str:
    """Detailed price info for a specific coin."""
    data = _get(
        "/coins/markets",
        vs_currency="usd",
        ids=coin_id.lower(),
        sparkline="false",
        price_change_percentage="1h,24h,7d,30d",
    )
    if not data:
        return f"Coin '{coin_id}' not found. Try using the CoinGecko ID (e.g. 'bitcoin', 'ethereum', 'solana')."
    return _format_price(data[0])


def _cmd_trending() -> str:
    """Trending coins on CoinGecko."""
    data = _get("/search/trending")
    coins = data.get("coins", [])
    lines = ["Trending Cryptocurrencies:\n"]
    for item in coins[:10]:
        c = item["item"]
        rank = c.get("market_cap_rank", "?")
        price = c.get("data", {}).get("price", 0)
        ch24 = c.get("data", {}).get("price_change_percentage_24h", {}).get("usd", 0)
        p_str = f"${price:.6f}" if isinstance(price, (int, float)) and price < 1 else f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)
        arrow = "+" if ch24 >= 0 else ""
        lines.append(f"  {c['symbol']} ({c['name']}) — rank #{rank} — {p_str}  24h: {arrow}{ch24:.1f}%")
    return "\n".join(lines)


def _cmd_gainers() -> str:
    """Top gainers and losers in last 24h (from top 250)."""
    data = _get(
        "/coins/markets",
        vs_currency="usd",
        order="market_cap_desc",
        per_page=250,
        page=1,
        sparkline="false",
        price_change_percentage="24h",
    )
    # Filter out stablecoins and coins without price change data
    filtered = [
        c for c in data
        if c.get("price_change_percentage_24h") is not None
        and abs(c.get("price_change_percentage_24h", 0)) > 0.1
    ]

    sorted_by_change = sorted(
        filtered, key=lambda x: x.get("price_change_percentage_24h", 0), reverse=True
    )

    lines = ["Top Gainers (24h):\n"]
    for c in sorted_by_change[:10]:
        sym = c["symbol"].upper()
        ch = c["price_change_percentage_24h"]
        price = c["current_price"]
        p_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
        lines.append(f"  {sym:>6} {p_str:>12}  +{ch:.1f}%")

    lines.append("\nTop Losers (24h):\n")
    for c in sorted_by_change[-10:]:
        sym = c["symbol"].upper()
        ch = c["price_change_percentage_24h"]
        price = c["current_price"]
        p_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
        lines.append(f"  {sym:>6} {p_str:>12}  {ch:.1f}%")

    return "\n".join(lines)


def _cmd_search(query: str) -> str:
    """Search for a coin by name or symbol."""
    data = _get("/search", query=query)
    coins = data.get("coins", [])
    if not coins:
        return f"No coins found matching '{query}'."

    lines = [f"Search results for '{query}':\n"]
    for c in coins[:10]:
        rank = c.get("market_cap_rank", "?")
        lines.append(f"  {c['symbol']} ({c['name']}) — rank #{rank} — id: {c['id']}")
    return "\n".join(lines)


def _cmd_global() -> str:
    """Global crypto market overview."""
    data = _get("/global")
    d = data["data"]
    mcap = d["total_market_cap"]["usd"]
    vol = d["total_volume"]["usd"]
    btc_dom = d["market_cap_percentage"]["btc"]
    eth_dom = d["market_cap_percentage"]["eth"]
    active = d["active_cryptocurrencies"]
    ch24 = d["market_cap_change_percentage_24h_usd"]
    arrow = "+" if ch24 >= 0 else ""

    return (
        f"Global Crypto Market:\n"
        f"  Total Market Cap: ${mcap:,.0f} ({arrow}{ch24:.1f}% 24h)\n"
        f"  24h Volume: ${vol:,.0f}\n"
        f"  BTC Dominance: {btc_dom:.1f}%\n"
        f"  ETH Dominance: {eth_dom:.1f}%\n"
        f"  Active Coins: {active:,}"
    )


@ToolRegistry.register("crypto")
class CryptoTool(BaseTool):
    """Live crypto market data, trends, gainers/losers, and coin search."""

    tool_id = "crypto"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="crypto",
            description=(
                "Get live cryptocurrency market data. Actions:\n"
                "- 'top': Top coins by market cap (default 10)\n"
                "- 'price': Detailed price for a specific coin (use CoinGecko ID like 'bitcoin', 'ethereum', 'solana')\n"
                "- 'trending': Currently trending coins\n"
                "- 'gainers': Top gainers and losers in the last 24 hours\n"
                "- 'search': Search for a coin by name or symbol\n"
                "- 'global': Overall crypto market summary\n"
                "Data from CoinGecko (free, no API key needed). "
                "Note: This is market data only, NOT investment advice."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "top",
                            "price",
                            "trending",
                            "gainers",
                            "search",
                            "global",
                        ],
                        "description": "What market data to retrieve.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "For 'price': the coin ID (e.g. 'bitcoin', 'ethereum', 'solana', 'dogecoin'). "
                            "For 'search': search term (e.g. 'pepe', 'AI token'). "
                            "For 'top': optional count (default 10)."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="finance",
            requires_confirmation=False,
            timeout_seconds=20.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "").strip()
        query = params.get("query", "").strip()

        try:
            if action == "top":
                count = 10
                if query.isdigit():
                    count = int(query)
                content = _cmd_top(count)
            elif action == "price":
                if not query:
                    return ToolResult(
                        tool_name="crypto",
                        content="Specify a coin ID (e.g. 'bitcoin', 'ethereum').",
                        success=False,
                    )
                content = _cmd_price(query)
            elif action == "trending":
                content = _cmd_trending()
            elif action == "gainers":
                content = _cmd_gainers()
            elif action == "search":
                if not query:
                    return ToolResult(
                        tool_name="crypto",
                        content="Specify a search term.",
                        success=False,
                    )
                content = _cmd_search(query)
            elif action == "global":
                content = _cmd_global()
            else:
                return ToolResult(
                    tool_name="crypto",
                    content=f"Unknown action: {action}. Use: top, price, trending, gainers, search, global.",
                    success=False,
                )

            return ToolResult(
                tool_name="crypto",
                content=content,
                success=True,
                metadata={"action": action, "query": query},
            )
        except Exception as exc:
            return ToolResult(
                tool_name="crypto",
                content=f"Crypto data fetch failed: {exc}",
                success=False,
            )


__all__ = ["CryptoTool"]
