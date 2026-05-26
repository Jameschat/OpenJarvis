from collections import Counter
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
        lambda requests: [{"id": "r1", "tool": "recall_vault", "ok": True, "hits": [{"path": "Projects/Networx.md"}]}],
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
