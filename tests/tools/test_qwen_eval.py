from pathlib import Path

from openjarvis.tools import qwen_eval
from openjarvis.tools.qwen_eval import EvalCase


def test_grade_contains_pass_and_fail():
    case = EvalCase(id="c1", prompt="p", grader="contains",
                    expect={"contains": ["def add", "return"], "not_contains": ["I cannot"]})
    assert qwen_eval.grade_contains("def add(a,b):\n  return a+b", case)["passed"] is True
    miss = qwen_eval.grade_contains("def add(a,b): pass", case)
    assert miss["passed"] is False
    assert "return" in miss["detail"]
    forbidden = qwen_eval.grade_contains("I cannot do that. def add return", case)
    assert forbidden["passed"] is False
    assert "forbidden" in forbidden["detail"]


def test_grade_contains_is_case_insensitive():
    case = EvalCase(id="c", prompt="p", expect={"contains": ["Hypertext Transfer Protocol"]})
    assert qwen_eval.grade_contains("http = hypertext transfer protocol", case)["passed"] is True


def test_run_eval_aggregates_pass_rate_and_categories():
    cases = [
        EvalCase(id="a", prompt="p", category="code", expect={"contains": ["x"]}),
        EvalCase(id="b", prompt="p", category="code", expect={"contains": ["zzz"]}),
        EvalCase(id="c", prompt="p", category="factual", expect={"contains": ["y"]}),
    ]
    outputs = {"a": "has x", "b": "no match", "c": "has y"}
    report = qwen_eval.run_eval(cases, run_task=lambda c: outputs[c.id], clock=lambda: 0.0)

    assert report.total == 3
    assert report.passed == 2
    assert abs(report.pass_rate - 2 / 3) < 1e-9
    cats = report.by_category()
    assert cats["code"] == {"total": 2, "passed": 1, "pass_rate": 0.5}
    assert cats["factual"]["pass_rate"] == 1.0


def test_run_eval_records_run_error_as_failed_case():
    def boom(_case):
        raise RuntimeError("model timeout")

    cases = [EvalCase(id="a", prompt="p", expect={"contains": ["x"]})]
    report = qwen_eval.run_eval(cases, run_task=boom, clock=lambda: 0.0)
    assert report.passed == 0
    assert "run error: model timeout" in report.results[0].detail


def test_run_eval_unknown_grader_fails_cleanly():
    cases = [EvalCase(id="a", prompt="p", grader="nope")]
    report = qwen_eval.run_eval(cases, run_task=lambda c: "out", clock=lambda: 0.0)
    assert report.passed == 0
    assert "unknown grader" in report.results[0].detail


def test_load_seed_cases_file():
    seed = Path(__file__).resolve().parents[2] / "evals" / "qwen" / "cases.json"
    cases = qwen_eval.load_cases(seed)
    assert len(cases) >= 5
    ids = {c.id for c in cases}
    assert "code-add-fn" in ids
    assert all(c.grader in qwen_eval.GRADERS for c in cases)


def test_format_report_markdown_has_headline_and_tables():
    cases = [
        EvalCase(id="a", prompt="p", category="code", expect={"contains": ["x"]}),
        EvalCase(id="b", prompt="p", category="code", expect={"contains": ["zzz"]}),
    ]
    report = qwen_eval.run_eval(cases, run_task=lambda c: "has x", clock=lambda: 0.0)
    md = qwen_eval.format_report_markdown(report, when="2026-05-30")
    assert "Pass rate: 1/2 = 50%" in md
    assert "| code |" in md
    assert "| a | code | PASS |" in md
    assert "| b | code | FAIL |" in md
