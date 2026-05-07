from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_tiktok_dashboard_contains_required_tabs_and_actions():
    html = (ROOT / "jarvis_web" / "tiktok.html").read_text(encoding="utf-8")

    for panel_id in (
        "tab-pipeline",
        "tab-queue",
        "tab-posted",
        "tab-finance",
        "tab-comments",
        "tab-settings",
    ):
        assert panel_id in html

    for fn in (
        "fetchState",
        "switchTab",
        "renderTrends",
        "renderAgents",
        "renderQueue",
        "renderPosted",
        "renderFinance",
        "renderComments",
        "renderSettings",
        "approveVideo",
        "rejectVideo",
        "approveComment",
        "rejectComment",
        "triggerScan",
        "saveSettings",
    ):
        assert f"function {fn}" in html or f"async function {fn}" in html

    assert "setInterval(fetchState, 5000)" in html
    assert "fetch('/tiktok/state')" in html


def test_tiktok_dashboard_escapes_external_state_before_rendering():
    html = (ROOT / "jarvis_web" / "tiktok.html").read_text(encoding="utf-8")

    assert "function escapeHtml" in html
    assert "function escapeAttr" in html
    assert "data-action=\"approve-video\"" in html
    assert "data-action=\"reject-video\"" in html
    assert "data-action=\"approve-comment\"" in html
    assert "data-action=\"reject-comment\"" in html
    assert 'onclick="previewVideo' not in html
    assert "const btn = event.target;" not in html
    assert "async function triggerScan(btn)" in html
    assert "triggerScan(this)" in html


def test_brain_operations_center_links_to_tiktok_dashboard():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert 'id="ob-tiktok-btn"' in html
    assert "window.location='/tiktok'" in html


def test_brain_server_resolves_tiktok_dashboard_file():
    from openjarvis.cli.brain_server import _jarvis_web_path

    assert _jarvis_web_path("tiktok.html") == ROOT / "jarvis_web" / "tiktok.html"
    assert _jarvis_web_path("tiktok.html").exists()


def test_brain_server_tiktok_route_uses_static_file_serving():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(
        encoding="utf-8"
    )
    tiktok_route = source[
        source.index('if path_only == "/tiktok":') : source.index(
            'elif path_only == "/tiktok/state":'
        )
    ]

    assert "self.path = \"/tiktok.html\"" in tiktok_route
    assert "super().do_GET()" in tiktok_route
    assert "self._send" not in tiktok_route
