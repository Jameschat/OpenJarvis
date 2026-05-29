from collections import Counter
import json
import sys
import types


def test_qwen_agents_are_registered_as_local_provider():
    from openjarvis.tools.agent_runner import DEFAULT_AGENTS

    qwen_agents = [a for a in DEFAULT_AGENTS if a.get("provider") == "qwen"]

    assert {a["id"] for a in qwen_agents} == {
        "qwen-chief",
        "qwen-researcher",
        "qwen-planner",
        "qwen-docs",
        "qwen-study",
        "qwen-capability-scout",
        "qwen-builder",
        "qwen-reviewer",
        "qwen-tester",
    }
    assert all(a.get("model") == "qwen3.6-27b-local" for a in qwen_agents)
    assert all(a.get("department") == "qwen" for a in qwen_agents)


def test_agent_roster_includes_qwen_without_replacing_claude_or_codex():
    from openjarvis.tools.agent_runner import DEFAULT_AGENTS

    providers = Counter(a.get("provider") for a in DEFAULT_AGENTS)

    assert providers["qwen"] == 9
    assert providers["claude"] >= 24
    assert providers["codex"] >= 6
    assert providers["python"] >= 12


def test_qwen_department_routes_to_local_chief():
    from openjarvis.cli import tool_use
    from openjarvis.tools import agent_runner

    assert agent_runner.DEPT_TO_HEAD["qwen"] == "qwen-chief"
    assert tool_use._resolve_department("qwen") == ("qwen-chief", "Qwen local")


def test_qwen_department_workflow_queues_specialists(monkeypatch):
    from openjarvis.tools import agent_runner

    queued = []

    def fake_add_task(**kwargs):
        queued.append(kwargs)
        return f"task-{len(queued)}"

    monkeypatch.setattr(agent_runner, "add_task", fake_add_task)

    ids = agent_runner.kick_off_qwen_department_workflow(
        goal="Build a tiny customer portal prototype",
        title="Customer portal",
    )

    assert ids == [f"task-{i}" for i in range(1, 8)]
    assert [q["agent_id"] for q in queued] == [
        "qwen-researcher",
        "qwen-planner",
        "qwen-builder",
        "qwen-tester",
        "qwen-docs",
        "qwen-reviewer",
        "qwen-chief",
    ]
    assert {q["project_id"] for q in queued} == {queued[0]["project_id"]}
    assert queued[-1]["priority"] > queued[0]["priority"]


def test_qwen_provider_runtime_path_exists():
    import inspect
    from openjarvis.tools import agent_runner

    run_task_source = inspect.getsource(agent_runner._run_task)
    qwen_source = inspect.getsource(agent_runner._run_qwen_task)

    assert 'if provider == "qwen":' in run_task_source
    assert "RESULT.md" in qwen_source
    assert "OpenAI(base_url=base_url" in qwen_source
    assert 'model = (agent_spec.get("model") or "qwen3.6-27b-local")' in qwen_source
    assert "_call_qwen_via_ollama" in qwen_source


def test_qwen_provider_bypasses_stalled_litellm_for_direct_ollama(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    class DummyRegistry:
        def __init__(self):
            self.finished = []

        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            self.finished.append((task_id, exit_code, error))

    dummy_reg = DummyRegistry()
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", dummy_reg)
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_runner, "_litellm_proxy_healthy", lambda base_url: False)
    monkeypatch.setattr(agent_runner, "_call_qwen_via_ollama", lambda prompt, **kwargs: "Direct Ollama result.")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:4000")

    task = Task(
        id="task-qwen-direct",
        title="Qwen direct test",
        agent_id="qwen-researcher",
        prompt="Use local model.",
    )

    agent_runner._run_qwen_task(
        task,
        {"id": "qwen-researcher", "role": "Research locally.", "model": "qwen3.6-27b-local"},
    )

    result = tmp_path / "task-qwen-direct" / "RESULT.md"
    assert "Direct Ollama result." in result.read_text(encoding="utf-8")
    assert dummy_reg.finished == [("task-qwen-direct", 0, None)]


def test_qwen_provider_writes_result_markdown(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    class DummyMessage:
        content = "Local Qwen result body."

    class DummyChoice:
        message = DummyMessage()

    class DummyCompletions:
        def create(self, **kwargs):
            assert kwargs["model"] == "qwen3.6-27b-local"
            assert kwargs["extra_body"] == {
                "chat_template_kwargs": {"enable_thinking": False}
            }
            return types.SimpleNamespace(choices=[DummyChoice()])

    class DummyClient:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://localhost:4000"
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def __init__(self):
            self.running = []
            self.finished = []

        def mark_running(self, task_id, workspace):
            self.running.append((task_id, workspace))

        def mark_finished(self, task_id, exit_code, error=None):
            self.finished.append((task_id, exit_code, error))

    dummy_reg = DummyRegistry()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", dummy_reg)
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:4000")

    task = Task(
        id="task-qwen",
        title="Qwen test",
        agent_id="qwen-researcher",
        prompt="Summarise local provider wiring.",
    )
    agent_spec = {
        "id": "qwen-researcher",
        "role": "Research locally.",
        "model": "qwen3.6-27b-local",
    }

    agent_runner._run_qwen_task(task, agent_spec)

    result_path = tmp_path / "task-qwen" / "RESULT.md"
    assert result_path.exists()
    assert "Local Qwen result body." in result_path.read_text(encoding="utf-8")
    assert dummy_reg.running == [("task-qwen", str(tmp_path / "task-qwen"))]
    assert dummy_reg.finished == [("task-qwen", 0, None)]


def test_qwen_provider_executes_safe_tool_bridge_round(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner, qwen_tool_bridge
    from openjarvis.tools.agent_runner import Task

    calls = []

    class DummyMessage:
        def __init__(self, content):
            self.content = content

    class DummyChoice:
        def __init__(self, content):
            self.message = DummyMessage(content)

    class DummyCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return types.SimpleNamespace(
                    choices=[
                        DummyChoice(
                            'Need context.\n```qwen_tool_requests\n{"requests":[{"id":"r1","tool":"recall_vault","args":{"query":"Networx"}}]}\n```'
                        )
                    ]
                )
            assert "QWEN TOOL RESULTS" in kwargs["messages"][0]["content"]
            return types.SimpleNamespace(choices=[DummyChoice("Final answer using vault context.")])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        qwen_tool_bridge,
        "execute_tool_requests",
        lambda requests, **kwargs: [{"id": "r1", "tool": "recall_vault", "ok": True, "hits": [{"path": "Projects/Networx.md"}]}],
    )

    task = Task(
        id="task-qwen-tools",
        title="Qwen tool bridge",
        agent_id="qwen-researcher",
        prompt="Use memory.",
    )

    agent_runner._run_qwen_task(
        task,
        {"id": "qwen-researcher", "role": "Research.", "model": "qwen3.6-27b-local"},
    )

    ws = tmp_path / "task-qwen-tools"
    assert len(calls) == 2
    assert "Final answer using vault context." in (ws / "RESULT.md").read_text(encoding="utf-8")
    assert (ws / "QWEN_TOOL_RESULTS.json").exists()


def test_qwen_provider_revises_weak_complex_answer(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    calls = []

    class DummyMessage:
        def __init__(self, content):
            self.content = content

    class DummyChoice:
        def __init__(self, content):
            self.message = DummyMessage(content)

    class DummyCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return types.SimpleNamespace(choices=[DummyChoice("I can help with that.")])
            assert "Quality issues" in kwargs["messages"][0]["content"]
            return types.SimpleNamespace(
                choices=[
                    DummyChoice(
                        "Assumptions: local Qwen should stay safe.\n\n"
                        "Verification: checked the requested workflow and no shell authority is needed.\n\n"
                        "Next actions: create a plan, retrieve memory, execute the safe bridge, and escalate if confidence is low. "
                        "This keeps the local model useful for routine project planning while preserving Claude/Codex backup for risky edits."
                    )
                ]
            )

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0
            assert error is None

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)

    task = Task(
        id="task-qwen-quality-loop",
        title="Quality loop",
        agent_id="qwen-planner",
        prompt="Plan a project workflow for Jarvis Studio.",
    )

    agent_runner._run_qwen_task(
        task,
        {"id": "qwen-planner", "role": "Plan.", "model": "qwen3.6-27b-local"},
    )

    result = (tmp_path / "task-qwen-quality-loop" / "RESULT.md").read_text(encoding="utf-8")
    assert len(calls) == 2
    assert "I can help with that." not in result
    assert "Qwen Quality Gate" in result
    assert "Status: passed" in result


def test_qwen_provider_accepts_xml_style_tool_request_block(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner, qwen_tool_bridge
    from openjarvis.tools.agent_runner import Task

    calls = []

    class DummyMessage:
        def __init__(self, content):
            self.content = content

    class DummyChoice:
        def __init__(self, content):
            self.message = DummyMessage(content)

    class DummyCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return types.SimpleNamespace(
                    choices=[
                        DummyChoice(
                            '<qwen_tool_requests>\n{"requests":[{"id":"r1","tool":"recall_vault","args":{"query":"qwen tokens per second"}}]}\n</qwen_tool_requests>'
                        )
                    ]
                )
            return types.SimpleNamespace(choices=[DummyChoice("Current local Qwen is about 60-76 tok/s.")])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        qwen_tool_bridge,
        "execute_tool_requests",
        lambda requests, **kwargs: [{"id": "r1", "tool": "recall_vault", "ok": True, "hits": []}],
    )

    agent_runner._run_qwen_task(
        Task(
            id="task-qwen-xml-tools",
            title="Qwen XML tool bridge",
            agent_id="qwen-researcher",
            prompt="How many tokens per second?",
        ),
        {"id": "qwen-researcher", "role": "Research.", "model": "qwen3.6-27b-local"},
    )

    result = (tmp_path / "task-qwen-xml-tools" / "RESULT.md").read_text(encoding="utf-8")
    assert len(calls) == 2
    assert "Current local Qwen is about 60-76 tok/s." in result
    assert "<qwen_tool_requests>" not in result


def test_qwen_provider_executes_multiple_safe_tool_bridge_rounds(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner, qwen_tool_bridge
    from openjarvis.tools.agent_runner import Task

    calls = []
    executed = []
    parsed = []

    class DummyMessage:
        def __init__(self, content):
            self.content = content

    class DummyChoice:
        def __init__(self, content):
            self.message = DummyMessage(content)

    class DummyCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return types.SimpleNamespace(
                    choices=[
                        DummyChoice(
                            '<qwen_tool_requests>{"requests":[{"id":"r1","tool":"recall_vault","args":{"query":"Networx"}}]}</qwen_tool_requests>'
                        )
                    ]
                )
            if len(calls) == 2:
                assert "vault hit" in kwargs["messages"][0]["content"]
                return types.SimpleNamespace(
                    choices=[
                        DummyChoice(
                            '```qwen_tool_requests\n{"requests":[{"id":"r2","tool":"repo_search","args":{"query":"Networx"}}]}\n```'
                        )
                    ]
                )
            if "repo hit" in kwargs["messages"][0]["content"]:
                return types.SimpleNamespace(
                    choices=[
                        DummyChoice(
                            "Assumptions: Networx context came from vault memory and repository search.\n\n"
                            "Verification: used recall_vault first, then repo_search, and combined both tool results.\n\n"
                            "Next actions: use the memory hit for project background, inspect the repo hit before any edit, "
                            "and escalate to Codex if production code changes are required. This answer is final and does "
                            "not need another tool request."
                        )
                    ]
                )
            return types.SimpleNamespace(choices=[DummyChoice("Unexpected revision without second tool result.")])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    def fake_execute(requests, **kwargs):
        executed.extend(requests)
        if requests[0]["id"] == "r1":
            return [{"id": "r1", "tool": "recall_vault", "ok": True, "summary": "vault hit"}]
        return [{"id": "r2", "tool": "repo_search", "ok": True, "summary": "repo hit"}]

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_tool_bridge, "execute_tool_requests", fake_execute)

    def recording_parse(content):
        if "qwen_tool_requests" not in content:
            requests = []
        elif "recall_vault" in content:
            requests = [{"id": "r1", "tool": "recall_vault", "args": {"query": "Networx"}}]
        elif "repo_search" in content:
            requests = [{"id": "r2", "tool": "repo_search", "args": {"query": "Networx"}}]
        else:
            requests = []
        parsed.append(requests)
        return requests

    monkeypatch.setattr(qwen_tool_bridge, "parse_tool_requests", recording_parse)

    agent_runner._run_qwen_task(
        Task(
            id="task-qwen-multi-tools",
            title="Qwen multi tool bridge",
            agent_id="qwen-researcher",
            prompt="Research Networx from memory and repo.",
        ),
        {"id": "qwen-researcher", "role": "Research.", "model": "qwen3.6-27b-local"},
    )

    ws = tmp_path / "task-qwen-multi-tools"
    result = (ws / "RESULT.md").read_text(encoding="utf-8")
    tool_log = json.loads((ws / "QWEN_TOOL_RESULTS.json").read_text(encoding="utf-8"))
    assert len(calls) == 3
    assert [request[0]["id"] if request else "" for request in parsed[:3]] == ["r1", "r2", ""]
    assert [request["id"] for request in executed] == ["r1", "r2"]
    assert "combined both tool results" in result
    assert "<qwen_tool_requests>" not in result
    assert [result["id"] for result in tool_log["results"]] == ["r1", "r2"]


def test_qwen_project_tasks_write_task_scoped_result(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    class DummyMessage:
        content = "Project-scoped result."

    class DummyChoice:
        message = DummyMessage()

    class DummyCompletions:
        def create(self, **kwargs):
            return types.SimpleNamespace(choices=[DummyChoice()])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)

    task = Task(
        id="task-project",
        title="Project result",
        agent_id="qwen-planner",
        prompt="Plan it.",
        project_id="qwen-workflow-test",
    )

    agent_runner._run_qwen_task(
        task,
        {"id": "qwen-planner", "role": "Plan.", "model": "qwen3.6-27b-local"},
    )

    ws = tmp_path / "qwen-workflow-test"
    assert (ws / "task-project.RESULT.md").exists()
    assert not (ws / "RESULT.md").exists()


def test_qwen_provider_uses_persisted_quality_profile(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    class DummyMessage:
        content = "Quality profile result."

    class DummyChoice:
        message = DummyMessage()

    class DummyCompletions:
        def create(self, **kwargs):
            assert kwargs["model"] == "qwen3.6-27b-quality"
            return types.SimpleNamespace(choices=[DummyChoice()])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    profile_path = tmp_path / ".openjarvis" / "studio" / "qwen_profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text('{"active":"quality"}', encoding="utf-8")
    monkeypatch.setattr(agent_runner.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("OPENJARVIS_QWEN_PROFILE", raising=False)
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)

    task = Task(
        id="task-qwen-quality",
        title="Quality profile",
        agent_id="qwen-planner",
        prompt="Plan it.",
    )

    agent_runner._run_qwen_task(
        task,
        {"id": "qwen-planner", "role": "Plan.", "model": "qwen3.6-27b-local"},
    )

    assert "Quality profile result." in (tmp_path / "runs" / "task-qwen-quality" / "RESULT.md").read_text(
        encoding="utf-8"
    )


def test_qwen_quality_profile_retries_local_when_litellm_lacks_alias(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    calls = []

    class DummyMessage:
        content = (
            "Assumptions: use the local Qwen route when the quality alias is missing.\n\n"
            "Verification: the request retried successfully on qwen3.6-27b-local.\n\n"
            "Next actions: continue the Studio planning task and keep the quality profile available "
            "when the dedicated server is configured."
        )

    class DummyChoice:
        message = DummyMessage()

    class DummyCompletions:
        def create(self, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "qwen3.6-27b-quality":
                raise RuntimeError("Invalid model name passed in model=qwen3.6-27b-quality")
            assert kwargs["model"] == "qwen3.6-27b-local"
            return types.SimpleNamespace(choices=[DummyChoice()])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    profile_path = tmp_path / ".openjarvis" / "studio" / "qwen_profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text('{"active":"quality"}', encoding="utf-8")
    monkeypatch.setattr(agent_runner.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("OPENJARVIS_QWEN_PROFILE", raising=False)
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)

    agent_runner._run_qwen_task(
        Task(
            id="task-qwen-quality-retry",
            title="Quality retry",
            agent_id="qwen-planner",
            prompt="Plan it.",
        ),
        {"id": "qwen-planner", "role": "Plan.", "model": "qwen3.6-27b-local"},
    )

    assert calls == ["qwen3.6-27b-quality", "qwen3.6-27b-local"]
    assert "retried successfully on qwen3.6-27b-local" in (
        tmp_path / "runs" / "task-qwen-quality-retry" / "RESULT.md"
    ).read_text(encoding="utf-8")


def test_qwen_provider_uses_reasoning_content_only_as_fallback(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    class DummyMessage:
        content = ""
        reasoning_content = "Fallback reasoning result."

    class DummyChoice:
        message = DummyMessage()

    class DummyCompletions:
        def create(self, **kwargs):
            assert kwargs["extra_body"] == {
                "chat_template_kwargs": {"enable_thinking": False}
            }
            return types.SimpleNamespace(choices=[DummyChoice()])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)

    task = Task(
        id="task-qwen-reasoning",
        title="Reasoning fallback",
        agent_id="qwen-researcher",
        prompt="Write it.",
    )
    agent_runner._run_qwen_task(
        task,
        {"id": "qwen-researcher", "role": "Research.", "model": "qwen3.6-27b-local"},
    )

    result = tmp_path / "task-qwen-reasoning" / "RESULT.md"
    assert "Fallback reasoning result." in result.read_text(encoding="utf-8")


def test_qwen_builder_writes_safe_workspace_files(monkeypatch, tmp_path):
    from openjarvis.tools import agent_runner
    from openjarvis.tools.agent_runner import Task

    content = """Built a tiny page.

```qwen_workspace_files
{"files":[{"path":"index.html","content":"<h1>Hello Jarvis</h1>"}]}
```
"""

    class DummyMessage:
        pass

    DummyMessage.content = content

    class DummyChoice:
        message = DummyMessage()

    class DummyCompletions:
        def create(self, **kwargs):
            assert "qwen_workspace_files" in kwargs["messages"][0]["content"]
            return types.SimpleNamespace(choices=[DummyChoice()])

    class DummyClient:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=DummyCompletions())

    class DummyRegistry:
        def mark_running(self, task_id, workspace):
            pass

        def mark_finished(self, task_id, exit_code, error=None):
            assert exit_code == 0
            assert error is None

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=DummyClient))
    monkeypatch.setattr(agent_runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent_runner, "_reg", DummyRegistry())
    monkeypatch.setattr(agent_runner, "_build_brain_context", lambda: "")
    monkeypatch.setattr(agent_runner, "_write_agent_task_note", lambda *args, **kwargs: None)

    task = Task(
        id="task-build",
        title="Build page",
        agent_id="qwen-builder",
        prompt="Create a small web page.",
    )
    agent_runner._run_qwen_task(
        task,
        {
            "id": "qwen-builder",
            "role": "Build sandbox files.",
            "model": "qwen3.6-27b-local",
            "workspace_write": True,
        },
    )

    assert (tmp_path / "task-build" / "index.html").read_text(encoding="utf-8") == (
        "<h1>Hello Jarvis</h1>"
    )


def test_qwen_workspace_files_reject_path_escape(tmp_path):
    from openjarvis.tools import agent_runner

    content = """```qwen_workspace_files
{"files":[{"path":"../outside.txt","content":"bad"}]}
```"""

    written = agent_runner._write_qwen_workspace_files(content, tmp_path)

    assert written == []
    assert not (tmp_path.parent / "outside.txt").exists()


def test_qwen_should_think_default_background_complex_only(monkeypatch):
    from openjarvis.tools import agent_runner

    monkeypatch.delenv("OPENJARVIS_QWEN_THINKING", raising=False)
    # default = background: think on complex tasks, not on simple ones
    assert agent_runner._qwen_should_think("build the westhill website") is True
    assert agent_runner._qwen_should_think("hi there, how are you") is False


def test_qwen_should_think_off_disables_everywhere(monkeypatch):
    from openjarvis.tools import agent_runner

    monkeypatch.setenv("OPENJARVIS_QWEN_THINKING", "off")
    assert agent_runner._qwen_should_think("build a complex app and plan it") is False


def test_qwen_should_think_all_enables_even_simple(monkeypatch):
    from openjarvis.tools import agent_runner

    monkeypatch.setenv("OPENJARVIS_QWEN_THINKING", "all")
    assert agent_runner._qwen_should_think("hi") is True
