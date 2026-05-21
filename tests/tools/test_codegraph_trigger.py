from __future__ import annotations

import time

import pytest

from openjarvis.tools import codegraph_trigger


@pytest.fixture(autouse=True)
def _reset_state():
    codegraph_trigger.reset_for_tests()
    yield
    codegraph_trigger.reset_for_tests()


def test_first_change_queues_codegraph_sync(monkeypatch):
    fired = []

    def fake_sync():
        fired.append(time.time())
        return {"started": True, "ok": True}

    monkeypatch.setattr(codegraph_trigger, "_call_sync", fake_sync)

    assert codegraph_trigger.maybe_sync_after_change(now=1000.0, cooldown_seconds=60.0)
    deadline = time.time() + 2.0
    while not fired and time.time() < deadline:
        time.sleep(0.01)

    assert len(fired) == 1


def test_burst_collapses_to_single_codegraph_sync(monkeypatch):
    fired = []

    def fake_sync():
        fired.append(time.time())
        return {"started": True, "ok": True}

    monkeypatch.setattr(codegraph_trigger, "_call_sync", fake_sync)

    for i in range(10):
        codegraph_trigger.maybe_sync_after_change(now=1000.0 + i, cooldown_seconds=60.0)
    time.sleep(0.2)

    assert len(fired) == 1


def test_sync_failure_does_not_raise(monkeypatch):
    def boom():
        raise RuntimeError("sync failed")

    monkeypatch.setattr(codegraph_trigger, "_call_sync", boom)

    assert codegraph_trigger.maybe_sync_after_change(now=1000.0, cooldown_seconds=60.0)
    time.sleep(0.2)
    assert codegraph_trigger._last_fired_at == 1000.0


def test_vault_write_triggers_graphify_and_codegraph(monkeypatch):
    from openjarvis.tools import graphify_trigger, obsidian_brain

    graphify_calls = []
    codegraph_calls = []

    monkeypatch.setattr(
        graphify_trigger,
        "maybe_refresh_after_task",
        lambda **kwargs: graphify_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        codegraph_trigger,
        "maybe_sync_after_change",
        lambda **kwargs: codegraph_calls.append(kwargs) or True,
    )

    obsidian_brain._emit_event("write", "test note", kind="knowledge")

    assert graphify_calls == [{"cooldown_seconds": 60.0}]
    assert codegraph_calls == [{"cooldown_seconds": 120.0}]
