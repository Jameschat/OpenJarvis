from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_studio_static_route_is_registered():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")
    assert '"/studio"' in source
    assert '"/studio.html"' in source
    assert 'self.path = "/studio.html"' in source


def test_studio_state_endpoint_is_registered():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")
    assert '"/studio/state"' in source
    assert "_studio_state()" in source


def test_studio_html_exists_and_wires_real_endpoints():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    for marker in [
        'id="jarvis-studio-root"',
        'id="studio-boot-screen"',
        'id="studio-boot-canvas"',
        "LOADING",
        'id="studio-thread"',
        'id="studio-composer"',
        'id="studio-agent-list"',
        'id="studio-context-panel"',
        "/studio/state",
        "/studio/projects",
        "/studio/chats",
        "/studio/runs",
        "/studio/search",
        "/chat_events",
        "/orch_events",
        "/agent_task",
        "/schedule",
        "/vault/summary",
        "/codegraph/status",
    ]:
        assert marker in html


def test_studio_buttons_are_not_inert():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    assert "closest('[data-studio-action]')" in html
    assert "document.addEventListener('click'" in html
    for line in html.splitlines():
        if "<button" in line:
            assert any(
                token in line
                for token in (
                    "data-studio-action",
                    "data-studio-page",
                    "data-studio-tab",
                    "id=",
                )
            ), line


def test_studio_has_boot_screen_that_fades_after_state_load():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    assert "drawBootRain" in html
    assert "hideBootScreen" in html
    assert "studio-boot-screen.hidden" in html
    assert "loadStudioState" in html


def test_studio_messages_render_timestamps():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "function formatStudioTime" in html
    assert "message-time" in html
    assert "message.created_at" in html
    assert "run.created_at" in html


def test_studio_polls_while_runs_are_active():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "scheduleStudioRefresh" in html
    assert "hasActiveRuns" in html
    assert "setTimeout(scheduleStudioRefresh" in html


def test_studio_has_typing_thinking_and_agent_colours():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "typeJarvisBubble" in html
    assert "thinking-indicator" in html
    assert "Jarvis is thinking" in html
    assert "agentColourClass" in html
    assert "agent-qwen" in html
    assert "agent-codex" in html
    assert "agent-claude" in html


def test_studio_has_qwen_profile_and_context_controls():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert "/studio/qwen-profile" in html
    assert 'data-profile="fast"' in html
    assert 'data-profile="quality"' in html
    assert "setQwenProfile" in html
    assert 'id="studio-file-input"' in html
    assert "composerAttachments" in html
    assert "addFileContext" in html
    assert "addTextContext" in html
    assert "/studio/qwen-profile" in source
