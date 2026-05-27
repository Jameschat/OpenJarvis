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
