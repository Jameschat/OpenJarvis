from pathlib import Path
from types import SimpleNamespace

from openjarvis.tools import studio_context


def test_context_pack_includes_project_files_and_memory(monkeypatch, tmp_path):
    project_dir = tmp_path / "Projects" / "OpenJarvis"
    project_dir.mkdir(parents=True)
    (project_dir / "STATE.md").write_text("# State\n\nWhere we left off", encoding="utf-8")
    (project_dir / "CONTEXT.md").write_text("# Context\n\nKey paths", encoding="utf-8")
    brain = tmp_path

    fake_ob = SimpleNamespace(
        BRAIN_ROOT=brain,
        recall=lambda query, limit=4: [(brain / "Knowledge" / "note.md", "memory snippet")],
    )
    monkeypatch.setattr(studio_context, "_obsidian", lambda: fake_ob)
    monkeypatch.setattr(
        studio_context,
        "_agentmemory_hits",
        lambda query, limit=3: [{"session_id": "s1", "snippet": "episodic"}],
    )
    monkeypatch.setattr(
        studio_context,
        "_graphify_status",
        lambda: {"online": True, "nodes": 10, "edges": 12},
    )
    monkeypatch.setattr(
        studio_context,
        "_codegraph_status_safe",
        lambda: {"online": True, "files": 2, "nodes": 3, "edges": 4},
    )

    pack = studio_context.build_project_context_pack(
        "Build Studio", project={"vault_project": "OpenJarvis"}, budget_chars=4000
    )

    assert pack["active_project"]["state_excerpt"]
    assert pack["vault"]["hits"][0]["snippet"] == "memory snippet"
    assert pack["episodic"]["hits"][0]["snippet"] == "episodic"
    assert pack["codegraph"]["online"] is True
    assert "PROJECT CONTEXT PACK" in pack["markdown"]


def test_context_pack_degrades_when_agentmemory_offline(monkeypatch, tmp_path):
    fake_ob = SimpleNamespace(BRAIN_ROOT=tmp_path, recall=lambda query, limit=4: [])
    monkeypatch.setattr(studio_context, "_obsidian", lambda: fake_ob)
    monkeypatch.setattr(
        studio_context,
        "_agentmemory_hits",
        lambda query, limit=3: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    pack = studio_context.build_project_context_pack("question", project=None)

    assert pack["ok"] is True
    assert pack["episodic"]["online"] is False
    assert pack["warnings"]


def test_context_pack_caps_markdown_budget(monkeypatch, tmp_path):
    long = "x" * 5000
    fake_ob = SimpleNamespace(
        BRAIN_ROOT=tmp_path,
        recall=lambda query, limit=4: [(tmp_path / "note.md", long)],
    )
    monkeypatch.setattr(studio_context, "_obsidian", lambda: fake_ob)
    monkeypatch.setattr(studio_context, "_agentmemory_hits", lambda query, limit=3: [])

    pack = studio_context.build_project_context_pack("question", project=None, budget_chars=900)

    assert len(pack["markdown"]) <= 950
    assert "untrusted" in pack["markdown"].lower()
