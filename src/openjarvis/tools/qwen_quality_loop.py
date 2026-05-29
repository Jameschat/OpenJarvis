from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
import re


_TOOL_BLOCK_RE = re.compile(
    r"(```qwen_tool_requests\b|<qwen_tool_requests>)",
    re.IGNORECASE,
)


@dataclass
class QualityAssessment:
    score: int
    issues: list[str] = field(default_factory=list)
    needs_retry: bool = False
    needs_escalation: bool = False

    @property
    def passed(self) -> bool:
        return not self.needs_retry and not self.needs_escalation and self.score >= 75


def assess_qwen_output(
    content: str,
    task_prompt: str,
    *,
    had_tool_results: bool = False,
    after_retry: bool = False,
) -> QualityAssessment:
    text = (content or "").strip()
    task = (task_prompt or "").lower()
    issues: list[str] = []

    if not text:
        issues.append("empty response")
    if _TOOL_BLOCK_RE.search(text):
        issues.append("raw qwen_tool_requests block leaked into final answer")
    if had_tool_results and re.search(r"\b(can't|cannot|do not|don't)\s+(access|see|search|read)\b", text, re.I):
        issues.append("denied access despite provided tool results")

    complex_task = _looks_complex(task)
    if complex_task and len(text) < 240:
        issues.append("answer is too thin for a complex task")
    if complex_task and not _has_any_heading(text, ("assumption", "assumptions")):
        issues.append("missing assumptions")
    if complex_task and not _has_any_heading(text, ("verification", "checked", "evidence")):
        issues.append("missing verification/evidence")
    if complex_task and not _has_any_heading(text, ("next", "recommend", "action", "steps")):
        issues.append("missing next action")

    score = max(0, 100 - (18 * len(issues)))
    needs_retry = bool(issues) and not after_retry
    needs_escalation = bool(issues) and after_retry
    return QualityAssessment(score=score, issues=issues, needs_retry=needs_retry, needs_escalation=needs_escalation)


def build_reflexion_prompt(original_prompt: str, draft: str) -> str:
    """Self-critique (reflexion) prompt: make Qwen find its own defects, then
    rewrite. Distinct from the heuristic gate — catches a class of errors the
    regex checks cannot see. v1.2 of the Qwen autonomy upgrade."""
    return (
        "Before finalising, critique your own draft against the task.\n\n"
        "Original task:\n"
        f"{(original_prompt or '').strip()}\n\n"
        "Your draft:\n"
        f"{(draft or '').strip()}\n\n"
        "Step 1: list the concrete defects, gaps, unverified claims, wrong "
        "assumptions, or missing steps in the draft as a short bullet list.\n"
        "Step 2: produce a corrected, complete final answer that fixes every "
        "defect you listed. Output only the corrected final answer."
    )


def is_complex(task_prompt: str) -> bool:
    """Public predicate for 'this task warrants the heavier autonomy loop'."""
    return _looks_complex((task_prompt or "").lower())


def revise_until_pass(
    content: str,
    task_prompt: str,
    *,
    redraft: Callable[[str], str],
    had_tool_results: bool = False,
    max_revisions: int = 3,
    base_prompt: str = "",
) -> tuple[str, QualityAssessment, list[dict]]:
    """Iterate-until-pass revision loop (v1.1 of the Qwen autonomy upgrade).

    Repeatedly assess the draft and, while it fails and budget remains, feed the
    *specific* quality-gate issues back to ``redraft`` (a callable that takes a
    revision prompt and returns a new draft). Stops on pass or when the revision
    budget is spent; the final assessment on a spent budget reads as
    escalation-required. Returns ``(final_content, final_assessment, rounds)``
    where ``rounds`` is an audit trail of each revision.

    Pure except for ``redraft`` — fully unit-testable with a fake redraft.
    """
    max_revisions = max(0, int(max_revisions))
    assessment = assess_qwen_output(content, task_prompt, had_tool_results=had_tool_results)
    rounds: list[dict] = []
    revisions = 0
    while not assessment.passed and revisions < max_revisions:
        revisions += 1
        is_last = revisions >= max_revisions
        revision_prompt = "\n\n".join(
            part
            for part in (base_prompt, build_revision_prompt(task_prompt, content, assessment))
            if part
        )
        revised = redraft(revision_prompt)
        if revised and revised.strip():
            content = revised
        assessment = assess_qwen_output(
            content,
            task_prompt,
            had_tool_results=had_tool_results,
            after_retry=is_last,
        )
        rounds.append(
            {
                "revision": revisions,
                "score": assessment.score,
                "issues": list(assessment.issues),
                "passed": assessment.passed,
            }
        )
    return content, assessment, rounds


def build_revision_prompt(original_prompt: str, draft: str, assessment: QualityAssessment) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in assessment.issues) or "- no listed issues"
    return (
        "Your previous answer failed Jarvis quality checks.\n\n"
        "Original task:\n"
        f"{original_prompt.strip()}\n\n"
        "Quality issues:\n"
        f"{issue_lines}\n\n"
        "Previous draft:\n"
        f"{draft.strip()}\n\n"
        "Revise now. Use any provided tool results, remove raw tool request blocks, "
        "state assumptions, include evidence or verification, and provide concrete next actions. "
        "If the task cannot be completed safely by local Qwen, say what escalation is needed."
    )


def format_quality_report(assessment: QualityAssessment) -> str:
    status = "passed" if assessment.passed else "escalation-required" if assessment.needs_escalation else "reviewed"
    issues = "\n".join(f"- {issue}" for issue in assessment.issues) or "- none"
    return (
        "## Qwen Quality Gate\n\n"
        f"Status: {status}\n\n"
        f"Score: {assessment.score}\n\n"
        f"Issues:\n{issues}\n"
    )


def _looks_complex(task: str) -> bool:
    keywords = (
        "build",
        "create",
        "plan",
        "project",
        "code",
        "app",
        "website",
        "research",
        "backtest",
        "debug",
        "fix",
        "compare",
        "workflow",
        "strategy",
        "implement",
    )
    return any(keyword in task for keyword in keywords)


def _has_any_heading(text: str, words: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(word in lower for word in words)
