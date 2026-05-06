# tests/tiktok/test_tiktok_client.py
import json, os, pytest, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

def _mock_urlopen(body: dict):
    m = MagicMock()
    m.read.return_value = json.dumps(body).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m

def test_build_auth_url_contains_scopes():
    from openjarvis.tiktok.tiktok_client import build_auth_url
    url = build_auth_url("clientkey", "https://myapp.com/callback", "state123")
    assert "video.publish" in url
    assert "comment.list" in url
    assert "clientkey" in url

def test_fetch_video_stats():
    from openjarvis.tiktok.tiktok_client import fetch_video_stats
    mock_resp = _mock_urlopen({"data": {"videos": [{"id": "v1", "view_count": 5000}]}})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        stats = fetch_video_stats(["v1"], "token123")
    assert stats[0]["view_count"] == 5000

def test_fetch_user_info():
    from openjarvis.tiktok.tiktok_client import fetch_user_info
    mock_resp = _mock_urlopen({"data": {"user": {"follower_count": 12000}}})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = fetch_user_info("token123")
    assert info["follower_count"] == 12000

def test_fetch_comments():
    from openjarvis.tiktok.tiktok_client import fetch_comments
    mock_resp = _mock_urlopen({"data": {"comments": [{"comment_id": "c1", "text": "What tool?", "username": "@user1"}]}})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        comments = fetch_comments("vid123", "token123")
    assert comments[0]["comment_id"] == "c1"

def test_post_comment_returns_id():
    from openjarvis.tiktok.tiktok_client import post_comment
    mock_resp = _mock_urlopen({"error": {"code": "ok"}, "data": {"comment_id": "new_c1"}})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        cid = post_comment("vid123", "Great question!", "token123")
    assert cid == "new_c1"

def test_post_comment_raises_on_error():
    from openjarvis.tiktok.tiktok_client import post_comment, TikTokError
    mock_resp = _mock_urlopen({"error": {"code": "spam_risk", "message": "rate limited"}})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(TikTokError):
            post_comment("vid123", "reply", "token123")
