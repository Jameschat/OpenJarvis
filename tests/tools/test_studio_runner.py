import json
from pathlib import Path

from openjarvis.tools import agent_runner, studio_runner
from openjarvis.tools.studio_store import StudioStore


def test_start_run_records_context_workflow_and_task(monkeypatch, tmp_path):
    created_tasks = []
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path)
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"markdown": "ctx", "warnings": []},
    )
    monkeypatch.setattr(
        studio_runner.studio_workflows,
        "select_workflow",
        lambda prompt: {
            "workflow": "execute",
            "reason": "direct",
            "verification": {"required": True},
            "model": "qwen3.6-27b-local",
            "requires_operator_approval": False,
            "risks": [],
            "next_steps": [],
        },
    )
    monkeypatch.setattr(
        studio_runner,
        "_queue_agent_task",
        lambda **kwargs: created_tasks.append(kwargs) or "task-1",
    )

    result = studio_runner.start_studio_run(
        project_id="openjarvis",
        chat_id="chat-1",
        prompt="Build thing",
    )

    assert result["run"]["status"] == "running"
    assert created_tasks[0]["agent_id"] == "qwen-planner"
    assert created_tasks[0]["prompt"].startswith("ctx")
    assert "Operator request:\nBuild thing" in created_tasks[0]["prompt"]
    assert [e["type"] for e in result["run"]["events"]][:3] == [
        "run.created",
        "run.context_built",
        "run.workflow_selected",
    ]


def test_start_run_blocks_when_approval_required(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path)
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"markdown": "ctx", "warnings": []},
    )
    monkeypatch.setattr(
        studio_runner.studio_workflows,
        "select_workflow",
        lambda prompt: {
            "workflow": "spec",
            "reason": "large",
            "verification": {"required": True},
            "model": "qwen3.6-27b-local",
            "requires_operator_approval": True,
            "risks": ["large"],
            "next_steps": [],
        },
    )

    result = studio_runner.start_studio_run(
        project_id="openjarvis",
        chat_id="chat-1",
        prompt="Build full platform",
    )

    assert result["run"]["status"] == "blocked"
    assert any(e["type"] == "run.blocked" for e in result["run"]["events"])


def test_start_run_answers_greeting_without_queueing_agent(monkeypatch, tmp_path):
    created_tasks = []
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path)
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"markdown": "ctx", "warnings": []},
    )
    monkeypatch.setattr(
        studio_runner,
        "_queue_agent_task",
        lambda **kwargs: created_tasks.append(kwargs) or "task-1",
    )

    result = studio_runner.start_studio_run(
        project_id="openjarvis",
        chat_id="chat-1",
        prompt="evening jarvis, how are you",
    )

    assert result["run"]["status"] == "completed"
    assert result["reply"]
    assert created_tasks == []
    assert any(e["type"] == "run.completed" for e in result["run"]["events"])


def test_start_run_answers_model_status_without_queueing_agent(monkeypatch, tmp_path):
    created_tasks = []
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path)
    monkeypatch.setattr(
        studio_runner,
        "_queue_agent_task",
        lambda **kwargs: created_tasks.append(kwargs) or "task-1",
    )

    result = studio_runner.start_studio_run(
        project_id="openjarvis",
        chat_id="chat-1",
        prompt="what qwen model are you running jarvis?",
    )

    assert result["run"]["status"] == "completed"
    assert "qwen3.6-27b-local" in result["reply"]
    assert created_tasks == []


def test_start_run_answers_memory_question_from_context_without_queueing_agent(monkeypatch, tmp_path):
    created_tasks = []
    brain = tmp_path / "brain"
    note = brain / "Projects" / "Networx.md"
    note.parent.mkdir(parents=True)
    note.write_text("Networx Ltd website was built in Claude Code and Codex.", encoding="utf-8")
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path)
    from openjarvis.tools import obsidian_brain

    monkeypatch.setattr(obsidian_brain, "BRAIN_ROOT", brain)
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: (_ for _ in ()).throw(AssertionError("full context should not be built")),
    )
    monkeypatch.setattr(
        studio_runner.studio_workflows,
        "select_workflow",
        lambda prompt: {
            "workflow": "execute",
            "reason": "direct",
            "verification": {"required": True},
            "model": "qwen3.6-27b-local",
            "requires_operator_approval": False,
            "risks": [],
            "next_steps": [],
        },
    )
    monkeypatch.setattr(
        studio_runner.studio_research,
        "should_prefetch_research",
        lambda prompt: False,
    )
    monkeypatch.setattr(
        studio_runner,
        "_queue_agent_task",
        lambda **kwargs: created_tasks.append(kwargs) or "task-1",
    )

    result = studio_runner.start_studio_run(
        project_id="openjarvis",
        chat_id="chat-1",
        prompt="from memory, what do you know about the Networx Ltd website?",
    )

    assert result["run"]["status"] == "completed"
    assert "Networx" in result["reply"]
    assert "Claude Code and Codex" in result["reply"]
    assert "local vault" in result["reply"]
    assert created_tasks == []


def test_record_verification_evidence_updates_run(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path)
    store = studio_runner.studio_store.StudioStore(tmp_path)
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Verify", workflow="verify")

    updated = studio_runner.record_verification_evidence(
        run["id"],
        kind="pytest",
        status="pass",
        summary="3 passed",
    )

    assert updated["evidence"][0]["kind"] == "pytest"
    assert any(
        e["type"] == "run.verification_evidence_recorded"
        for e in updated["events"]
    )


def test_sync_completed_run_outputs_appends_agent_result(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Long task", workflow="execute")
    run["status"] = "running"
    run["tasks"] = ["task-123"]
    store._write_json(store._run_path(run["id"]), run)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "task-123.RESULT.md").write_text("Finished result.", encoding="utf-8")
    agent_state = {
        "tasks": [
            {
                "id": "task-123",
                "status": "done",
                "workspace": str(workspace),
                "exit_code": 0,
            }
        ]
    }
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps(agent_state), encoding="utf-8")
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)

    studio_runner.sync_completed_run_outputs(store)

    updated = store.get_run(run["id"])
    chat = store.get_chat(chat["id"])
    assert updated["status"] == "completed"
    assert any(e["type"] == "run.completed" for e in updated["events"])
    assert any("Finished result." in m["content"] for m in chat["messages"])


def test_enrich_runs_for_studio_includes_task_outputs(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "task-123.RESULT.md").write_text("Finished result.", encoding="utf-8")
    (workspace / "QWEN_TOOL_RESULTS.json").write_text("[]", encoding="utf-8")
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-123",
                        "title": "Studio: test",
                        "agent_id": "qwen-planner",
                        "status": "done",
                        "workspace": str(workspace),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)

    enriched = studio_runner.enrich_runs_for_studio(
        [{"id": "run-1", "status": "completed", "tasks": ["task-123"]}]
    )

    assert enriched[0]["task_details"][0]["agent_id"] == "qwen-planner"
    assert enriched[0]["outputs"][0]["name"] == "task-123.RESULT.md"
    assert any(output["name"] == "QWEN_TOOL_RESULTS.json" for output in enriched[0]["outputs"])


def test_enrich_runs_for_studio_surfaces_qwen_tool_artifacts(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    proposal_path = tmp_path / "proposal.json"
    screenshot_path = tmp_path / "studio.png"
    (workspace / "QWEN_TOOL_RESULTS.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "tool": "repo_patch_proposal",
                        "ok": True,
                        "proposal_id": "qwen-proposal-1",
                        "proposal_path": str(proposal_path),
                        "changed_files": ["src/app.py"],
                        "apply_requires_approval": True,
                    },
                    {
                        "tool": "browser_visual_check",
                        "ok": True,
                        "screenshot_path": str(screenshot_path),
                        "title": "Jarvis Studio",
                        "url": "http://localhost:7710/studio",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-123",
                        "title": "Studio: test",
                        "agent_id": "qwen-planner",
                        "status": "done",
                        "workspace": str(workspace),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)

    enriched = studio_runner.enrich_runs_for_studio(
        [{"id": "run-1", "status": "completed", "tasks": ["task-123"]}]
    )

    outputs = enriched[0]["outputs"]
    proposal = next(output for output in outputs if output["kind"] == "proposal")
    screenshot = next(output for output in outputs if output["kind"] == "screenshot")
    assert proposal["name"] == "Qwen proposal: src/app.py"
    assert proposal["proposal_id"] == "qwen-proposal-1"
    assert proposal["path"] == str(proposal_path)
    assert screenshot["name"] == "Visual check: Jarvis Studio"
    assert screenshot["path"] == str(screenshot_path)


def test_enrich_runs_for_studio_includes_live_task_progress(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "stdout.log").write_text("Step 1 complete\nPlanning edit proposal\n", encoding="utf-8")
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-live",
                        "title": "Studio: live",
                        "agent_id": "qwen-planner",
                        "status": "running",
                        "workspace": str(workspace),
                        "started_at": 1000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)
    monkeypatch.setattr(studio_runner.time, "time", lambda: 1065)

    enriched = studio_runner.enrich_runs_for_studio(
        [{"id": "run-1", "status": "running", "tasks": ["task-live"]}]
    )

    task = enriched[0]["task_details"][0]
    assert task["elapsed_seconds"] == 65
    assert task["progress_summary"] == "qwen-planner running for 65s"
    assert "Planning edit proposal" in task["live_preview"]
    assert enriched[0]["progress_summary"] == "qwen-planner running for 65s"


def test_subtract_file_activity_hides_baseline_and_secrets():
    current = [
        {"path": "uv.lock", "additions": 12, "deletions": 2},
        {"path": "jarvis_web/studio.html", "additions": 40, "deletions": 5},
        {"path": "jarvis.bat", "additions": 99, "deletions": 1},
    ]
    baseline = [{"path": "uv.lock", "additions": 12, "deletions": 2}]

    activity = studio_runner._subtract_file_activity(current, baseline)

    assert activity == [
        {
            "path": "jarvis_web/studio.html",
            "name": "studio.html",
            "additions": 40,
            "deletions": 5,
            "status": "editing",
        }
    ]


def test_sync_marks_stale_studio_runs_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(studio_runner, "STUDIO_RUN_STALE_AFTER_SECONDS", 60)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Long task", workflow="execute")
    run["status"] = "running"
    run["tasks"] = ["task-stuck"]
    run["updated_at"] = "2026-05-26T05:00:00+00:00"
    store._write_json(store._run_path(run["id"]), run)

    agent_state = {
        "tasks": [
            {
                "id": "task-stuck",
                "status": "running",
                "workspace": "",
                "started_at": 1779771600,
            }
        ]
    }
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps(agent_state), encoding="utf-8")
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)
    monkeypatch.setattr(studio_runner.time, "time", lambda: 1779771901)

    studio_runner.sync_completed_run_outputs(store)

    updated = store.get_run(run["id"])
    chat = store.get_chat(chat["id"])
    assert updated["status"] == "failed"
    assert any(e["type"] == "run.timeout" for e in updated["events"])
    assert any("timed out" in m["content"].lower() for m in chat["messages"])


def test_sync_timeout_marks_agent_task_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(studio_runner, "STUDIO_RUN_STALE_AFTER_SECONDS", 60)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Long task", workflow="execute")
    run["status"] = "running"
    run["tasks"] = ["task-stuck"]
    store._write_json(store._run_path(run["id"]), run)

    monkeypatch.setattr(
        studio_runner,
        "_load_agent_task_index",
        lambda: {"task-stuck": {"id": "task-stuck", "status": "running", "started_at": 1779771600}},
    )
    monkeypatch.setattr(studio_runner.time, "time", lambda: 1779771901)
    marked = []

    class FakeRegistry:
        def mark_finished(self, task_id, exit_code, error=None):
            marked.append((task_id, exit_code, error))

    monkeypatch.setattr(studio_runner, "_agent_registry", lambda: FakeRegistry())

    studio_runner.sync_completed_run_outputs(store)

    assert marked == [("task-stuck", -1, "Studio run timed out after 60s.")]


def test_sync_marks_orphaned_studio_runs_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(studio_runner, "STUDIO_RUN_STALE_AFTER_SECONDS", 60)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Never started", workflow="execute")
    run["status"] = "running"
    run["tasks"] = ["task-missing"]
    run["updated_at"] = "2026-05-26T05:00:00+00:00"
    store._write_json(store._run_path(run["id"]), run)

    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)
    monkeypatch.setattr(studio_runner.time, "time", lambda: 1779771901)

    studio_runner.sync_completed_run_outputs(store)

    updated = store.get_run(run["id"])
    chat = store.get_chat(chat["id"])
    assert updated["status"] == "failed"
    assert any(e["type"] == "run.timeout" for e in updated["events"])
    assert any("timed out" in m["content"].lower() for m in chat["messages"])


def test_sync_marks_never_started_studio_task_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(studio_runner, "STUDIO_RUN_STALE_AFTER_SECONDS", 60)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Queued forever", workflow="execute")
    run["status"] = "queued"
    run["tasks"] = ["task-queued"]
    run["updated_at"] = "2026-05-26T05:00:00+00:00"
    store._write_json(store._run_path(run["id"]), run)

    agent_state = {"tasks": [{"id": "task-queued", "status": "queued", "workspace": ""}]}
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps(agent_state), encoding="utf-8")
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)
    monkeypatch.setattr(studio_runner.time, "time", lambda: 1779771901)

    studio_runner.sync_completed_run_outputs(store)

    updated = store.get_run(run["id"])
    assert updated["status"] == "failed"
    assert any(e["type"] == "run.timeout" for e in updated["events"])


def test_sync_marks_stale_run_without_tasks_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(studio_runner, "STUDIO_RUN_STALE_AFTER_SECONDS", 60)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "No task spawned", workflow="execute")
    run["status"] = "running"
    run["tasks"] = []
    run["updated_at"] = "2026-05-26T05:00:00+00:00"
    store._write_json(store._run_path(run["id"]), run)

    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)
    monkeypatch.setattr(studio_runner.time, "time", lambda: 1779771901)

    studio_runner.sync_completed_run_outputs(store)

    updated = store.get_run(run["id"])
    chat = store.get_chat(chat["id"])
    assert updated["status"] == "failed"
    assert any(e["type"] == "run.timeout" for e in updated["events"])
    assert any("timed out" in m["content"].lower() for m in chat["messages"])


def test_sync_failed_run_reports_agent_error(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Chat")
    run = store.create_run("openjarvis", chat["id"], "Long task", workflow="execute")
    run["status"] = "running"
    run["tasks"] = ["task-failed"]
    store._write_json(store._run_path(run["id"]), run)

    agent_state = {
        "tasks": [
            {
                "id": "task-failed",
                "status": "failed",
                "workspace": "",
                "error": "qwen agent failed: timed out",
            }
        ]
    }
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps(agent_state), encoding="utf-8")
    from openjarvis.tools import agent_runner

    monkeypatch.setattr(agent_runner, "STATE_FILE", state_path)

    studio_runner.sync_completed_run_outputs(store)

    chat = store.get_chat(chat["id"])
    assert any("qwen agent failed: timed out" in m["content"] for m in chat["messages"])


def test_studio_store_reads_utf8_bom_json(tmp_path):
    store = StudioStore(tmp_path)
    path = store.projects_dir / "bom.json"
    path.write_text('\ufeff{"id":"bom","title":"BOM"}', encoding="utf-8")

    assert store._read_json(path, {})["id"] == "bom"
    assert not list(store.corrupt_dir.glob("bom-*.json"))


def test_enrich_chats_adds_context_character_pressure(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Long chat")
    store.add_message(chat["id"], "operator", "a" * 80)
    chat = store.get_chat(chat["id"])

    enriched = studio_runner.enrich_chats_with_context([chat], char_limit=100)

    context = enriched[0]["context"]
    assert context["used_chars"] == 80
    assert context["limit_chars"] == 100
    assert context["percent"] == 80
    assert context["status"] == "warning"
    assert context["handoff_recommended"] is False


def test_context_handoff_writes_vault_note_once(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    brain = tmp_path / "brain"
    monkeypatch.setattr(studio_runner, "BRAIN_ROOT", brain)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Deep project")
    store.add_message(chat["id"], "operator", "Plan the project.")
    store.add_message(chat["id"], "jarvis", "Assumptions, decisions, and next actions.")
    chat = store.get_chat(chat["id"])

    enriched = studio_runner.enrich_chats_with_context([chat], store=store, char_limit=40)

    context = enriched[0]["context"]
    assert context["status"] == "critical"
    assert context["handoff_recommended"] is True
    handoff = context["handoff"]
    assert handoff["path"]
    note = Path(handoff["path"])
    assert note.exists()
    assert "Jarvis Studio Context Handoff" in note.read_text(encoding="utf-8")
    assert store.get_chat(chat["id"])["context_handoff"]["path"] == str(note)

    enriched_again = studio_runner.enrich_chats_with_context([store.get_chat(chat["id"])], store=store, char_limit=40)
    assert enriched_again[0]["context"]["handoff"]["path"] == str(note)
    assert len(list((brain / "Sessions").glob("*.md"))) == 1


def test_critical_context_creates_continuation_chat_once(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    brain = tmp_path / "brain"
    monkeypatch.setattr(studio_runner, "BRAIN_ROOT", brain)
    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="Long build")
    store.add_message(chat["id"], "operator", "a" * 100)
    chat = store.get_chat(chat["id"])

    enriched = studio_runner.enrich_chats_with_context([chat], store=store, char_limit=100)

    context = enriched[0]["context"]
    continuation = context["continuation"]
    assert context["status"] == "critical"
    assert continuation["chat_id"]
    continuation_chat = store.get_chat(continuation["chat_id"])
    assert continuation_chat["continuation"]["source_chat_id"] == chat["id"]
    assert "Jarvis Studio Context Handoff" in continuation_chat["messages"][0]["content"]

    enriched_again = studio_runner.enrich_chats_with_context([store.get_chat(chat["id"])], store=store, char_limit=100)
    assert enriched_again[0]["context"]["continuation"]["chat_id"] == continuation["chat_id"]
    assert len([c for c in store.list_chats("openjarvis") if c.get("continuation")]) == 1


def test_project_repo_root_resolves_vault_project_path(monkeypatch, tmp_path):
    """A Studio project whose vault PROJECT.md declares a `path:` resolves its
    repo root to that working dir, so Qwen file tools target the right folder."""
    from openjarvis.tools import obsidian_brain

    brain = tmp_path / "brain"
    site = tmp_path / "westhill-hotel"
    site.mkdir()
    project_md = brain / "Projects" / "westhill-hotel" / "PROJECT.md"
    project_md.parent.mkdir(parents=True)
    project_md.write_text(
        f"---\nslug: westhill-hotel\npath: {site}\n---\n# Westhill\n", encoding="utf-8"
    )
    monkeypatch.setattr(obsidian_brain, "BRAIN_ROOT", brain)

    root = studio_runner._project_repo_root(
        {"id": "westhill-hotel", "vault_project": "westhill-hotel",
         "repo_root": str(studio_runner.studio_store.DEFAULT_REPO_ROOT)}
    )
    assert root.resolve() == site.resolve()


def test_project_repo_root_falls_back_to_default(monkeypatch, tmp_path):
    from openjarvis.tools import obsidian_brain

    monkeypatch.setattr(obsidian_brain, "BRAIN_ROOT", tmp_path / "brain")
    root = studio_runner._project_repo_root(
        {"id": "openjarvis", "vault_project": "OpenJarvis",
         "repo_root": str(studio_runner.studio_store.DEFAULT_REPO_ROOT)}
    )
    assert root.resolve() == studio_runner.studio_store.DEFAULT_REPO_ROOT.resolve()


def test_start_studio_run_routes_named_vault_project(monkeypatch, tmp_path):
    from openjarvis.tools import obsidian_brain

    brain = tmp_path / "brain"
    site = tmp_path / "westhill-hotel"
    site.mkdir()
    project_md = brain / "Projects" / "westhill-hotel" / "PROJECT.md"
    project_md.parent.mkdir(parents=True)
    project_md.write_text(
        f"---\nslug: westhill-hotel\npath: {site}\n---\n"
        "# PROJECT.md - Westhill Country Hotel Website\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(obsidian_brain, "BRAIN_ROOT", brain)
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"ok": True, "markdown": "Westhill context", "warnings": []},
    )
    monkeypatch.setattr(
        studio_runner.studio_workflows,
        "select_workflow",
        lambda prompt: {
            "workflow": "execute",
            "reason": "Single direct task with normal verification.",
            "requires_operator_approval": False,
            "verification": {"required": True},
            "risks": [],
        },
    )
    queued = {}

    def fake_queue(**kwargs):
        queued.update(kwargs)
        return "task-westhill"

    monkeypatch.setattr(studio_runner, "_queue_agent_task", fake_queue)

    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat("openjarvis", title="New chat")

    result = studio_runner.start_studio_run(
        "openjarvis",
        chat["id"],
        "can we continue with the westhill country hotel website, modernising it",
    )

    assert result["run"]["project_id"] == "westhill-hotel"
    assert queued["project_id"] == "studio-westhill-hotel"
    assert Path(queued["repo_root"]).resolve() == site.resolve()


def test_start_studio_run_answers_project_continuation_without_queueing(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {
            "ok": True,
            "warnings": [],
            "markdown": "\n".join(
                [
                    "## Active project",
                    "Project: westhill-hotel",
                    "",
                    "### STATE.md",
                    "## Where we left off",
                    "Phase 1 is complete. The homepage and Jersey pages are built.",
                    "The site has not yet been deployed to Netlify.",
                    "",
                    "## Current known issues / open items",
                    "- **Events enquiry form** — needs Netlify Forms.",
                    "- **Newsletter form** — footer subscribe input is UI-only.",
                    "",
                    "### ROADMAP.md",
                    "**Current phase:** Phase 2 — Page completion & polish",
                ]
            ),
        },
    )
    queued = []
    monkeypatch.setattr(studio_runner, "_queue_agent_task", lambda **kwargs: queued.append(kwargs) or "task")

    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("westhill-hotel", title="Westhill Country Hotel", repo_root=str(tmp_path))
    chat = store.create_chat("westhill-hotel", title="New chat")

    result = studio_runner.start_studio_run(
        "westhill-hotel",
        chat["id"],
        "can we continue with the westhill country hotel website, modernising it",
    )

    assert result["run"]["status"] == "completed"
    assert result["decision"]["workflow"] == "project_continuation"
    assert "Where we left off" in result["reply"]
    assert "Build the dedicated dining page" in result["reply"]
    assert queued == []


def test_start_studio_run_answers_new_project_platform_brief_without_queueing(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"ok": True, "markdown": "", "warnings": []},
    )
    queued = []
    monkeypatch.setattr(studio_runner, "_queue_agent_task", lambda **kwargs: queued.append(kwargs) or "task")

    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis", repo_root=str(tmp_path))
    chat = store.create_chat("openjarvis", title="New chat")

    result = studio_runner.start_studio_run(
        "openjarvis",
        chat["id"],
        (
            "new project - localised platform, that can import all my emails, categorize them "
            "into client name, store attached files into a files folder under that clients name, "
            "it can be a html based portal, but it also needs to be secure"
        ),
    )

    assert result["run"]["status"] == "completed"
    assert result["decision"]["workflow"] == "new_project_brief"
    assert "Local Email Client Portal" in result["reply"]
    assert "Security baseline" in result["reply"]
    assert "IMAP/OAuth email import" in result["reply"]
    assert queued == []


def test_cancel_studio_run_marks_running_run_and_tasks_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    cancelled_running = []
    cancelled_todo = []
    monkeypatch.setattr(agent_runner, "cancel_running_task", lambda task_id: cancelled_running.append(task_id) or False)
    monkeypatch.setattr(agent_runner, "cancel_task", lambda task_id: cancelled_todo.append(task_id) or True)

    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis", repo_root=str(tmp_path))
    chat = store.create_chat("openjarvis", title="New chat")
    run = store.create_run("openjarvis", chat["id"], "Long Qwen task", workflow="execute")
    run["status"] = "running"
    run["tasks"] = ["task-live"]
    store._write_json(store._run_path(run["id"]), run)

    result = studio_runner.cancel_studio_run(run["id"])
    chat_after = store.get_chat(chat["id"])

    assert result["run"]["status"] == "cancelled"
    assert result["run"]["cancelled"] is True
    assert cancelled_running == ["task-live"]
    assert cancelled_todo == ["task-live"]
    assert "Cancelled the running Studio task" in chat_after["messages"][-1]["content"]


def test_sync_completed_outputs_does_not_overwrite_cancelled_run(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(
        studio_runner,
        "_load_agent_task_index",
        lambda: {"task-live": {"id": "task-live", "status": "done", "agent_id": "qwen-planner"}},
    )

    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("openjarvis", title="OpenJarvis", repo_root=str(tmp_path))
    chat = store.create_chat("openjarvis", title="New chat")
    run = store.create_run("openjarvis", chat["id"], "Long Qwen task", workflow="execute")
    run["status"] = "cancelled"
    run["cancelled"] = True
    run["tasks"] = ["task-live"]
    store._write_json(store._run_path(run["id"]), run)

    synced = studio_runner.sync_completed_run_outputs(store)
    updated = store.get_run(run["id"])

    assert synced == 0
    assert updated["status"] == "cancelled"


def test_continue_phase_one_after_email_portal_brief_creates_project_scaffold(monkeypatch, tmp_path):
    studio_root = tmp_path / "studio"
    projects_root = tmp_path / "projects"
    brain_root = tmp_path / "Brain"
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", studio_root)
    monkeypatch.setattr(studio_runner, "BRAIN_ROOT", brain_root)
    monkeypatch.setenv("OPENJARVIS_PROJECTS_ROOT", str(projects_root))
    queued = []
    monkeypatch.setattr(studio_runner, "_queue_agent_task", lambda **kwargs: queued.append(kwargs) or "task")

    store = studio_runner.studio_store.StudioStore(studio_root)
    store.ensure_project("openjarvis", title="OpenJarvis", repo_root=str(tmp_path))
    chat = store.create_chat("openjarvis", title="Local email portal")
    store.add_message(chat["id"], "jarvis", "Yes. Start this as **Local Email Client Portal**.")

    result = studio_runner.start_studio_run("openjarvis", chat["id"], "continue phase 1")

    project_dir = projects_root / "local-email-client-portal"
    vault_dir = brain_root / "Projects" / "local-email-client-portal"
    assert result["run"]["status"] == "completed"
    assert result["decision"]["workflow"] == "phase1_project_scaffold"
    assert (project_dir / "README.md").exists()
    assert (project_dir / "docs" / "SECURITY.md").exists()
    assert (vault_dir / "PROJECT.md").exists()
    assert (vault_dir / "REQUIREMENTS.md").exists()
    assert (vault_dir / "ROADMAP.md").exists()
    assert (vault_dir / "STATE.md").exists()
    assert (vault_dir / "CONTEXT.md").exists()
    assert "Phase 1 scaffold is created" in result["reply"]
    assert queued == []


def test_website_preview_request_starts_project_preview_without_qwen(monkeypatch, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>Preview</h1>", encoding="utf-8")
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", tmp_path / "studio")
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"ok": True, "markdown": "", "warnings": []},
    )
    monkeypatch.setattr(
        studio_runner.project_preview,
        "start_project_preview",
        lambda repo_root: {"ok": True, "url": "http://127.0.0.1:8128/", "repo_root": str(repo_root)},
    )
    queued = []
    monkeypatch.setattr(studio_runner, "_queue_agent_task", lambda **kwargs: queued.append(kwargs) or "task")

    store = studio_runner.studio_store.StudioStore(tmp_path / "studio")
    store.ensure_project("westhill-hotel", title="Westhill", repo_root=str(site))
    chat = store.create_chat("westhill-hotel", title="New chat")

    result = studio_runner.start_studio_run(
        "westhill-hotel",
        chat["id"],
        "can you show me the preview of the website as it currently is built",
    )

    assert result["run"]["status"] == "completed"
    assert result["decision"]["workflow"] == "project_preview"
    assert "http://127.0.0.1:8128/" in result["reply"]
    assert queued == []


def test_common_studio_request_matrix_routes_without_hanging(monkeypatch, tmp_path):
    studio_root = tmp_path / "studio"
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "index.html").write_text("<h1>Site</h1>", encoding="utf-8")
    monkeypatch.setattr(studio_runner.studio_store, "STUDIO_ROOT", studio_root)
    monkeypatch.setattr(studio_runner, "BRAIN_ROOT", tmp_path / "Brain")
    monkeypatch.setenv("OPENJARVIS_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(
        studio_runner.studio_context,
        "build_project_context_pack",
        lambda prompt, project=None: {"ok": True, "markdown": "Project context", "warnings": []},
    )
    monkeypatch.setattr(
        studio_runner.project_preview,
        "start_project_preview",
        lambda repo_root: {"ok": True, "url": "http://127.0.0.1:8128/", "repo_root": str(repo_root)},
    )
    queued: list[dict] = []
    monkeypatch.setattr(studio_runner, "_queue_agent_task", lambda **kwargs: queued.append(kwargs) or f"task-{len(queued)}")

    store = studio_runner.studio_store.StudioStore(studio_root)
    store.ensure_project("openjarvis", title="OpenJarvis", repo_root=str(project_root))
    chat = store.create_chat("openjarvis", title="Matrix")
    store.add_message(chat["id"], "jarvis", "Yes. Start this as **Local Email Client Portal**.")

    cases = [
        ("morning jarvis", "completed", "direct_chat"),
        ("what qwen model are you running jarvis?", "completed", "direct_chat"),
        (
            "new project - localised platform that imports emails, categorizes by client, stores attachments, html portal, secure",
            "completed",
            "new_project_brief",
        ),
        ("continue phase 1", "completed", "phase1_project_scaffold"),
        ("can you show me the preview of the website", "completed", "project_preview"),
        ("Fix the DCA backtest HTTP 500 and add a regression test", "running", "debug"),
        ("Research the best tools for local Qwen agent memory", "running", "qwen_workflow"),
        ("Create a dedicated dining page for this website", "running", "execute"),
        ("Build a complete Codex replica with projects, plugins, automations, memory, and task loops", "blocked", "spec"),
    ]

    for prompt, expected_status, expected_workflow in cases:
        result = studio_runner.start_studio_run("openjarvis", chat["id"], prompt)
        assert result["run"]["status"] == expected_status, prompt
        assert result["decision"]["workflow"] == expected_workflow, prompt
        if expected_status == "running":
            assert result["run"]["tasks"], prompt
