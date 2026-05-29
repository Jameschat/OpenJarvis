from pathlib import Path


def test_traces_ignore_is_anchored_for_packaging() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    gitignore_lines = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/traces/" in gitignore_lines
    assert "traces/" not in gitignore_lines


def test_local_secret_and_tool_ignores_are_kept() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    gitignore_lines = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "jarvis.bat" in gitignore_lines
    assert ".codegraph/" in gitignore_lines
