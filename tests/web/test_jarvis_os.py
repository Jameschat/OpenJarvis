from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_brain_operations_center_links_to_jarvis_os():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert 'id="ob-jarvis-os-btn"' in html
    assert "window.location='/jarvis-os'" in html
    assert ">Jarvis OS<" in html


def test_brain_server_resolves_jarvis_os_static_file():
    from openjarvis.cli.brain_server import _jarvis_web_path

    assert _jarvis_web_path("jarvis-os.html") == ROOT / "jarvis_web" / "jarvis-os.html"
    assert _jarvis_web_path("jarvis-os.html").exists()


def test_brain_server_jarvis_os_route_uses_static_file_serving():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(
        encoding="utf-8"
    )

    route_start = source.index('"/jarvis-os"')
    route_block = source[route_start : route_start + 700]

    assert '"/jarvis-os.html"' in route_block
    assert 'self.path = "/jarvis-os.html"' in route_block
    assert "super().do_GET()" in route_block


def test_jarvis_os_state_shape_is_local_qwen_first():
    from openjarvis.cli.brain_server import _jarvis_os_state

    state = _jarvis_os_state()

    assert state["model"]["primary"] == "qwen3.6:27b"
    assert state["model"]["mode"] == "local-first"
    assert state["model"]["escalation"] == "Claude/Codex standby"
    assert set(state["widgets"]) >= {
        "missions",
        "agents",
        "plugins",
        "markets",
        "gpu",
        "schedule",
        "inbox",
        "memory",
    }
    assert isinstance(state["actions"], list)
    assert "New Mission" in state["actions"]


def test_brain_server_exposes_jarvis_os_state_endpoint():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(
        encoding="utf-8"
    )

    assert 'elif self.path == "/jarvis-os/state":' in source
    assert "_jarvis_os_state()" in source


def test_jarvis_os_page_contains_desktop_shell_landmarks():
    html = (ROOT / "jarvis_web" / "jarvis-os.html").read_text(encoding="utf-8")

    for marker in (
        'id="jarvis-os-root"',
        'class="desktop-shell"',
        'class="desktop-shortcuts"',
        'id="start-menu"',
        'class="taskbar"',
        'id="mission-window"',
        'id="widget-grid"',
        'fetch("/jarvis-os/state")',
    ):
        assert marker in html


def test_jarvis_os_page_declares_required_widget_targets():
    html = (ROOT / "jarvis_web" / "jarvis-os.html").read_text(encoding="utf-8")

    for widget_id in (
        "widget-model",
        "widget-missions",
        "widget-agents",
        "widget-plugins",
        "widget-markets",
        "widget-gpu",
        "widget-schedule",
        "widget-inbox",
        "widget-memory",
    ):
        assert f'id="{widget_id}"' in html


def test_jarvis_os_page_uses_safe_text_assignment_for_state():
    html = (ROOT / "jarvis_web" / "jarvis-os.html").read_text(encoding="utf-8")

    assert "function setText(id, value)" in html
    assert ".textContent = value == null ? '' : String(value)" in html
    assert "innerHTML = state" not in html


def test_jarvis_os_mobile_layout_uses_scrollable_document_flow():
    html = (ROOT / "jarvis_web" / "jarvis-os.html").read_text(encoding="utf-8")

    mobile_block = html[html.index("@media (max-width: 820px)") :]

    assert "body { overflow-y: auto; overflow-x: hidden; }" in mobile_block
    assert ".desktop-shell {\n        min-height: 100dvh;\n        display: block;" in mobile_block
    assert ".widget-grid {\n        position: static;" in mobile_block
    assert ".app-window {\n        position: static;" in mobile_block
