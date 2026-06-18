from pathlib import Path

from openjarvis.tools.file_edit import EditTool


def test_applies_unique_edit_and_returns_diff(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    tool = EditTool()
    res = tool.execute(path=str(f), old_string="return 1", new_string="return 2")
    assert res.success
    assert f.read_text(encoding="utf-8") == "def foo():\n    return 2\n"
    assert "return 2" in res.metadata["diff"]
    assert res.metadata["added"] >= 1
    assert res.metadata["removed"] >= 1
    assert res.metadata["path"].endswith("sample.py")
    assert res.metadata["edit_id"]


def test_missing_old_string_is_an_error(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    res = EditTool().execute(path=str(f), old_string="nope", new_string="x")
    assert not res.success
    assert "not found" in res.content.lower()


def test_ambiguous_old_string_requires_replace_all(tmp_path: Path):
    f = tmp_path / "b.txt"
    f.write_text("x\nx\n", encoding="utf-8")
    res = EditTool().execute(path=str(f), old_string="x", new_string="y")
    assert not res.success
    assert "unique" in res.content.lower() or "multiple" in res.content.lower()
    # replace_all resolves it
    res2 = EditTool().execute(path=str(f), old_string="x", new_string="y", replace_all=True)
    assert res2.success
    assert f.read_text(encoding="utf-8") == "y\ny\n"


def test_missing_file_is_an_error(tmp_path: Path):
    res = EditTool().execute(path=str(tmp_path / "nope.txt"), old_string="a", new_string="b")
    assert not res.success


def test_sensitive_file_blocked(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text("SECRET=1", encoding="utf-8")
    res = EditTool().execute(path=str(f), old_string="1", new_string="2")
    assert not res.success
    assert "sensitive" in res.content.lower() or "denied" in res.content.lower()
