"""Unit tests for agentmemory_client — all HTTP calls are mocked."""
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _mock_response(body: dict):
    """Fake urllib response context manager returning JSON body."""
    m = MagicMock()
    m.read.return_value = json.dumps(body).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


# health()

def test_health_true_when_status_ok():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"service": "agentmemory", "status": "ok"})):
        assert c.health() is True

def test_health_true_when_ok_true():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"ok": True})):
        assert c.health() is True

def test_health_false_when_unavailable():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        assert c.health() is False


# search()

def test_search_returns_hit_list():
    import openjarvis.tools.agentmemory_client as c
    body = {
        "results": [
            {"observation": {"content": "fixed auth"}, "score": 0.9, "sessionId": "sess-1"}
        ],
        "format": "full",
        "tokens_used": 50,
        "truncated": False,
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        hits = c.search("authentication fix", limit=5)
    assert len(hits) == 1
    assert hits[0].snippet == "fixed auth"
    assert hits[0].score == 0.9
    assert hits[0].session_id == "sess-1"

def test_search_raises_on_timeout():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(c.AgentMemoryUnavailable):
            c.search("query")

def test_search_raises_on_connection_refused():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(c.AgentMemoryUnavailable):
            c.search("query")

def test_search_empty_list_on_unexpected_response_shape():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"hits": []})):
        hits = c.search("query")
    assert hits == []

def test_search_passes_project_in_body():
    import openjarvis.tools.agentmemory_client as c
    captured = {}
    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _mock_response({"results": []})
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        c.search("query", limit=3, project="openjarvis")
    assert captured["body"]["project"] == "openjarvis"
    assert captured["body"]["limit"] == 3


# remember()

def test_remember_returns_true_on_ok():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"ok": True})):
        assert c.remember("some content", tags=["tag1"]) is True

def test_remember_returns_false_on_not_ok():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"ok": False})):
        assert c.remember("some content") is False

def test_remember_raises_on_unavailable():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        with pytest.raises(c.AgentMemoryUnavailable):
            c.remember("content")


# reflect()

def test_reflect_returns_content_string():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"content": "lesson learned"})):
        assert c.reflect("authentication") == "lesson learned"

def test_reflect_returns_empty_string_on_missing_key():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({})):
        assert c.reflect("topic") == ""


# insights()

def test_insights_returns_list():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({"insights": ["A", "B"]})):
        assert c.insights() == ["A", "B"]

def test_insights_returns_empty_list_on_missing_key():
    import openjarvis.tools.agentmemory_client as c
    with patch("urllib.request.urlopen", return_value=_mock_response({})):
        assert c.insights() == []
