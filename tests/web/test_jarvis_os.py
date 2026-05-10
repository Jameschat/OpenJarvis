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
