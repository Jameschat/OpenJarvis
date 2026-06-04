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
    assert '"/studio/runtime-health"' in source
    assert "def _studio_state(project_id" in source
    assert "qs.get(\"project_id\")" in source
    assert "qs.get(\"chat_id\")" in source
    assert "check_runtime_health" in source


def test_studio_fastapi_routes_are_registered_before_spa_fallback():
    app_source = (ROOT / "src" / "openjarvis" / "server" / "app.py").read_text(encoding="utf-8")
    routes_source = (ROOT / "src" / "openjarvis" / "server" / "studio_routes.py").read_text(encoding="utf-8")

    assert "from openjarvis.server.studio_routes import studio_router" in app_source
    assert "app.include_router(studio_router)" in app_source
    assert app_source.index("app.include_router(studio_router)") < app_source.index('@app.get("/{full_path:path}")')

    for marker in [
        '@studio_router.get("/state")',
        '@studio_router.get("/runtime-health")',
        '@studio_router.get("/chats")',
        '@studio_router.post("/chats")',
        '@studio_router.get("/runs")',
        '@studio_router.post("/runs")',
        '@studio_router.post("/runs/{run_id}/cancel")',
        '@studio_router.get("/qwen-profile")',
        '@studio_router.post("/qwen-profile")',
        '@studio_router.post("/worker-update")',
    ]:
        assert marker in routes_source


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
        "/studio/preview",
        "/studio/search",
        "/chat_events",
        "/orch_events",
        "/agent_task",
        "/schedule",
        "/vault/summary",
        "/codegraph/status",
    ]:
        assert marker in html


def test_tauri_frontend_has_studio_route_and_components():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    sidebar = (ROOT / "frontend" / "src" / "components" / "Sidebar" / "Sidebar.tsx").read_text(encoding="utf-8")
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")

    assert "StudioPage" in app
    assert 'path="studio"' in app
    assert "label: 'Studio'" in sidebar or 'label: "Studio"' in sidebar
    assert "StudioSidebar" in page
    assert "StudioThread" in page
    assert "StudioComposer" in page
    assert "StudioContextRail" in page


def test_tauri_frontend_has_studio_api_client():
    api = (ROOT / "frontend" / "src" / "lib" / "studio-api.ts").read_text(encoding="utf-8")

    for marker in [
        "fetchStudioState",
        "startStudioRun",
        "cancelStudioRun",
        "setStudioQwenProfile",
        "startStudioPreview",
        "updateStudioWorker",
        "createStudioChat",
        "archiveStudioChat",
        "deleteStudioChat",
        "searchStudio",
        "/studio/state",
        "/studio/chats",
        "/studio/search",
        "/studio/runs",
        "/studio/qwen-profile",
        "/studio/worker-update",
    ]:
        assert marker in api


def test_tauri_desktop_app_targets_jarvis_studio_runtime():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    main = (ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    tauri = (ROOT / "frontend" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    config = (ROOT / "frontend" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")

    assert "const JARVIS_PORT: u16 = 7710" in tauri
    assert "http://127.0.0.1:7710" in api
    assert "redirectTauriToStudio" in main
    assert "window.history.replaceState(null, '', '/studio')" in main
    assert "useState(true)" in app
    assert '"productName": "J.A.R.V.I.S. Studio"' in config
    assert '"title": "J.A.R.V.I.S. Studio"' in config
    assert '"createUpdaterArtifacts": false' in config


def test_tauri_frontend_has_studio_shell_styles():
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        ".studio-shell",
        ".studio-left-rail",
        ".studio-main-workspace",
        ".studio-context-rail",
        ".studio-composer",
        ".studio-card",
        ".studio-chip",
    ]:
        assert marker in css


def test_tauri_frontend_studio_exposes_core_actions():
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioComposer.tsx").read_text(encoding="utf-8")
    rail = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioContextRail.tsx").read_text(encoding="utf-8")
    sidebar = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioSidebar.tsx").read_text(encoding="utf-8")

    for marker in [
        "setStudioQwenProfile",
        "startStudioPreview",
        "updateStudioWorker",
        "cancelStudioRun",
        "createStudioChat",
        "archiveStudioChat",
        "deleteStudioChat",
        "searchStudio",
        "onProfileChange",
        "onOpenPreview",
        "onUpdateWorker",
        "onCreateChat",
        "onArchiveChat",
        "onDeleteChat",
    ]:
        assert marker in page or marker in composer or marker in rail or marker in sidebar


def test_tauri_frontend_sidebar_controls_are_wired():
    sidebar = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioSidebar.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        "onCreateChat",
        "onArchiveChat(chat.id)",
        "onDeleteChat(chat.id)",
        "onSearchQueryChange",
        "studio-chat-menu",
        "studio-sidebar-drawer",
        "pluginsOpen",
        "automationsOpen",
        "settingsOpen",
        "settingsItems",
    ]:
        assert marker in sidebar
    for marker in [
        ".studio-search-box",
        ".studio-chat-row",
        ".studio-chat-menu",
        ".studio-sidebar-drawer",
    ]:
        assert marker in css


def test_tauri_frontend_context_control_is_wired():
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioComposer.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        "contextOpen",
        "contextDraft",
        "contextItems",
        "handleAddContext",
        "[Studio attached context]",
        "onToggleContext",
        "onAddContext",
        "onRemoveContext",
        "studio-context-drawer",
        "studio-context-input",
    ]:
        assert marker in page or marker in composer or marker in css


def test_tauri_frontend_message_steering_is_wired():
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    thread = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioThread.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioComposer.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        "steeringMessageId",
        "steeringSummary",
        "handleSteerMessage",
        "branchFromMessageId",
        "onSteerMessage",
        "Steer",
        "Cancel steer",
        "studio-steer-banner",
        "studio-message-action",
    ]:
        assert marker in page or marker in thread or marker in composer or marker in css


def test_tauri_frontend_context_rail_shows_codex_style_panels():
    rail = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioContextRail.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    types = (ROOT / "frontend" / "src" / "components" / "Studio" / "types.ts").read_text(encoding="utf-8")

    for marker in [
        "file_activity",
        "File Activity",
        "Browser",
        "Sources",
        "Code Review Graph",
        "Web search",
        "copyText",
        "studio-output-row",
        "studio-file-row",
    ]:
        assert marker in rail or marker in types
    for marker in [
        ".studio-mini-action",
        ".studio-file-row .diff-add",
        ".studio-file-row .diff-del",
    ]:
        assert marker in css


def test_tauri_frontend_context_rail_shows_live_run_summary():
    rail = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioContextRail.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        "Run Summary",
        "activeRun.status",
        "activeRun.workflow",
        "activeRun.progress_summary",
        "studio-run-summary-grid",
        "studio-run-summary-note",
        "Copy run id",
    ]:
        assert marker in rail or marker in css


def test_tauri_frontend_has_native_runtime_parity_panels():
    page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    composer = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioComposer.tsx").read_text(encoding="utf-8")
    rail = (ROOT / "frontend" / "src" / "components" / "Studio" / "StudioContextRail.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    for marker in [
        "loadError",
        "Retry connection",
        "Native desktop",
        "studio-backend-banner",
        "Runtime Readiness",
        "System Health",
        "Remote Worker",
        "Qwen Lanes",
        "promotion_verdict",
        "runtime_health?.services",
        "gpu.memory_percent",
        "remoteProfileOnline",
        "Remote unavailable",
    ]:
        assert marker in page or marker in composer or marker in rail or marker in css


def test_studio_runtime_status_checks_remote_worker_host():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert 'def _studio_port_is_open(port: int, host: str = "127.0.0.1")' in source
    assert "return _port_is_open(host, port)" in source
    assert "port_checker=_studio_port_is_open" in source


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


def test_studio_has_project_preview_action():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert 'data-studio-action="open-preview"' in html
    assert "openProjectPreview" in html
    assert "/studio/preview" in html
    assert "_handle_studio_preview" in source


def test_studio_has_worker_update_action():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert 'data-studio-action="update-worker"' in html
    assert "updateWorkerNode" in html
    assert "/studio/worker-update" in html
    assert "_handle_studio_worker_update" in source
    assert 'id="studio-worker-update-button"' in html
    assert 'id="studio-worker-update-progress"' in html
    assert "setWorkerUpdateProgress" in html
    assert "worker-update-progress-fill" in html
    assert "worker-update-progress-percent" in html
    assert "workerUpdateInFlight" in html
    assert "Pulling latest Jarvis changes" in html
    assert "Running worker smoke test" in html


def test_studio_context_panel_wraps_runtime_text():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "grid-template-columns: 18px minmax(0,1fr)" in html
    assert "overflow-wrap: anywhere" in html
    assert ".source-row .row-meta" in html


def test_studio_context_panel_is_independently_scrollable():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "#studio-context-panel" in html
    assert "overflow-y: auto" in html
    assert "scrollbar-gutter: stable" in html
    assert "min-height: 0" in html


def test_studio_progress_panel_shows_ecc_lite_skills():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "renderEccLiteSkills" in html
    assert "ecc-lite-skill-list" in html
    assert "ECC skills" in html
    assert "run.ecc_lite_skills" in html


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


def test_studio_uses_codex_style_assistant_transcript_blocks():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "assistant-transcript" in html
    assert "renderAssistantContent" in html
    assert "formatAssistantMarkdown" in html
    assert 'article.className = "message jarvis assistant-transcript"' in html
    assert "assistant-code" in html


def test_studio_composer_enter_sends_shift_enter_newline():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert 'composer.addEventListener("keydown"' in html
    assert 'event.key === "Enter"' in html
    assert "!event.shiftKey" in html
    assert "event.preventDefault()" in html
    assert "sendComposer()" in html


def test_studio_composer_exposes_stop_button_for_active_runs():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert "/studio/runs/${runId}/cancel" in html
    assert "cancelActiveRun()" in html
    assert "stop-active" in html
    assert 'setAttribute("aria-label", "Stop current Jarvis task")' in html


def test_studio_sidebar_plugins_and_automations_are_expandable():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert 'id="studio-sidebar-plugins"' in html
    assert 'id="studio-sidebar-automations"' in html
    assert "renderSidebarPlugins" in html
    assert "renderSidebarAutomations" in html
    assert "toggleSidebarSection" in html
    assert 'data-section="plugins"' in html
    assert 'data-section="automations"' in html


def test_studio_has_qwen_profile_and_context_controls():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert "/studio/qwen-profile" in html
    assert 'data-profile="fast"' in html
    assert 'data-profile="quality"' in html
    assert 'data-profile="remote"' in html
    assert "qwen3.6-35b-a3b-remote" in source
    assert "setQwenProfile" in html
    assert 'id="studio-file-input"' in html
    assert "composerAttachments" in html
    assert "addFileContext" in html
    assert "addTextContext" in html
    assert "/studio/qwen-profile" in source
    assert 'profile not in {"fast", "quality", "remote"}' in source
    assert "profile must be fast, quality, or remote" in source


def test_studio_remote_profile_advertises_deep_context_policy():
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")

    assert '"context_tokens": 128000' in source
    assert '"thinking": "complex tasks only"' in source
    assert "128K context" in source


def test_studio_remote_profile_has_visible_status_and_send_guard():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")

    assert 'id="studio-remote-worker-status"' in html
    assert "remote-worker-led" in html
    assert "getRemoteWorkerLane" in html
    assert "remoteProfileCanRun" in html
    assert "Remote 35B unavailable" in html
    assert "Remote 35B worker offline" in html


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


def test_studio_has_runtime_readiness_panel():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")
    runtime_source = (
        ROOT / "src" / "openjarvis" / "tools" / "runtime_health.py"
    ).read_text(encoding="utf-8")

    for marker in [
        'id="studio-runtime-list"',
        'id="studio-runtime-count"',
        "renderRuntimePanel",
        "state.runtime_health",
        "jarvis_backend",
        "litellm_proxy",
        "qwen_fast_lane",
    ]:
        assert marker in html or marker in source or marker in runtime_source


def test_studio_has_qwen_runtime_verdict_panel():
    html = (ROOT / "jarvis_web" / "studio.html").read_text(encoding="utf-8")
    source = (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")
    runtime_source = (
        ROOT / "src" / "openjarvis" / "tools" / "qwen_runtime_status.py"
    ).read_text(encoding="utf-8")

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
        assert marker in html or marker in source or marker in runtime_source
    assert "def _qwen_runtime_status" in source
    assert "load_qwen_runtime_status" in source
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
