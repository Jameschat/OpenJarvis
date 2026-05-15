from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MARKETS_HTML = ROOT / "jarvis_web" / "markets.html"


def test_forecast_strip_renderer_is_defined_when_called():
    html = MARKETS_HTML.read_text(encoding="utf-8-sig")

    assert "renderForecastStrip(" in html
    assert re.search(r"function\s+renderForecastStrip\s*\(", html)


def test_coin_price_page_ui_is_wired():
    html = MARKETS_HTML.read_text(encoding="utf-8-sig")

    assert 'data-tab="coins"' in html
    assert 'data-tab-page="coins"' in html
    assert "/markets-pro/coins?" in html
    assert "/markets-pro/coins/categories" in html
    assert 'id="coins-query"' in html
    assert re.search(r"function\s+loadCoins\s*\(", html)
    assert re.search(r"function\s+loadCoinCategories\s*\(", html)


def test_bot_lab_ui_is_wired():
    html = MARKETS_HTML.read_text(encoding="utf-8-sig")

    assert 'data-tab="botlab"' in html
    assert 'data-tab-page="botlab"' in html
    assert "/markets-pro/bot/backtest" in html
    assert re.search(r"function\s+runDcaBacktest\s*\(", html)
    assert re.search(r"function\s+renderDcaBacktest\s*\(", html)
