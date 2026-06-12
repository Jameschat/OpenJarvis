"""Tests for the memory-activity aggregator (Memory page data feed)."""

from __future__ import annotations

import time

import pytest

from openjarvis.tools import memory_activity


@pytest.fixture()
def fake_events(monkeypatch):
    now = time.time()
    events = [
        {"op": "write", "label": "Knowledge/note.md", "kind": "knowledge",
         "count": 1, "source": "voice", "ts": now - 5},
        {"op": "read", "label": "Projects/openjarvis/STATE.md", "kind": "recall",
         "count": 1, "source": "agent", "ts": now - 3},
        {"op": "append", "label": "Daily/log.md", "kind": "daily",
         "count": 1, "source": "qwen", "ts": now - 1},
    ]
    monkeypatch.setattr(memory_activity, "_vault_events", lambda since: [
        e for e in events if not since or e["ts"] > since
    ])
    return events


class TestPulses:
    def test_write_flows_actor_to_vault(self, fake_events):
        snap = memory_activity.snapshot()
        write = next(p for p in snap["pulses"] if p["op"] == "write")
        assert write["source"] == "jarvis" and write["target"] == "vault"

    def test_read_flows_vault_to_actor(self, fake_events):
        snap = memory_activity.snapshot()
        read = next(p for p in snap["pulses"] if p["op"] == "read")
        assert read["source"] == "vault" and read["target"] == "jarvis"

    def test_qwen_source_lights_qwen_node(self, fake_events):
        snap = memory_activity.snapshot()
        append = next(p for p in snap["pulses"] if p["op"] == "append")
        assert append["source"] == "qwen"

    def test_since_filters_old_pulses(self, fake_events):
        cutoff = time.time() - 2
        snap = memory_activity.snapshot(since=cutoff)
        assert len(snap["pulses"]) == 1
        assert snap["pulses"][0]["op"] == "append"


class TestNodesAndLog:
    def test_snapshot_has_all_seven_nodes(self, fake_events):
        snap = memory_activity.snapshot()
        ids = {n["id"] for n in snap["nodes"]}
        assert ids == {"vault", "graphify", "agentmemory", "jarvis", "qwen", "claude", "codex"}

    def test_log_is_newest_first(self, fake_events):
        snap = memory_activity.snapshot()
        ts = [r["ts"] for r in snap["log"]]
        assert ts == sorted(ts, reverse=True)

    def test_snapshot_never_raises_when_sources_break(self, monkeypatch):
        monkeypatch.setattr(
            memory_activity, "_vault_events",
            lambda since: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # _vault_events raising would propagate — guard at call level instead
        try:
            snap = memory_activity.snapshot()
        except RuntimeError:
            pytest.fail("snapshot must not raise when a source breaks")
        assert "nodes" in snap
