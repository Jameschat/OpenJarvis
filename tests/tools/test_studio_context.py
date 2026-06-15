from pathlib import Path
from types import SimpleNamespace

from openjarvis.tools import studio_context


def test_context_pack_includes_project_files_and_memory(monkeypatch, tmp_path):
    project_dir = tmp_path / "Projects" / "OpenJarvis"
    project_dir.mkdir(parents=True)
    (tmp_path / "00 Session Handoff.md").write_text("# Handoff\n\nCurrent cross-agent state", encoding="utf-8")
    (project_dir / "STATE.md").write_text("# State\n\nWhere we left off", encoding="utf-8")
    (project_dir / "CONTEXT.md").write_text("# Context\n\nKey paths", encoding="utf-8")
    (project_dir / "ROADMAP.md").write_text("# Roadmap\n\nNext phase", encoding="utf-8")
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

    assert pack["active_project"]["handoff_excerpt"]
    assert pack["active_project"]["state_excerpt"]
    assert pack["active_project"]["roadmap_excerpt"]
    assert pack["vault"]["hits"][0]["snippet"] == "memory snippet"
    assert pack["episodic"]["hits"][0]["snippet"] == "episodic"
    assert pack["codegraph"]["online"] is True
    assert "PROJECT CONTEXT PACK" in pack["markdown"]
    assert "00 Session Handoff.md" in pack["markdown"]
    assert "ROADMAP.md" in pack["markdown"]


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


def test_excerpt_chars_param_widens_file_excerpt_default_unchanged(monkeypatch, tmp_path):
    # The per-file excerpt cap must default to 1200 (no regression for existing
    # callers / small models) but be widenable so a big-window lane gets fuller
    # project files (the real bottleneck behind a small context pack).
    project_dir = tmp_path / "Projects" / "OpenJarvis"
    project_dir.mkdir(parents=True)
    (project_dir / "STATE.md").write_text("S" * 5000, encoding="utf-8")
    fake_ob = SimpleNamespace(BRAIN_ROOT=tmp_path, recall=lambda q, limit=4: [])
    monkeypatch.setattr(studio_context, "_obsidian", lambda: fake_ob)
    monkeypatch.setattr(studio_context, "_agentmemory_hits", lambda q, limit=3: [])
    monkeypatch.setattr(studio_context, "_graphify_status", lambda: {"online": False})
    monkeypatch.setattr(studio_context, "_codegraph_status_safe", lambda: {"online": False})

    default_pack = studio_context.build_project_context_pack(
        "q", project={"vault_project": "OpenJarvis"}
    )
    wide_pack = studio_context.build_project_context_pack(
        "q", project={"vault_project": "OpenJarvis"}, excerpt_chars=4000, budget_chars=40000
    )

    assert len(default_pack["active_project"]["state_excerpt"]) <= 1200
    assert len(wide_pack["active_project"]["state_excerpt"]) > 1200
    assert len(wide_pack["active_project"]["state_excerpt"]) <= 4000


def test_recall_and_episodic_limits_are_parameterised(monkeypatch, tmp_path):
    seen = {}

    def fake_recall(query, limit=4):
        seen["recall"] = limit
        return []

    def fake_episodic(query, limit=3):
        seen["episodic"] = limit
        return []

    fake_ob = SimpleNamespace(BRAIN_ROOT=tmp_path, recall=fake_recall)
    monkeypatch.setattr(studio_context, "_obsidian", lambda: fake_ob)
    monkeypatch.setattr(studio_context, "_agentmemory_hits", fake_episodic)

    studio_context.build_project_context_pack("q", project=None, recall_limit=8, episodic_limit=5)

    assert seen["recall"] == 8
    assert seen["episodic"] == 5


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
