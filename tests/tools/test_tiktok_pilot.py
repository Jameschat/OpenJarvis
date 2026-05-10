"""Tests for the tiktok voice fast-path detector."""
from __future__ import annotations

from openjarvis.tools import tiktok_pilot


def test_try_tiktok_matches_brief_phrasing():
    text = "brief this tiktok: https://vm.tiktok.com/ZNRsHGmus/"
    result = tiktok_pilot._extract_url(text)
    assert result == "https://vm.tiktok.com/ZNRsHGmus/"


def test_try_tiktok_matches_summarise_phrasing():
    text = "summarise this tiktok https://www.tiktok.com/@user/video/123"
    result = tiktok_pilot._extract_url(text)
    assert result == "https://www.tiktok.com/@user/video/123"


def test_try_tiktok_matches_bare_url():
    text = "https://vm.tiktok.com/ZNRsHGmus/"
    result = tiktok_pilot._extract_url(text)
    assert result == "https://vm.tiktok.com/ZNRsHGmus/"


def test_try_tiktok_extracts_url_with_tracking_params():
    text = "brief this tiktok https://www.tiktok.com/@x/video/123?_r=1&_t=Z"
    result = tiktok_pilot._extract_url(text)
    assert result == "https://www.tiktok.com/@x/video/123?_r=1&_t=Z"


def test_try_tiktok_returns_none_for_youtube():
    text = "brief this youtube https://youtube.com/watch?v=abc"
    assert tiktok_pilot._extract_url(text) is None


def test_try_tiktok_returns_none_for_no_url():
    assert tiktok_pilot._extract_url("how are you today") is None
    assert tiktok_pilot._extract_url("") is None


def test_try_tiktok_full_dispatches_task(monkeypatch):
    """When trigger fires, _try_tiktok should add an agent task and return
    a spoken acknowledgment string."""
    captured = {}
    def fake_add_task(*, title, agent_id, prompt, priority):
        captured["title"] = title
        captured["agent_id"] = agent_id
        captured["prompt"] = prompt
        return "task_id_xyz"
    monkeypatch.setattr(
        "openjarvis.tools.agent_runner.add_task", fake_add_task,
    )
    result = tiktok_pilot._try_tiktok(
        "brief this tiktok: https://vm.tiktok.com/ZNRsHGmus/"
    )
    assert result is not None
    assert "tiktok" in result.lower() or "brief" in result.lower()
    assert captured.get("agent_id") in (
        "browser-pilot",
        "tiktok-pilot",
        "architect",
    )
    assert "vm.tiktok.com/ZNRsHGmus" in captured.get("prompt", "")


def test_try_tiktok_returns_none_when_no_url():
    """No URL = no trigger = let the LLM handle it."""
    assert tiktok_pilot._try_tiktok("hi") is None
    assert tiktok_pilot._try_tiktok("") is None
