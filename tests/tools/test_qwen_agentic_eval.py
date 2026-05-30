import sys
from pathlib import Path

from openjarvis.tools import qwen_agentic_eval
from openjarvis.tools.qwen_agentic_eval import AgenticCase

PY = sys.executable


def _compile_case():
    return AgenticCase(
        id="fix",
        prompt="fix the syntax error in app.py",
        category="code-fix",
        fixture={
            "files": {"app.py": "def greet(name)\n    return name\n"},  # broken
            "verify": f"{PY} -m py_compile app.py",
        },
    )


def test_setup_case_project_writes_files_and_verify(tmp_path):
    proj = qwen_agentic_eval.setup_case_project(_compile_case(), tmp_path)
    assert (proj / "app.py").exists()
    assert (proj / ".jarvis-verify.txt").read_text(encoding="utf-8").strip().endswith("py_compile app.py")


def test_first_pass_passes_when_proposal_fixes_compile():
    # propose a syntactically valid app.py
    def propose(_prompt, _proj):
        return [{"path": "app.py", "content": "def greet(name):\n    return name\n"}]

    report = qwen_agentic_eval.run_agentic_eval(
        [_compile_case()], propose=propose, max_revise=0, clock=lambda: 0.0
    )
    assert report.pass_rate == 1.0
    assert report.results[0].passed is True


def test_first_pass_fails_when_proposal_still_broken():
    def propose(_prompt, _proj):
        return [{"path": "app.py", "content": "def greet(name)\n    return name\n"}]  # still broken

    report = qwen_agentic_eval.run_agentic_eval(
        [_compile_case()], propose=propose, max_revise=0, clock=lambda: 0.0
    )
    assert report.results[0].passed is False
    assert "exit=" in report.results[0].detail


def test_no_proposal_is_a_failed_case():
    report = qwen_agentic_eval.run_agentic_eval(
        [_compile_case()], propose=lambda p, proj: None, max_revise=0, clock=lambda: 0.0
    )
    assert report.results[0].passed is False
    assert "no proposal" in report.results[0].detail


def test_self_correction_loop_fixes_then_passes():
    state = {"n": 0}

    def propose(_prompt, _proj):
        state["n"] += 1
        if state["n"] == 1:
            return [{"path": "app.py", "content": "def greet(name)\n    return name\n"}]  # broken first
        return [{"path": "app.py", "content": "def greet(name):\n    return name\n"}]  # fixed on revise

    report = qwen_agentic_eval.run_agentic_eval(
        [_compile_case()], propose=propose, max_revise=2, clock=lambda: 0.0
    )
    assert report.results[0].passed is True
    assert "verified=True" in report.results[0].detail


def test_pytest_case_runs_real_check():
    case = AgenticCase(
        id="impl",
        prompt="implement add",
        fixture={
            "files": {
                "app.py": "def add(a, b):\n    raise NotImplementedError\n",
                "test_app.py": "from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            },
            "verify": f"{PY} -m pytest -q",
        },
    )

    def propose(_prompt, _proj):
        return [{"path": "app.py", "content": "def add(a, b):\n    return a + b\n"}]

    report = qwen_agentic_eval.run_agentic_eval([case], propose=propose, max_revise=0, clock=lambda: 0.0)
    assert report.results[0].passed is True


def test_load_seed_agentic_cases():
    seed = Path(__file__).resolve().parents[2] / "evals" / "qwen" / "agentic-cases.json"
    cases = qwen_agentic_eval.load_agentic_cases(seed)
    assert len(cases) >= 3
    assert all(c.fixture.get("verify") for c in cases)
