from openjarvis.tools import qwen_verify_loop


def test_verify_passes_first_try_no_revision():
    redrafts = []

    def run_check(_root, _files):
        return {"ok": True, "passed": True, "exit_code": 0}

    files, verdict, rounds = qwen_verify_loop.verify_and_revise(
        [{"path": "app.py", "content": "ok"}],
        "/proj",
        redraft_proposal=lambda fb: redrafts.append(fb) or [],
        run_check=run_check,
        max_rounds=3,
    )
    assert verdict == {"available": True, "verified": True, "rounds": 0, "exit_code": 0}
    assert rounds == []
    assert redrafts == []  # never asked to revise


def test_verify_fixes_within_budget():
    state = {"n": 0}

    def run_check(_root, files):
        # fails until the proposal content is "fixed"
        passed = files and files[0].get("content") == "fixed"
        state["n"] += 1
        return {
            "ok": True,
            "passed": passed,
            "exit_code": 0 if passed else 1,
            "stderr": "SyntaxError: bad",
            "command": ["python", "-m", "py_compile", "app.py"],
        }

    def redraft(_feedback):
        return [{"path": "app.py", "content": "fixed"}]

    files, verdict, rounds = qwen_verify_loop.verify_and_revise(
        [{"path": "app.py", "content": "broken"}],
        "/proj",
        redraft_proposal=redraft,
        run_check=run_check,
        max_rounds=3,
    )
    assert verdict["verified"] is True
    assert files[0]["content"] == "fixed"
    assert len(rounds) == 1
    assert "SyntaxError" in rounds[0]["stderr"]


def test_verify_exhausts_budget_when_never_fixed():
    def run_check(_root, _files):
        return {"ok": True, "passed": False, "exit_code": 1, "stderr": "still broken"}

    files, verdict, rounds = qwen_verify_loop.verify_and_revise(
        [{"path": "app.py", "content": "broken"}],
        "/proj",
        redraft_proposal=lambda fb: [{"path": "app.py", "content": "still broken"}],
        run_check=run_check,
        max_rounds=2,
    )
    assert verdict["verified"] is False
    assert verdict["exhausted"] is True
    assert verdict["rounds"] == 2
    assert len(rounds) == 2


def test_verify_unavailable_when_no_declared_check():
    def run_check(_root, _files):
        return {"ok": False, "error": "no verify command declared; create .jarvis-verify.txt"}

    calls = []
    files, verdict, rounds = qwen_verify_loop.verify_and_revise(
        [{"path": "app.py", "content": "x"}],
        "/proj",
        redraft_proposal=lambda fb: calls.append(fb),
        run_check=run_check,
        max_rounds=3,
    )
    assert verdict["available"] is False
    assert verdict["verified"] is False
    assert calls == []  # cannot verify -> never asks for a revision


def test_build_verify_feedback_includes_real_error():
    fb = qwen_verify_loop.build_verify_feedback(
        {
            "command": ["python", "-m", "py_compile", "app.py"],
            "exit_code": 1,
            "stderr": "SyntaxError: invalid syntax",
        },
        [{"path": "app.py", "content": "def f(:"}],
        base_prompt="BASE",
    )
    assert "BASE" in fb
    assert "SyntaxError: invalid syntax" in fb
    assert "app.py" in fb
    assert "corrected repo_patch_proposal" in fb
