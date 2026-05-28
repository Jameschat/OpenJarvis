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


def test_store_archives_chat_out_of_default_list(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("openjarvis", title="OpenJarvis")
    keep = store.create_chat(project["id"], title="Keep")
    archive = store.create_chat(project["id"], title="Archive me")

    archived = store.archive_chat(archive["id"])

    assert archived["status"] == "archived"
    assert [chat["id"] for chat in store.list_chats(project["id"])] == [keep["id"]]
    assert store.get_chat(archive["id"])["status"] == "archived"
    assert archive["id"] in [chat["id"] for chat in store.list_chats(project["id"], include_archived=True)]


def test_store_soft_deletes_chat_to_trash(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat(project["id"], title="Remove me")
    chat_path = store._chat_path(chat["id"])

    deleted = store.delete_chat(chat["id"])

    assert deleted["status"] == "deleted"
    assert not chat_path.exists()
    assert not store.list_chats(project["id"])
    deleted_files = list((tmp_path / "deleted" / "chats").glob("*.json"))
    assert len(deleted_files) == 1
    assert deleted_files[0].name.startswith(chat["id"])


def test_store_branches_chat_from_user_message(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat(project["id"], title="Original task")
    first = store.add_message(chat["id"], "operator", "Build a plan")
    store.add_message(chat["id"], "jarvis", "First answer")
    second = store.add_message(chat["id"], "operator", "Make it faster")
    store.add_message(chat["id"], "jarvis", "Second answer")

    branch = store.branch_chat(chat["id"], second["id"], "Make it safer")

    assert branch["id"] != chat["id"]
    assert branch["title"] == "Original task - steer"
    assert branch["branch"]["source_chat_id"] == chat["id"]
    assert branch["branch"]["source_message_id"] == second["id"]
    assert [message["content"] for message in branch["messages"]] == ["Build a plan", "First answer", "Make it safer"]
    assert branch["messages"][0]["id"] != first["id"]
    assert branch["messages"][-1]["role"] == "operator"
    assert store.get_chat(chat["id"])["messages"][-1]["content"] == "Second answer"


def test_store_creates_context_continuation_chat_once(tmp_path):
    store = studio_store.StudioStore(tmp_path)
    project = store.ensure_project("openjarvis", title="OpenJarvis")
    chat = store.create_chat(project["id"], title="Deep project")
    store.add_message(chat["id"], "operator", "Plan the inventory app")

    continuation = store.create_context_continuation_chat(
        chat["id"],
        handoff_path="E:/Claude/Obsidian/Claude/Brain/Sessions/handoff.md",
        handoff_excerpt="Recent decisions and next action.",
    )
    again = store.create_context_continuation_chat(
        chat["id"],
        handoff_path="E:/Claude/Obsidian/Claude/Brain/Sessions/handoff.md",
        handoff_excerpt="Recent decisions and next action.",
    )

    assert again["id"] == continuation["id"]
    assert continuation["title"] == "Deep project - continuation"
    assert continuation["continuation"]["source_chat_id"] == chat["id"]
    assert continuation["continuation"]["handoff_path"].endswith("handoff.md")
    assert "Recent decisions and next action." in continuation["messages"][0]["content"]
    assert store.get_chat(chat["id"])["context_continuation"]["chat_id"] == continuation["id"]
