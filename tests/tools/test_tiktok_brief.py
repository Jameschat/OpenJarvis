"""Tests for tiktok_brief: URL parsing + metadata extraction.

Pure-logic tests use real URL strings + mocked yt-dlp.
Integration tests live in test_tiktok_brief_integration.py (deferred).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.tools import tiktok_brief


def test_extract_video_id_from_long_url():
    url = "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119"
    assert tiktok_brief._extract_video_id(url) == "7628009213884665119"


def test_extract_video_id_from_long_url_with_tracking_params():
    url = "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119?_r=1&_t=ZN-96EDYiWdyBq"
    assert tiktok_brief._extract_video_id(url) == "7628009213884665119"


def test_extract_video_id_from_short_url_returns_none_pre_resolve():
    """Short URLs need to be resolved (redirect followed) before video_id is
    available. The URL-parser by itself returns None — the caller is
    expected to resolve first via _resolve_short_url."""
    url = "https://vm.tiktok.com/ZNRsHGmus/"
    assert tiktok_brief._extract_video_id(url) is None


def test_extract_video_id_from_garbage_returns_none():
    assert tiktok_brief._extract_video_id("https://example.com/foo") is None
    assert tiktok_brief._extract_video_id("not a url") is None
    assert tiktok_brief._extract_video_id("") is None


def test_resolve_short_url_follows_redirect(monkeypatch):
    """vm.tiktok.com short-link returns a 301 to the canonical URL."""
    class _FakeResp:
        url = "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119?_r=1&_t=Z"
    def fake_head(url, **kw):
        return _FakeResp()
    import httpx
    monkeypatch.setattr(httpx, "head", fake_head)
    resolved = tiktok_brief._resolve_short_url("https://vm.tiktok.com/ZNRsHGmus/")
    assert "@jonathon.mj/video/7628009213884665119" in resolved


def test_resolve_short_url_passthrough_on_long_url():
    """Long URLs should be returned unchanged — no network call needed."""
    long_url = "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119"
    assert tiktok_brief._resolve_short_url(long_url) == long_url


def test_fetch_meta_returns_dict_from_mocked_ytdlp(monkeypatch):
    """yt-dlp's extract_info(download=False) returns a dict-shaped result.
    We need: id, title, description, uploader, duration, webpage_url."""
    fake_info = {
        "id": "7628009213884665119",
        "title": "Test caption text",
        "description": "Full description with #hashtag1 #hashtag2",
        "uploader": "jonathon.mj",
        "duration": 47,
        "webpage_url": "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119",
    }
    class _FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False): return fake_info
    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    meta = tiktok_brief._fetch_meta(
        "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119"
    )
    assert meta["id"] == "7628009213884665119"
    assert meta["uploader"] == "jonathon.mj"
    assert meta["duration"] == 47
    assert "#hashtag1" in meta["description"]


def test_fetch_meta_returns_none_when_ytdlp_raises(monkeypatch):
    """yt-dlp can fail (region-blocked, deleted video, etc). Caller must get
    None back, not an exception."""
    class _FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False):
            from yt_dlp.utils import DownloadError
            raise DownloadError("video unavailable")
    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    meta = tiktok_brief._fetch_meta("https://www.tiktok.com/@x/video/123")
    assert meta is None
