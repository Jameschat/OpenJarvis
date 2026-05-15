import json

from openjarvis.markets.markets_tools import TOOL_DISPATCH, TOOL_SCHEMAS, crypto_prices_page


def _coin(symbol="BTC", name="Bitcoin", price=100.0):
    return {
        "id": name.lower(),
        "symbol": symbol,
        "name": name,
        "last": price,
        "change_24h_pct": 1.5,
        "change_7d_pct": 2.5,
        "change_30d_pct": 3.5,
        "market_cap": 1_000_000.0,
        "volume_24h": 50_000.0,
        "sparkline_7d": [],
        "ts": 1_700_000_000.0,
        "currency": "GBP",
        "source": "coingecko",
    }


def test_crypto_prices_page_returns_paginated_coin_universe(monkeypatch):
    def fake_fetch_markets_page(**kwargs):
        assert kwargs["page"] == 2
        assert kwargs["per_page"] == 50
        assert kwargs["vs_currency"] == "gbp"
        assert kwargs["category"] == "artificial-intelligence"
        return {
            "ok": True,
            "coins": [_coin("TAO", "Bittensor", 291.0)],
            "page": 2,
            "per_page": 50,
            "has_next": True,
            "currency": "GBP",
            "source": "coingecko",
        }

    monkeypatch.setattr("openjarvis.markets.sources.coingecko.fetch_markets_page", fake_fetch_markets_page)

    payload = crypto_prices_page(page=2, per_page=50, currency="GBP", category="artificial-intelligence")
    data = json.loads(payload)

    assert data["ok"] is True
    assert data["page"] == 2
    assert data["per_page"] == 50
    assert data["coins"][0]["symbol"] == "TAO"
    assert TOOL_DISPATCH["crypto_prices_page"] is crypto_prices_page
    assert any(schema["function"]["name"] == "crypto_prices_page" for schema in TOOL_SCHEMAS)


def test_markets_pro_coins_page_endpoint_helper(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_coins_page

    monkeypatch.setattr(
        "openjarvis.markets.sources.coingecko.fetch_markets_page",
        lambda **kwargs: {
            "ok": True,
            "coins": [_coin("SOL", "Solana", 89.0)],
            "page": kwargs["page"],
            "per_page": kwargs["per_page"],
            "has_next": False,
            "currency": kwargs["vs_currency"].upper(),
            "source": "coingecko",
        },
    )

    result = _markets_pro_coins_page({"page": ["3"], "per_page": ["25"], "currency": ["usd"]})

    assert result["ok"] is True
    assert result["page"] == 3
    assert result["per_page"] == 25
    assert result["currency"] == "USD"
    assert result["coins"][0]["symbol"] == "SOL"


def test_markets_pro_coins_categories_endpoint_helper(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_coin_categories

    monkeypatch.setattr(
        "openjarvis.markets.sources.coingecko.fetch_categories_list",
        lambda: [{"id": "artificial-intelligence", "name": "Artificial Intelligence"}],
    )

    result = _markets_pro_coin_categories()

    assert result["ok"] is True
    assert result["categories"][0]["id"] == "artificial-intelligence"


def test_crypto_prices_page_can_search_market_prices(monkeypatch):
    def fake_fetch_markets_page(**kwargs):
        assert kwargs["query"] == "qwen"
        return {
            "ok": True,
            "coins": [_coin("QWEN", "Qwen", 1.23)],
            "page": 1,
            "per_page": 100,
            "has_next": False,
            "currency": "GBP",
            "source": "coingecko",
        }

    monkeypatch.setattr("openjarvis.markets.sources.coingecko.fetch_markets_page", fake_fetch_markets_page)

    data = json.loads(crypto_prices_page(query="qwen"))

    assert data["ok"] is True
    assert data["coins"][0]["symbol"] == "QWEN"
