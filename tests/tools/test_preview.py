from pathlib import Path

from openjarvis.tools.preview import PreviewStartTool


def test_serves_a_directory_and_returns_url(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    res = PreviewStartTool().execute(path=str(tmp_path))
    assert res.success
    assert str(res.metadata["url"]).startswith("http://127.0.0.1:")
    assert res.metadata["port"]


def test_rejects_missing_directory(tmp_path: Path):
    res = PreviewStartTool().execute(path=str(tmp_path / "nope"))
    assert not res.success


def test_respects_allowed_dirs(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    res = PreviewStartTool(allowed_dirs=[str(sub)]).execute(path=str(tmp_path))
    assert not res.success
    assert "denied" in res.content.lower()


def test_requires_path():
    res = PreviewStartTool().execute()
    assert not res.success
