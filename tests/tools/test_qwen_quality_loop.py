def test_quality_loop_flags_raw_tool_request_leak():
    from openjarvis.tools import qwen_quality_loop

    assessment = qwen_quality_loop.assess_qwen_output(
        "<qwen_tool_requests>{}</qwen_tool_requests>",
        "research the Networx project",
    )

    assert assessment.needs_retry is True
    assert "raw qwen_tool_requests block leaked into final answer" in assessment.issues


def test_quality_loop_passes_useful_complex_answer():
    from openjarvis.tools import qwen_quality_loop

    assessment = qwen_quality_loop.assess_qwen_output(
        "Assumptions: local context is current and no external edits are needed.\n\n"
        "Verification: checked the supplied task and included an evidence path.\n\n"
        "Next actions: retrieve memory, build the plan, run focused tests, and escalate only if the local path fails. "
        "This gives Jarvis a concrete response loop instead of a vague acknowledgement.",
        "plan a project workflow",
    )

    assert assessment.passed is True
    assert assessment.issues == []


_GOOD_ANSWER = (
    "Assumptions: local context is current and no external edits are needed.\n\n"
    "Verification: checked the supplied task and ran the focused path as evidence.\n\n"
    "Next actions: build the plan, run focused tests, escalate only if the local "
    "path fails. This gives Jarvis a concrete response loop instead of a vague ack."
)


def test_is_complex_predicate():
    from openjarvis.tools import qwen_quality_loop

    assert qwen_quality_loop.is_complex("build the westhill website") is True
    assert qwen_quality_loop.is_complex("hello there") is False


def test_explanatory_question_does_not_require_structure_headings():
    # Phase 9 #6: a correct, substantial answer to an EXPLANATION / Q&A task
    # must not be penalised for lacking assumptions / verification / next-action
    # headers. Those are deliverable-engineering markers, not answer quality — a
    # right prose answer used to score 46 (100 - 3*18) and auto-escalate.
    from openjarvis.tools import qwen_quality_loop

    answer = (
        "Jarvis routes a request to the local Qwen MTP lane first because it is "
        "free and low-latency; the remote 35B lane is reserved for prompts that "
        "exceed the local context window or that the quality gate escalates. The "
        "routing choice is made in agent_runner before dispatch, using the live "
        "profile and the prompt size, so the bigger remote model is only used when "
        "the local lane genuinely cannot serve the request."
    )
    assessment = qwen_quality_loop.assess_qwen_output(
        answer, "compare the local and remote Qwen routing lanes"
    )

    assert assessment.passed is True
    assert assessment.issues == []


def test_deliverable_task_still_requires_structure_headings():
    # Regression guard for Phase 9 #6: build / implement tasks DO still need
    # assumptions / verification / next-action — the fix must not weaken the gate
    # for tasks that actually produce or change an artifact.
    from openjarvis.tools import qwen_quality_loop

    answer = (
        "I rewrote the lane routing function so it picks the local Qwen lane "
        "first and only falls through to the remote 35B lane when the prompt "
        "overflows the local context window. The edit lives in agent_runner near "
        "the dispatch path and reads consistently with the surrounding code."
    )
    assessment = qwen_quality_loop.assess_qwen_output(
        answer, "implement the lane routing change"
    )

    assert assessment.passed is False
    assert "missing assumptions" in assessment.issues
    assert "missing verification/evidence" in assessment.issues
    assert "missing next action" in assessment.issues


def test_revise_until_pass_returns_immediately_when_first_draft_passes():
    from openjarvis.tools import qwen_quality_loop

    calls = []

    def redraft(_prompt):
        calls.append(_prompt)
        return "should not be called"

    content, assessment, rounds = qwen_quality_loop.revise_until_pass(
        _GOOD_ANSWER, "plan a project workflow", redraft=redraft, max_revisions=3
    )

    assert assessment.passed is True
    assert rounds == []
    assert calls == []  # no revision needed


def test_revise_until_pass_fixes_within_budget():
    from openjarvis.tools import qwen_quality_loop

    attempts = {"n": 0}

    def redraft(_prompt):
        # First revision still thin; second returns a good answer.
        attempts["n"] += 1
        return "still thin" if attempts["n"] < 2 else _GOOD_ANSWER

    content, assessment, rounds = qwen_quality_loop.revise_until_pass(
        "thin draft", "build a project", redraft=redraft, max_revisions=3
    )

    assert assessment.passed is True
    assert content == _GOOD_ANSWER
    assert len(rounds) == 2
    assert rounds[-1]["passed"] is True


def test_revise_until_pass_escalates_when_budget_exhausted():
    from openjarvis.tools import qwen_quality_loop

    def redraft(_prompt):
        return "still thin and useless"

    content, assessment, rounds = qwen_quality_loop.revise_until_pass(
        "thin draft", "build a project", redraft=redraft, max_revisions=2
    )

    assert assessment.passed is False
    assert assessment.needs_escalation is True
    assert len(rounds) == 2


def test_revise_until_pass_keeps_prior_content_when_redraft_blank():
    from openjarvis.tools import qwen_quality_loop

    def redraft(_prompt):
        return "   "  # blank redraft must not clobber the prior draft

    content, assessment, rounds = qwen_quality_loop.revise_until_pass(
        "thin draft", "build a project", redraft=redraft, max_revisions=1
    )

    assert content == "thin draft"
    assert rounds[-1]["revision"] == 1


def test_build_reflexion_prompt_contains_critique_then_rewrite():
    from openjarvis.tools import qwen_quality_loop

    prompt = qwen_quality_loop.build_reflexion_prompt("build X", "my draft")

    assert "critique" in prompt.lower()
    assert "my draft" in prompt
    assert "corrected" in prompt.lower()
