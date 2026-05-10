"""Tests for the graphify-on-demand trigger.

The trigger is the post-task hook that rebuilds the vault graph after
agent activity. Debounce semantics matter: a department-dispatch burst
of 10 tasks finishing in 5 seconds must collapse to 1 rebuild.
"""
from __future__ import annotations

import time

import pytest

from openjarvis.tools import graphify_trigger


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with a fresh trigger module (no last-fired memory)."""
    graphify_trigger.reset_for_tests()
    yield
    graphify_trigger.reset_for_tests()


def test_first_call_fires_refresh(monkeypatch):
    fired = []
    def fake_refresh():
        fired.append(time.time())
        return {"started": True}
    monkeypatch.setattr(graphify_trigger, "_call_refresh", fake_refresh)

    graphify_trigger.maybe_refresh_after_task(now=1000.0, cooldown_seconds=60.0)
    # Wait briefly for the daemon thread to run.
    deadline = time.time() + 2.0
    while not fired and time.time() < deadline:
        time.sleep(0.01)
    assert len(fired) == 1


def test_second_call_within_cooldown_is_skipped(monkeypatch):
    fired = []
    def fake_refresh():
        fired.append(time.time())
        return {"started": True}
    monkeypatch.setattr(graphify_trigger, "_call_refresh", fake_refresh)

    graphify_trigger.maybe_refresh_after_task(now=1000.0, cooldown_seconds=60.0)
    graphify_trigger.maybe_refresh_after_task(now=1030.0, cooldown_seconds=60.0)
    # Settle.
    deadline = time.time() + 2.0
    while len(fired) < 1 and time.time() < deadline:
        time.sleep(0.01)
    # Second call must NOT have triggered another refresh.
    time.sleep(0.2)
    assert len(fired) == 1


def test_call_after_cooldown_fires_again(monkeypatch):
    fired = []
    def fake_refresh():
        fired.append(time.time())
        return {"started": True}
    monkeypatch.setattr(graphify_trigger, "_call_refresh", fake_refresh)

    graphify_trigger.maybe_refresh_after_task(now=1000.0, cooldown_seconds=60.0)
    graphify_trigger.maybe_refresh_after_task(now=1061.0, cooldown_seconds=60.0)
    deadline = time.time() + 2.0
    while len(fired) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(fired) == 2


def test_burst_collapses_to_single_refresh(monkeypatch):
    """10 tasks finishing in 5 seconds → exactly 1 refresh."""
    fired = []
    def fake_refresh():
        fired.append(time.time())
        return {"started": True}
    monkeypatch.setattr(graphify_trigger, "_call_refresh", fake_refresh)

    base = 1000.0
    for i in range(10):
        graphify_trigger.maybe_refresh_after_task(now=base + i * 0.5, cooldown_seconds=60.0)
    time.sleep(0.3)
    assert len(fired) == 1


def test_refresh_failure_does_not_raise(monkeypatch, caplog):
    """If graphify itself errors, the trigger must NOT propagate.
    mark_finished MUST stay non-fatal."""
    def boom():
        raise RuntimeError("graphify exploded")
    monkeypatch.setattr(graphify_trigger, "_call_refresh", boom)
    # No exception should escape this call.
    graphify_trigger.maybe_refresh_after_task(now=1000.0, cooldown_seconds=60.0)
    time.sleep(0.2)
    # And state should still record we tried, so we don't retry-loop.
    assert graphify_trigger._last_fired_at == 1000.0
