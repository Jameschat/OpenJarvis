from pathlib import Path
import re
import shutil
import subprocess


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
    assert "Grid Bot Backtest" in html
    assert re.search(r"function\s+runGridBacktest\s*\(", html)
    assert re.search(r"function\s+renderGridBacktest\s*\(", html)
    assert "DCA Parameter Sweep" in html
    assert re.search(r"function\s+runDcaSweep\s*\(", html)
    assert re.search(r"function\s+renderDcaSweep\s*\(", html)
    assert "Grid Parameter Sweep" in html
    assert re.search(r"function\s+runGridSweep\s*\(", html)
    assert re.search(r"function\s+renderGridSweep\s*\(", html)
    assert "Signal Webhook Simulation" in html
    assert re.search(r"function\s+runSignalBacktest\s*\(", html)
    assert re.search(r"function\s+renderSignalBacktest\s*\(", html)
    assert "Paper Bot Scheduler" in html
    assert "/markets-pro/bot/schedule" in html
    assert "/markets-pro/bot/schedules" in html
    assert "/markets-pro/bot/run-due" in html
    assert "/markets-pro/bot/approve-execution" in html
    assert re.search(r"function\s+schedulePaperBot\s*\(", html)
    assert re.search(r"function\s+loadPaperBotSchedules\s*\(", html)
    assert re.search(r"function\s+runDuePaperBots\s*\(", html)
    assert re.search(r"function\s+approvePaperBotExecution\s*\(", html)


def test_bot_lab_fields_use_dark_glass_styling():
    html = MARKETS_HTML.read_text(encoding="utf-8-sig")

    assert ".field {" in html
    assert "linear-gradient(180deg, rgba(20,64,104,0.45), rgba(9,28,52,0.78))" in html
    assert "border-radius: 12px" in html
    assert "select.field option" in html
    assert "background: #0b2340" in html
    assert ".bot-form .field" in html


def test_markets_page_inline_javascript_has_valid_syntax(tmp_path):
    node = shutil.which("node")
    if not node:
        return
    html = MARKETS_HTML.read_text(encoding="utf-8-sig")
    scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, flags=re.IGNORECASE)

    assert scripts
    for index, script in enumerate(scripts, start=1):
        script_path = tmp_path / f"markets-script-{index}.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
