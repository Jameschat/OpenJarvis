from openjarvis.tools.agent_runner import DEFAULT_AGENTS


def test_tiktok_agents_are_registered_with_expected_providers():
    agents = {agent["id"]: agent for agent in DEFAULT_AGENTS}
    expected = {
        "trend-scorer": {
            "provider": "claude",
            "model": "claude-sonnet-4-6",
        },
        "script-writer-tiktok": {
            "provider": "claude",
            "model": "claude-sonnet-4-6",
        },
        "video-generator": {
            "provider": "python",
            "python_entry": "openjarvis.tiktok.pipeline:video_generator_entry",
        },
        "tiktok-publisher": {
            "provider": "python",
            "python_entry": "openjarvis.tiktok.pipeline:tiktok_publisher_entry",
        },
        "stats-puller": {
            "provider": "python",
            "python_entry": "openjarvis.tiktok.pipeline:stats_puller_entry",
        },
        "comment-responder": {
            "provider": "claude",
            "model": "claude-sonnet-4-6",
        },
    }

    for agent_id, fields in expected.items():
        assert agent_id in agents
        assert agents[agent_id]["color"] == "#ff2d55"
        for field, value in fields.items():
            assert agents[agent_id][field] == value
