# tests/tiktok/test_state.py
import json, tempfile, os, pytest
from pathlib import Path
from unittest.mock import patch

@pytest.fixture
def tiktok_dir(tmp_path):
    with patch("openjarvis.tiktok.state._TIKTOK_DIR", tmp_path):
        yield tmp_path

def test_add_and_load_queue(tiktok_dir):
    from openjarvis.tiktok.state import add_to_queue, load_queue, get_pending_queue
    entry = add_to_queue("Test title", "/scripts/a.md", "/videos/a.mp4", "caption #ai", ["ai", "tech"])
    assert entry["status"] == "pending"
    assert entry["id"]
    pending = get_pending_queue()
    assert len(pending) == 1
    assert pending[0]["title"] == "Test title"

def test_approve_video(tiktok_dir):
    from openjarvis.tiktok.state import add_to_queue, approve_video, get_approved_queue
    entry = add_to_queue("T", "/s", "/v", "cap", [])
    assert approve_video(entry["id"])
    assert get_approved_queue()[0]["status"] == "approved"

def test_reject_video(tiktok_dir):
    from openjarvis.tiktok.state import add_to_queue, reject_video, get_pending_queue
    entry = add_to_queue("T", "/s", "/v", "cap", [])
    assert reject_video(entry["id"])
    assert get_pending_queue() == []

def test_add_posted(tiktok_dir):
    from openjarvis.tiktok.state import add_posted, load_posted, update_posted_stats
    add_posted("vid123", "My video", "caption")
    assert load_posted()[0]["video_id"] == "vid123"
    assert update_posted_stats("vid123", 1000, 50, 5, 7.5)
    assert load_posted()[0]["views"] == 1000

def test_add_comment_reply_deduplicates(tiktok_dir):
    from openjarvis.tiktok.state import add_comment_reply, get_pending_comments
    add_comment_reply("c1", "v1", "@user1", "What tool?", "Great question!")
    add_comment_reply("c2", "v1", "@user1", "Another q?", "Draft 2")  # same commenter+video
    assert len(get_pending_comments()) == 1  # deduped

def test_approve_and_reject_comment(tiktok_dir):
    from openjarvis.tiktok.state import add_comment_reply, approve_comment, reject_comment, get_pending_comments
    r = add_comment_reply("c1", "v1", "@u", "Q?", "A!")
    assert approve_comment(r["id"])
    assert get_pending_comments() == []
    r2 = add_comment_reply("c2", "v2", "@u2", "Q2?", "A2!")
    assert reject_comment(r2["id"])
    assert get_pending_comments() == []

def test_settings(tiktok_dir):
    from openjarvis.tiktok.state import set_setting, get_setting
    set_setting("threshold", 75)
    assert get_setting("threshold") == 75
    assert get_setting("missing_key", "default") == "default"
