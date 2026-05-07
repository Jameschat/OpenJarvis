from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MARKETS_HTML = ROOT / "jarvis_web" / "markets.html"


def test_forecast_strip_renderer_is_defined_when_called():
    html = MARKETS_HTML.read_text(encoding="utf-8-sig")

    assert "renderForecastStrip(" in html
    assert re.search(r"function\s+renderForecastStrip\s*\(", html)
