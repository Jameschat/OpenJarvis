import json
from pathlib import Path

from openjarvis.tools import studio_runner
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
