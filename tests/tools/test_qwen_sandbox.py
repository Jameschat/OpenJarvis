import sys


def _write_repo(tmp_path, verify_cmd: str, app_src: str):
    (tmp_path / ".jarvis-verify.txt").write_text(verify_cmd + "\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(app_src, encoding="utf-8")


def test_sandbox_refuses_without_declared_command(tmp_path):
    from openjarvis.tools import qwen_sandbox

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = qwen_sandbox.run_check_in_sandbox(
        tmp_path, [{"path": "app.py", "content": "x = 1\n"}]
    )
    assert result["ok"] is False
    assert "no verify command declared" in result["error"]


def test_sandbox_blocks_non_allowlisted_executable(tmp_path):
    from openjarvis.tools import qwen_sandbox

    _write_repo(tmp_path, "rm -rf /", "x = 1\n")
    result = qwen_sandbox.run_check_in_sandbox(
        tmp_path, [{"path": "app.py", "content": "x = 1\n"}]
    )
    assert result["ok"] is False
    assert result["blocked"] is True
    assert "not allow-listed" in result["error"]


def test_sandbox_blocks_unsafe_proposed_path(tmp_path):
    from openjarvis.tools import qwen_sandbox

    _write_repo(tmp_path, f"{sys.executable} -m py_compile app.py", "x = 1\n")
    result = qwen_sandbox.run_check_in_sandbox(
        tmp_path, [{"path": "../escape.py", "content": "x = 1\n"}]
    )
    assert result["ok"] is False
    assert result["blocked"] is True


def test_sandbox_passes_when_proposed_fix_compiles(tmp_path):
    from openjarvis.tools import qwen_sandbox

    # Repo file is broken; the proposed file fixes it.
    _write_repo(tmp_path, f"{sys.executable} -m py_compile app.py", "def broken(:\n")
    result = qwen_sandbox.run_check_in_sandbox(
        tmp_path,
        [{"path": "app.py", "content": "def fixed():\n    return 1\n"}],
        timeout=60,
    )
    assert result["ok"] is True
    assert result["passed"] is True
    assert result["exit_code"] == 0
    # Real file untouched by the sandbox run.
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "def broken(:\n"


def test_sandbox_reports_real_failure(tmp_path):
    from openjarvis.tools import qwen_sandbox

    _write_repo(tmp_path, f"{sys.executable} -m py_compile app.py", "x = 1\n")
    result = qwen_sandbox.run_check_in_sandbox(
        tmp_path,
        [{"path": "app.py", "content": "def still_broken(:\n"}],
        timeout=60,
    )
    assert result["ok"] is True
    assert result["passed"] is False
    assert result["exit_code"] != 0
    assert result["stderr"]  # the real compiler error is fed back


def test_sandbox_load_verify_command_skips_comments(tmp_path):
    from openjarvis.tools import qwen_sandbox

    (tmp_path / ".jarvis-verify.txt").write_text(
        "# how to verify this project\npytest -q\n", encoding="utf-8"
    )
    assert qwen_sandbox.load_verify_command(tmp_path) == ["pytest", "-q"]
