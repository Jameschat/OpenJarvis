"""Qwen accuracy eval harness (distribution readiness gate #1).

Turns "is Qwen reliable?" into a number you can track over time: run a fixed set
of cases through Qwen, grade each deterministically, and report a pass-rate
(overall + per category). Repeatable, so you can see whether a model/prompt/loop
change actually helped.

Design: the core (load cases -> run -> grade -> aggregate -> report) is pure and
fully unit-tested with an injected ``run_task``. The real model call lives in
``default_run_task`` and is only used by the CLI.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openjarvis.tools import qwen_quality_loop


@dataclass
class EvalCase:
    id: str
    prompt: str
    category: str = "general"
    grader: str = "contains"
    expect: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    id: str
    category: str
    passed: bool
    detail: str
    output: str = ""
    duration_s: float = 0.0


@dataclass
class EvalReport:
    results: list[CaseResult]
    label: str = "qwen-accuracy"

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    def by_category(self) -> dict[str, dict[str, Any]]:
        cats: dict[str, dict[str, Any]] = {}
        for r in self.results:
            c = cats.setdefault(r.category, {"total": 0, "passed": 0})
            c["total"] += 1
            c["passed"] += int(r.passed)
        for c in cats.values():
            c["pass_rate"] = (c["passed"] / c["total"]) if c["total"] else 0.0
        return cats


# ----- graders (each returns {"passed": bool, "detail": str}) -----

def grade_contains(output: str, case: EvalCase) -> dict[str, Any]:
    text = (output or "").lower()
    must = [str(s) for s in case.expect.get("contains", [])]
    forbid = [str(s) for s in case.expect.get("not_contains", [])]
    missing = [s for s in must if s.lower() not in text]
    present_forbidden = [s for s in forbid if s.lower() in text]
    passed = not missing and not present_forbidden
    detail = []
    if missing:
        detail.append("missing: " + ", ".join(missing))
    if present_forbidden:
        detail.append("forbidden present: " + ", ".join(present_forbidden))
    return {"passed": passed, "detail": "; ".join(detail) or "ok"}


def grade_quality(output: str, case: EvalCase) -> dict[str, Any]:
    assessment = qwen_quality_loop.assess_qwen_output(output, case.prompt)
    return {
        "passed": assessment.passed,
        "detail": "ok" if assessment.passed else "; ".join(assessment.issues) or "below quality bar",
    }


GRADERS: dict[str, Callable[[str, EvalCase], dict[str, Any]]] = {
    "contains": grade_contains,
    "quality": grade_quality,
}


# ----- core -----

def load_cases(path: str | Path) -> list[EvalCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    cases = data["cases"] if isinstance(data, dict) else data
    out: list[EvalCase] = []
    for raw in cases:
        out.append(
            EvalCase(
                id=str(raw["id"]),
                prompt=str(raw["prompt"]),
                category=str(raw.get("category", "general")),
                grader=str(raw.get("grader", "contains")),
                expect=raw.get("expect", {}) or {},
            )
        )
    return out


def run_eval(
    cases: list[EvalCase],
    *,
    run_task: Callable[[EvalCase], str],
    clock: Callable[[], float] = time.monotonic,
    label: str = "qwen-accuracy",
) -> EvalReport:
    """Run every case through ``run_task`` and grade it. ``run_task`` raising is
    recorded as a failed case (not a crash), so one bad case doesn't sink the run."""
    results: list[CaseResult] = []
    for case in cases:
        t0 = clock()
        error = None
        output = ""
        try:
            output = run_task(case) or ""
        except Exception as exc:  # noqa: BLE001 - a failing task is a failed case
            error = str(exc)
        duration = clock() - t0

        if error is not None:
            results.append(
                CaseResult(case.id, case.category, False, f"run error: {error}", "", duration)
            )
            continue
        grader = GRADERS.get(case.grader)
        if grader is None:
            results.append(
                CaseResult(case.id, case.category, False, f"unknown grader: {case.grader}", output, duration)
            )
            continue
        verdict = grader(output, case)
        results.append(
            CaseResult(
                case.id,
                case.category,
                bool(verdict.get("passed")),
                str(verdict.get("detail", "")),
                output,
                duration,
            )
        )
    return EvalReport(results=results, label=label)


def format_report_markdown(report: EvalReport, *, when: str = "") -> str:
    lines = [
        f"# Qwen Accuracy Eval — {report.label}",
        "",
        f"When: {when or 'n/a'}",
        "",
        f"**Pass rate: {report.passed}/{report.total} = {report.pass_rate:.0%}**",
        "",
        "## By category",
        "",
        "| Category | Pass | Total | Rate |",
        "| --- | --- | --- | --- |",
    ]
    for cat, stats in sorted(report.by_category().items()):
        lines.append(
            f"| {cat} | {stats['passed']} | {stats['total']} | {stats['pass_rate']:.0%} |"
        )
    lines += ["", "## Cases", "", "| Case | Category | Result | Detail |", "| --- | --- | --- | --- |"]
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        detail = (r.detail or "").replace("|", "/")[:120]
        lines.append(f"| {r.id} | {r.category} | {mark} | {detail} |")
    return "\n".join(lines) + "\n"


# ----- real model adapter (CLI only) -----

def default_run_task(case: EvalCase, *, model: str = "qwen3.6-27b-local", timeout: int = 120) -> str:
    """Single direct Qwen call for a case (first-pass accuracy). Lazy imports so
    importing this module never requires the OpenAI client."""
    import os

    from openai import OpenAI

    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:4000")
    client = OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "sk-noop"))
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a precise local assistant. Answer the task directly."},
            {"role": "user", "content": case.prompt},
        ],
        max_tokens=700,
        timeout=timeout,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    if not resp.choices:
        return ""
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", "") or "").strip()


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(prog="qwen-eval", description="Run the Qwen accuracy eval set.")
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).resolve().parents[3] / "evals" / "qwen" / "cases.json"),
        help="path to the eval cases JSON",
    )
    parser.add_argument("--model", default="qwen3.6-27b-local")
    parser.add_argument("--out", default="", help="optional path to write the markdown report")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    report = run_eval(cases, run_task=lambda c: default_run_task(c, model=args.model))
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    md = format_report_markdown(report, when=when)
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    return 0 if report.pass_rate >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
