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
        'id="studio-progress-list"',
        'id="studio-output-list"',
        'id="studio-browser-list"',
        'id="studio-source-list"',
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


def test_studio_has_live_system_health_panel():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    for marker in [
        'id="studio-system-list"',
        'id="studio-system-count"',
        "renderSystemPanel",
        "state.system",
        "gpu.util_percent",
        "gpu.memory_percent",
        "cpu_percent",
        "ram_percent",
        "sampled_at",
    ]:
        assert marker in html
    assert '"sampled_at"' in source


def test_studio_has_qwen_runtime_verdict_panel():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    for marker in [
        'id="studio-qwen-runtime-list"',
        'id="studio-qwen-runtime-count"',
        "renderQwenRuntimePanel",
        "state.qwen_runtime",
        "active_lane",
        "promotion_verdict",
        "wsl-mtp-froggeric",
        "vllm-int4-mtp",
    ]:
        assert marker in html or marker in source
    assert "def _qwen_runtime_status" in source
    assert 'state["qwen_runtime"]' in source


def test_studio_has_codex_style_work_panel():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    for marker in [
        "renderProgressPanel",
        "renderOutputsPanel",
        "renderBrowserPanel",
        "renderSourcesPanel",
        "studio-progress-count",
        "studio-output-count",
        "studio-subagent-count",
        "task_details",
        "progress_summary",
        "live_preview",
        "Code Review Graph",
        "Web search",
    ]:
        assert marker in html
    assert "enrich_runs_for_studio" in source


def test_studio_runs_endpoint_syncs_and_enriches_runs():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert "def _studio_runs_response" in source
    assert "sync_completed_run_outputs(store)" in source
    assert "enrich_runs_for_studio" in source
    assert '{"runs": _studio_runs_response(project_id, chat_id)}' in source


def test_studio_has_live_file_activity_panel():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    for marker in [
        'id="studio-file-activity-list"',
        "renderFileActivityPanel",
        "file_activity",
        "diff-add",
        "diff-del",
        "No file edits",
    ]:
        assert marker in html


def test_studio_has_qwen_patch_proposal_panel_and_apply_route():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    for marker in [
        'id="studio-patch-proposal-list"',
        "renderPatchProposalsPanel",
        "qwen_patch_proposals",
        "/studio/qwen-proposals/apply",
        "APPLY QWEN PATCH",
    ]:
        assert marker in html
    assert "list_patch_proposals" in source
    assert "_handle_studio_qwen_proposal_apply" in source


def test_studio_shows_context_pressure_meter():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert 'id="studio-context-meter"' in html
    assert "renderContextMeter" in html
    assert "context.percent" in html
    assert "handoff_recommended" in html
    assert "context.continuation" in html
    assert "Continuation chat created" in html
    assert "continuationId" in html
    assert 'context.status === "critical"' in html


def test_studio_has_chat_row_archive_delete_actions():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "toggle-chat-menu" in html
    assert "archive-chat" in html
    assert "delete-chat" in html
    assert "/archive" in html
    assert "/delete" in html


def test_studio_has_message_steering_controls():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "steer-message" in html
    assert "cancel-steer" in html
    assert "branch_from_message_id" in html
    assert "steeringState" in html
