from datetime import datetime


def test_next_future_run_at_rolls_past_missed_daily_runs():
    from openjarvis.tools import agent_runner

    next_at = agent_runner._next_future_run_at(
        "2026-05-10T02:00:00",
        "daily",
        datetime.fromisoformat("2026-05-12T18:49:00"),
    )

    assert next_at == "2026-05-13T02:00:00"


def test_should_skip_missed_recurring_only_when_catch_up_disabled():
    from openjarvis.tools import agent_runner

    assert agent_runner._should_skip_missed_recurring(
        {"recurrence": "daily", "catch_up": False}
    )
    assert not agent_runner._should_skip_missed_recurring(
        {"recurrence": "daily", "catch_up": True}
    )
    assert not agent_runner._should_skip_missed_recurring(
        {"recurrence": "once", "catch_up": False}
    )
