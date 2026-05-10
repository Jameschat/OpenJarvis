from collections import Counter
import sys
import types


def test_qwen_agents_are_registered_as_local_provider():
    from openjarvis.tools.agent_runner import DEFAULT_AGENTS

    qwen_agents = [a for a in DEFAULT_AGENTS if a.get("provider") == "qwen"]

    assert {a["id"] for a in qwen_agents} == {
        "qwen-researcher",
        "qwen-planner",
        "qwen-docs",
        "qwen-study",
        "qwen-capability-scout",
    }
    assert all(a.get("model") == "qwen3.6-27b-local" for a in qwen_agents)


def test_agent_roster_includes_qwen_without_replacing_claude_or_codex():
    from openjarvis.tools.agent_runner import DEFAULT_AGENTS

    providers = Counter(a.get("provider") for a in DEFAULT_AGENTS)

    assert providers["qwen"] == 5
    assert providers["claude"] >= 24
    assert providers["codex"] >= 6
    assert providers["python"] >= 12


def test_qwen_provider_runtime_path_exists():
    import inspect
    from openjarvis.tools import agent_runner

    run_task_source = inspect.getsource(agent_runner._run_task)
    qwen_source = inspect.getsource(agent_runner._run_qwen_task)

    assert 'if provider == "qwen":' in run_task_source
    assert "RESULT.md" in qwen_source
    assert "OpenAI(base_url=base_url" in qwen_source
    assert 'model = (agent_spec.get("model") or "qwen3.6-27b-local")' in qwen_source


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
