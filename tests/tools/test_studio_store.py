from pathlib import Path

from openjarvis.tools import studio_store


def test_store_creates_default_openjarvis_project(tmp_path, monkeypatch):
    monkeypatch.setattr(studio_store, "STUDIO_ROOT", tmp_path)
    store = studio_store.StudioStore(tmp_path)

    state = store.initial_state()

    assert state["projects"][0]["id"] == "openjarvis"
    assert state["projects"][0]["title"] == "OpenJarvis"
    assert state["projects"][0]["repo_root"].endswith("OpenJarvis")


def test_store_persists_chat_messages_and_run_events(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat(project["id"], title="Build Studio")
    message = store.add_message(chat["id"], "operator", "Build Jarvis Studio")
    run = store.create_run(project["id"], chat["id"], "Build Jarvis Studio", workflow="execute")

    store.append_run_event(run["id"], "run.created", "Run created")
    store.append_run_event(run["id"], "run.workflow_selected", "Selected execute")

    fresh = studio_store.StudioStore(tmp_path)
    loaded_chat = fresh.get_chat(chat["id"])
    loaded_run = fresh.get_run(run["id"])

    assert loaded_chat["messages"][0]["id"] == message["id"]
    assert [e["type"] for e in loaded_run["events"]] == ["run.created", "run.workflow_selected"]


def test_store_searches_projects_chats_messages_and_runs(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("markets", title="Markets Lab")
    chat = store.create_chat(project["id"], title="SOL DCA backtest")
    store.add_message(chat["id"], "operator", "Run live SOL DCA backtest")
    run = store.create_run(project["id"], chat["id"], "paper-only SOL bot", workflow="execute")
    store.append_run_event(run["id"], "verification", "ROI and drawdown recorded")

    results = store.search("drawdown")

    assert any(item["type"] == "run_event" and item["run_id"] == run["id"] for item in results)


def test_store_quarantines_corrupt_json(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("openjarvis", title="OpenJarvis")
    path = store._project_path(project["id"])
    path.write_text("{bad json", encoding="utf-8")

    fresh = studio_store.StudioStore(tmp_path)
    state = fresh.initial_state()

    assert state["projects"]
    assert list(tmp_path.glob("corrupt/*.json"))
