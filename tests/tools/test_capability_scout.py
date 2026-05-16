from openjarvis.tools import capability_scout


def test_score_candidate_prefers_active_relevant_repo():
    gap = {
        "capability": "browser automation for JS-heavy websites",
        "context": "fetch_url cannot inspect interactive pages",
    }
    candidate = {
        "name": "browser-use",
        "description": "AI agent browser automation for JS-heavy websites",
        "stars": 25000,
        "pushed_at": "2026-05-01T10:00:00Z",
        "html_url": "https://github.com/browser-use/browser-use",
    }

    scored = capability_scout.score_candidate(gap, candidate, now_iso="2026-05-07T12:00:00Z")

    assert scored["score"] >= 80
    assert scored["recommendation"] == "prototype first"
    assert "recent activity" in " ".join(scored["reasons"])


def test_build_scout_report_ranks_candidates():
    gap = {"capability": "voice cloning", "trigger": "operator asked for custom TTS"}
    candidates = [
        {"name": "stale", "description": "old TTS", "stars": 10, "pushed_at": "2023-01-01T00:00:00Z", "html_url": "https://github.com/x/stale"},
        {"name": "active-tts", "description": "voice cloning TTS API", "stars": 9000, "pushed_at": "2026-05-06T00:00:00Z", "html_url": "https://github.com/x/active"},
    ]

    report = capability_scout.build_scout_report(
        gap=gap,
        candidates=candidates,
        date_str="2026-05-07",
        now_iso="2026-05-07T12:00:00Z",
    )

    assert "# Capability scout - voice cloning" in report
    assert report.index("active-tts") < report.index("stale")
    assert "## Recommendation" in report


def test_capability_scout_agent_registered():
    from openjarvis.tools import agent_runner

    agents = {a["id"]: a for a in agent_runner.list_agents()}
    assert agents["capability-scout"]["provider"] == "python"
    assert (
        agents["capability-scout"]["python_entry"]
        == "openjarvis.tools.capability_scout:run_as_agent_task"
    )
