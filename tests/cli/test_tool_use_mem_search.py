"""Tests for the mem_search tool wired into tool_use.py."""
import json
from unittest.mock import patch


def test_mem_search_schema_in_tool_schemas():
    """mem_search appears in TOOL_SCHEMAS."""
    from openjarvis.cli.tool_use import TOOL_SCHEMAS
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "mem_search" in names


def test_mem_search_schema_has_required_query():
    """mem_search schema marks 'query' as required."""
    from openjarvis.cli.tool_use import TOOL_SCHEMAS
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "mem_search")
    assert "query" in schema["function"]["parameters"]["required"]


def test_mem_search_in_dispatch():
    """mem_search key is present in _TOOL_DISPATCH."""
    from openjarvis.cli.tool_use import _TOOL_DISPATCH
    assert "mem_search" in _TOOL_DISPATCH


def test_tool_mem_search_returns_hits_when_available():
    """_tool_mem_search returns ok=True with structured hits."""
    from openjarvis.tools.agentmemory_client import Hit
    fake_hits = [
        Hit(snippet="fixed auth", score=0.9, session_id="sess-1", tier="episodic")
    ]
    with patch("openjarvis.tools.agentmemory_client.search", return_value=fake_hits):
        from openjarvis.cli.tool_use import _tool_mem_search
        result = json.loads(_tool_mem_search("auth fix", limit=5))
    assert result["ok"] is True
    assert len(result["hits"]) == 1
    assert result["hits"][0]["snippet"] == "fixed auth"
    assert result["hits"][0]["session_id"] == "sess-1"
    assert result["hits"][0]["tier"] == "episodic"


def test_tool_mem_search_returns_offline_when_unavailable():
    """_tool_mem_search returns ok=False when AgentMemoryUnavailable."""
    from openjarvis.tools.agentmemory_client import AgentMemoryUnavailable
    with patch("openjarvis.tools.agentmemory_client.search",
               side_effect=AgentMemoryUnavailable("server down")):
        from openjarvis.cli.tool_use import _tool_mem_search
        result = json.loads(_tool_mem_search("auth fix"))
    assert result["ok"] is False
    assert "offline" in result["reason"]


def test_tool_mem_search_returns_offline_on_unexpected_exception():
    """_tool_mem_search never propagates exceptions — always returns JSON."""
    with patch("openjarvis.tools.agentmemory_client.search",
               side_effect=RuntimeError("unexpected")):
        from openjarvis.cli.tool_use import _tool_mem_search
        result = json.loads(_tool_mem_search("query"))
    assert result["ok"] is False
