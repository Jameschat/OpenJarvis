# tests/tiktok/test_video_gen.py
import json, pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

def _mock_urlopen(response_body: dict):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_body).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp

def test_submit_job_returns_task_id():
    from openjarvis.tiktok.video_gen import submit_job
    mock_resp = _mock_urlopen({"code": 0, "data": {"task_id": "abc123"}})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        task_id = submit_job("narration text", "cinematic visuals", "key", "secret")
    assert task_id == "abc123"

def test_submit_job_raises_on_api_error():
    from openjarvis.tiktok.video_gen import submit_job, KlingError
    mock_resp = _mock_urlopen({"code": 1, "message": "invalid key"})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(KlingError, match="invalid key"):
            submit_job("script", "visual", "bad_key", "bad_secret")

def test_poll_job_returns_url_on_success():
    from openjarvis.tiktok.video_gen import poll_job
    mock_resp = _mock_urlopen({
        "data": {
            "task_status": "succeed",
            "task_result": {"videos": [{"url": "https://cdn.kling.ai/video.mp4"}]}
        }
    })
    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch("time.sleep"):
        url = poll_job("task123", "key", "secret", max_wait=60)
    assert url == "https://cdn.kling.ai/video.mp4"

def test_poll_job_raises_on_failure():
    from openjarvis.tiktok.video_gen import poll_job, KlingError
    mock_resp = _mock_urlopen({"data": {"task_status": "failed", "task_status_msg": "quota exceeded"}})
    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch("time.sleep"):
        with pytest.raises(KlingError, match="quota exceeded"):
            poll_job("t", "k", "s", max_wait=60)

def test_download_video(tmp_path):
    from openjarvis.tiktok.video_gen import download_video
    dest = tmp_path / "video.mp4"
    with patch("urllib.request.urlretrieve") as mock_dl:
        result = download_video("https://example.com/video.mp4", dest)
    mock_dl.assert_called_once_with("https://example.com/video.mp4", str(dest))
    assert result == dest
