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


def test_download_audio_returns_path_on_success(tmp_path, monkeypatch):
    """yt-dlp writes audio to dest_dir and we return the resulting filename."""
    target = tmp_path / "audio.m4a"
    target.write_bytes(b"fake audio bytes")  # simulate yt-dlp finishing

    captured = {}
    class _FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=True):
            return {"id": "abc", "ext": "m4a"}
        def prepare_filename(self, info):
            return str(target)
    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)

    out = tiktok_brief._download_audio(
        "https://www.tiktok.com/@x/video/123", dest_dir=tmp_path,
    )
    assert out == target
    # Confirm yt-dlp got asked for audio-only output.
    assert captured["opts"].get("format", "").startswith("bestaudio")


def test_download_audio_returns_none_when_ytdlp_fails(tmp_path, monkeypatch):
    class _FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=True):
            from yt_dlp.utils import DownloadError
            raise DownloadError("403 Forbidden")
        def prepare_filename(self, info): return ""
    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    out = tiktok_brief._download_audio(
        "https://www.tiktok.com/@x/video/123", dest_dir=tmp_path,
    )
    assert out is None


def test_transcribe_audio_returns_text_from_mocked_whisper(tmp_path, monkeypatch):
    fake_audio = tmp_path / "audio.m4a"
    fake_audio.write_bytes(b"x")

    class _FakeSegment:
        def __init__(self, text): self.text = text
    class _FakeModel:
        def __init__(self, *a, **kw): pass
        def transcribe(self, path, **kw):
            segments = [
                _FakeSegment("Hello and welcome."),
                _FakeSegment(" Today we're talking about TikTok briefings."),
            ]
            info = type("Info", (), {"language": "en", "duration": 12.5})
            return iter(segments), info
    # Patch the cached model so the test doesn't try to load real whisper.
    monkeypatch.setattr(tiktok_brief, "_WHISPER_MODEL", _FakeModel())
    monkeypatch.setattr(tiktok_brief, "_get_whisper_model", lambda: _FakeModel())

    text, lang, duration = tiktok_brief._transcribe_audio(fake_audio)
    assert "Hello and welcome." in text
    assert "TikTok briefings" in text
    assert lang == "en"
    assert duration == 12.5


def test_transcribe_audio_returns_empty_on_silence(tmp_path, monkeypatch):
    """A music-only / silent TikTok produces no transcribed segments. The
    caller (vision step) is what carries that case."""
    fake_audio = tmp_path / "silent.m4a"
    fake_audio.write_bytes(b"x")
    class _FakeModel:
        def __init__(self, *a, **kw): pass
        def transcribe(self, path, **kw):
            info = type("Info", (), {"language": "en", "duration": 5.0})
            return iter([]), info
    monkeypatch.setattr(tiktok_brief, "_WHISPER_MODEL", _FakeModel())
    monkeypatch.setattr(tiktok_brief, "_get_whisper_model", lambda: _FakeModel())

    text, lang, duration = tiktok_brief._transcribe_audio(fake_audio)
    assert text == ""
    assert duration == 5.0


def test_extract_keyframes_invokes_ffmpeg_with_correct_timestamps(tmp_path, monkeypatch):
    """For a 20-second video, frames should be sampled at ~2s, 8s, 12s, 18s."""
    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"x")

    captured_calls = []
    def fake_run(cmd, **kw):
        captured_calls.append(cmd)
        # Simulate ffmpeg writing the requested output file. The output path
        # is the LAST positional arg in the command (after all flags). We
        # write to whatever absolute path ffmpeg was told to use.
        from pathlib import Path
        out_arg = cmd[-1]
        Path(out_arg).write_bytes(b"jpgdata")
        class _R:
            returncode = 0
            stderr = ""
        return _R()
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    paths = tiktok_brief._extract_keyframes(fake_video, duration_s=20.0, n=4)
    assert len(paths) == 4
    # ffmpeg called once per frame (4 calls).
    assert len(captured_calls) == 4
    # Each call uses -ss <timestamp>; timestamps should be at ~10/40/60/90% of duration.
    timestamps = []
    for cmd in captured_calls:
        ss_idx = cmd.index("-ss")
        timestamps.append(float(cmd[ss_idx + 1]))
    assert timestamps[0] == pytest.approx(2.0, abs=0.5)
    assert timestamps[1] == pytest.approx(8.0, abs=0.5)
    assert timestamps[2] == pytest.approx(12.0, abs=0.5)
    assert timestamps[3] == pytest.approx(18.0, abs=0.5)


def test_extract_keyframes_returns_empty_when_duration_zero(tmp_path):
    """Slideshow / 0-duration content has no video to sample."""
    fake = tmp_path / "v.mp4"
    fake.write_bytes(b"x")
    paths = tiktok_brief._extract_keyframes(fake, duration_s=0.0, n=4)
    assert paths == []


def test_describe_keyframes_returns_descriptions_from_mocked_llm(tmp_path, monkeypatch):
    """Each keyframe gets a 1-2 sentence description from gpt-4o-mini vision."""
    frame1 = tmp_path / "f1.jpg"
    frame1.write_bytes(b"\xff\xd8\xff\xe0fake")
    frame2 = tmp_path / "f2.jpg"
    frame2.write_bytes(b"\xff\xd8\xff\xe0fake2")

    fake_responses = iter([
        "A person speaking to camera with text overlay 'How to brief'.",
        "Code editor showing a Python function being typed.",
    ])
    class _FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})
    class _FakeResp:
        def __init__(self, content): self.choices = [_FakeChoice(content)]
    class _FakeCompletions:
        def create(self, **kw):
            return _FakeResp(next(fake_responses))
    class _FakeChat:
        def __init__(self): self.completions = _FakeCompletions()
    class _FakeClient:
        def __init__(self, *a, **kw): self.chat = _FakeChat()
    monkeypatch.setattr("openai.OpenAI", _FakeClient)

    descriptions, cost = tiktok_brief._describe_keyframes([frame1, frame2])
    assert len(descriptions) == 2
    assert "person speaking" in descriptions[0]
    assert "Code editor" in descriptions[1]
    assert cost > 0  # nominal cost tracked
    assert cost < 0.10  # under the per-run cap


def test_describe_keyframes_returns_empty_when_no_frames():
    descriptions, cost = tiktok_brief._describe_keyframes([])
    assert descriptions == []
    assert cost == 0.0


def test_slugify_handles_basic_titles():
    assert tiktok_brief._slugify("How to use TikTok") == "how-to-use-tiktok"
    assert tiktok_brief._slugify("Hello, World!") == "hello-world"


def test_slugify_truncates_long_titles():
    long = "a" * 200
    assert len(tiktok_brief._slugify(long, max_len=60)) == 60


def test_slugify_falls_back_when_empty():
    assert tiktok_brief._slugify("") == "tiktok"
    assert tiktok_brief._slugify("!@#$%") == "tiktok"


def test_build_summary_prompt_includes_transcript_and_vision():
    meta = {
        "title": "How to use semantic HTML",
        "uploader": "jonathon.mj",
        "duration": 47,
        "webpage_url": "https://www.tiktok.com/@jonathon.mj/video/123",
    }
    prompt = tiktok_brief._build_summary_prompt(
        meta=meta,
        transcript="The transcript text goes here.",
        vision_notes=["Frame 1 shows person.", "Frame 2 shows code."],
    )
    assert "jonathon.mj" in prompt
    assert "47" in prompt or "00:47" in prompt
    assert "The transcript text goes here." in prompt
    assert "Frame 1 shows person." in prompt
    assert "TL;DR" in prompt or "tl;dr" in prompt.lower()


def test_write_brief_to_vault_writes_file_with_frontmatter(tmp_path, monkeypatch):
    """The writer mirrors youtube_brief: frontmatter + body, slugged filename
    in Brain/Knowledge/. Patch _brain_knowledge_dir so tests don't touch the real vault."""
    monkeypatch.setattr(tiktok_brief, "_brain_knowledge_dir", lambda: tmp_path / "Knowledge")
    meta = {
        "id": "7628009213884665119",
        "title": "How to brief",
        "uploader": "jonathon.mj",
        "duration": 47,
        "webpage_url": "https://www.tiktok.com/@jonathon.mj/video/7628009213884665119",
    }
    from datetime import datetime, timezone
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    path = tiktok_brief._write_brief_to_vault(
        meta=meta,
        briefing="## TL;DR\n\nA short summary.\n",
        transcript="The full transcript.",
        vision_notes=["Frame note 1.", "Frame note 2."],
        cost_usd=0.0008,
        now=now,
    )
    assert path is not None
    assert path.exists()
    assert path.name.startswith("2026-05-09 - briefing - tiktok - ")
    assert path.name.endswith(".md")
    content = path.read_text(encoding="utf-8")
    assert "type: briefing" in content
    assert "source: tiktok" in content
    assert "video_id: 7628009213884665119" in content
    assert "creator: jonathon.mj" in content
    assert "duration_s: 47" in content
    assert "## TL;DR" in content
    assert "## Transcript" in content
    assert "## Visual cues" in content
    assert "Frame note 1." in content


def test_tiktok_brief_url_tool_registered():
    """The ToolRegistry must expose `tiktok_brief_url` after import.

    Note: tests/conftest.py has an autouse fixture that clears all registries
    between tests, so we must reload the module to re-fire the @register
    decorator. This mirrors how the production process registers tools at
    import time.
    """
    import importlib
    from openjarvis.core.registry import ToolRegistry
    import openjarvis.tools.tiktok_brief as _tt
    importlib.reload(_tt)
    cls = ToolRegistry.get("tiktok_brief_url")
    assert cls is not None
    instance = cls()
    spec = instance.spec
    assert spec.name == "tiktok_brief_url"
    assert "tiktok" in spec.description.lower()
