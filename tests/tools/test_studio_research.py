import json


def test_studio_research_detects_current_or_external_research_requests():
    from openjarvis.tools import studio_research

    assert studio_research.should_prefetch_research("Search GitHub for agent tools")
    assert studio_research.should_prefetch_research("What is the latest Qwen setup?")
    assert studio_research.should_prefetch_research("Plan a project using current market APIs")
    assert not studio_research.should_prefetch_research("Summarise my local project state")


def test_studio_research_prefetch_uses_web_and_github_tools(monkeypatch):
    from openjarvis.tools import studio_research

    calls = []

    def fake_web(query, limit=5):
        calls.append(("web", query, limit))
        return json.dumps({"hits": [{"title": "Doc", "url": "https://example.com", "snippet": "Useful"}]})

    def fake_github(query, limit=5):
        calls.append(("github", query, limit))
        return json.dumps({"repos": [{"name": "owner/repo", "url": "https://github.com/owner/repo", "description": "Tool", "stars": 42}]})

    monkeypatch.setattr(studio_research, "_web_search", fake_web)
    monkeypatch.setattr(studio_research, "_github_search", fake_github)

    result = studio_research.prefetch_research("Search GitHub for browser automation tools")

    assert result["ok"] is True
    assert calls[0][0] == "web"
    assert calls[1][0] == "github"
    assert "Doc" in result["markdown"]
    assert "owner/repo" in result["markdown"]


def test_start_run_appends_research_context_for_current_tasks(monkeypatch, tmp_path):
    from openjarvis.tools import studio_runner

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
            "workflow": "qwen_workflow",
            "reason": "research",
            "verification": {"required": True},
            "model": "qwen3.6-27b-local",
            "requires_operator_approval": False,
            "risks": [],
            "next_steps": [],
        },
    )
    monkeypatch.setattr(
        studio_runner.studio_research,
        "prefetch_research",
        lambda prompt, limit=4: {"ok": True, "markdown": "== WEB RESEARCH PREFETCH ==\nsource evidence"},
    )
    monkeypatch.setattr(
        studio_runner,
        "_queue_agent_task",
        lambda **kwargs: created_tasks.append(kwargs) or "task-1",
    )

    result = studio_runner.start_studio_run(
        project_id="openjarvis",
        chat_id="chat-1",
        prompt="Find the latest GitHub tools for Jarvis",
    )

    assert result["research"]["ok"] is True
    assert "source evidence" in created_tasks[0]["prompt"]
    assert any(e["type"] == "run.research_prefetched" for e in result["run"]["events"])
