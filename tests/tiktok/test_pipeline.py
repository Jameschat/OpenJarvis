# tests/tiktok/test_pipeline.py
import pytest
from unittest.mock import patch, MagicMock

def test_get_pipeline_state_structure(tmp_path):
    with patch("openjarvis.tiktok.state._TIKTOK_DIR", tmp_path), \
         patch("openjarvis.tiktok.trend_scorer.fetch_and_score", return_value=[]):
        from openjarvis.tiktok.pipeline import get_pipeline_state
        state = get_pipeline_state()
    assert "settings" in state
    assert "queue" in state
    assert "posted" in state
    assert "finance" in state
    assert "comments" in state
    assert "pending" in state["queue"]

def test_video_generator_entry_no_key(tmp_path):
    with patch("openjarvis.tiktok.state._TIKTOK_DIR", tmp_path):
        from openjarvis.tiktok.pipeline import video_generator_entry
        result = video_generator_entry({"title": "test", "script": "hello"})
    assert result["status"] == "error"
    assert "Kling" in result["message"]

def test_tiktok_publisher_entry_no_token(tmp_path):
    with patch("openjarvis.tiktok.state._TIKTOK_DIR", tmp_path):
        from openjarvis.tiktok.pipeline import tiktok_publisher_entry
        result = tiktok_publisher_entry({"queue_id": "abc"})
    assert result["status"] == "error"
    assert "TikTok" in result["message"]

def test_stats_puller_entry_no_token(tmp_path):
    with patch("openjarvis.tiktok.state._TIKTOK_DIR", tmp_path):
        from openjarvis.tiktok.pipeline import stats_puller_entry
        result = stats_puller_entry({})
    assert result["status"] == "error"
