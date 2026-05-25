import json

from openjarvis.tools import studio_runner


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
