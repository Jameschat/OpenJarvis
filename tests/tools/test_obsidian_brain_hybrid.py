"""Tests for hybrid vault + episodic RRF layer in obsidian_brain."""
from pathlib import Path
from unittest.mock import patch

import pytest


def _hit(session_id="sess-1", snippet="agent performed action", score=0.9):
    from openjarvis.tools.agentmemory_client import Hit
    return Hit(snippet=snippet, score=score, session_id=session_id)


def _make_vault(tmp_path, content="deployment pipeline steps"):
    vault = tmp_path / "Brain"
    (vault / "Knowledge").mkdir(parents=True)
    (vault / "Knowledge" / "deploy.md").write_text(f"# Deploy\n{content}")
    return vault


# _episodic_rrf_merge unit tests

def test_rrf_merge_includes_vault_hits():
    from openjarvis.tools.obsidian_brain import _episodic_rrf_merge
    p = Path("/vault/Brain/Knowledge/auth.md")
    vault_sorted = [(0.8, p, "vault snippet")]
    merged = _episodic_rrf_merge(vault_sorted, [], limit=5)
    assert any(str(mp) == str(p) for mp, _ in merged)


def test_rrf_merge_includes_episodic_hits():
    from openjarvis.tools.obsidian_brain import _episodic_rrf_merge
    episodic = [_hit(session_id="sess-1", snippet="fixed auth token bug")]
    merged = _episodic_rrf_merge([], episodic, limit=5)
    snippets = [s for _, s in merged]
    assert any("[episodic]" in s for s in snippets)


def test_rrf_merge_respects_limit():
    from openjarvis.tools.obsidian_brain import _episodic_rrf_merge
    vault_sorted = [(float(10 - i), Path(f"/vault/note{i}.md"), f"s{i}") for i in range(8)]
    episodic = [_hit(session_id=f"s{i}") for i in range(8)]
    merged = _episodic_rrf_merge(vault_sorted, episodic, limit=5)
    assert len(merged) <= 5


def test_rrf_merge_empty_inputs():
    from openjarvis.tools.obsidian_brain import _episodic_rrf_merge
    assert _episodic_rrf_merge([], [], limit=5) == []


def test_rrf_episodic_paths_use_sentinel_prefix():
    from openjarvis.tools.obsidian_brain import _episodic_rrf_merge
    episodic = [_hit(session_id="my-session-123")]
    merged = _episodic_rrf_merge([], episodic, limit=5)
    paths = [str(p) for p, _ in merged]
    # On Windows, Path normalises "agentmemory:///" to "agentmemory:\" so we
    # check the common prefix "agentmemory:" which survives on all platforms.
    assert any(p.startswith("agentmemory:") for p in paths)


# recall() integration tests

def test_recall_includes_episodic_hits(tmp_path):
    vault = _make_vault(tmp_path, content="deployment pipeline steps")
    fake = _hit(snippet="deployed agent to production in task T3")
    with patch("openjarvis.tools.obsidian_brain.DEFAULT_VAULT", vault), \
         patch("openjarvis.tools.obsidian_brain._am_search", return_value=[fake]):
        from openjarvis.tools import obsidian_brain
        results = obsidian_brain.recall("deployment", limit=5)
    snippets = [s for _, s in results]
    assert any("[episodic]" in s for s in snippets)


def test_recall_degrades_when_agentmemory_unavailable(tmp_path):
    from openjarvis.tools.agentmemory_client import AgentMemoryUnavailable
    vault = _make_vault(tmp_path)
    with patch("openjarvis.tools.obsidian_brain.DEFAULT_VAULT", vault), \
         patch("openjarvis.tools.obsidian_brain._am_search",
               side_effect=AgentMemoryUnavailable("offline")):
        from openjarvis.tools import obsidian_brain
        results = obsidian_brain.recall("deployment", limit=5)
    assert isinstance(results, list)
    assert all(not str(p).startswith("agentmemory:///") for p, _ in results)


def test_recall_degrades_on_any_exception(tmp_path):
    vault = _make_vault(tmp_path)
    with patch("openjarvis.tools.obsidian_brain.DEFAULT_VAULT", vault), \
         patch("openjarvis.tools.obsidian_brain._am_search",
               side_effect=RuntimeError("unexpected crash")):
        from openjarvis.tools import obsidian_brain
        results = obsidian_brain.recall("deployment", limit=5)
    assert isinstance(results, list)


# vault_context_for_query() sentinel-path test

def test_vault_context_uses_snippet_for_episodic_paths(tmp_path):
    vault = _make_vault(tmp_path)
    episodic_path = Path("agentmemory:///sess-1")
    episodic_snippet = "[episodic] agent ran browser-pilot on example.com"
    fake_recall = [(episodic_path, episodic_snippet)]
    with patch("openjarvis.tools.obsidian_brain.DEFAULT_VAULT", vault), \
         patch("openjarvis.tools.obsidian_brain.recall", return_value=fake_recall):
        from openjarvis.tools import obsidian_brain
        ctx = obsidian_brain.vault_context_for_query("browser task", max_hits=1)
    assert "browser-pilot" in ctx
    assert "agentmemory:///" not in ctx
