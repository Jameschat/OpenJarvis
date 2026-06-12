"""Tests for AgentManager.recover_agent() always resetting status."""

import pytest

from openjarvis.agents.manager import AgentManager


@pytest.fixture
def manager(tmp_path):
    db = str(tmp_path / "agents.db")
    return AgentManager(db_path=db)


def test_recover_resets_to_idle_without_checkpoint(manager):
    """recover_agent must reset status to idle even when no checkpoint exists."""
    agent = manager.create_agent(name="test", agent_type="monitor_operative")
    manager.update_agent(agent["id"], status="error")

    result = manager.recover_agent(agent["id"])

    assert result is None
    refreshed = manager.get_agent(agent["id"])
    assert refreshed["status"] == "idle"


def test_recover_resets_to_idle_with_checkpoint(manager):
    """recover_agent returns checkpoint and resets status when checkpoint exists."""
    agent = manager.create_agent(name="test", agent_type="monitor_operative")
    manager.update_agent(agent["id"], status="error")
    manager.save_checkpoint(agent["id"], "tick-1", {"history": []}, {"tools": {}})

    result = manager.recover_agent(agent["id"])

    assert result is not None
    assert result["tick_id"] == "tick-1"
    refreshed = manager.get_agent(agent["id"])
    assert refreshed["status"] == "idle"


def test_init_recovers_agents_stuck_in_running(tmp_path):
    """A process restart orphans status='running' rows (ticks run in server
    threads); a new AgentManager on the same DB must reset them so later run
    clicks do not 409 forever."""
    db = str(tmp_path / "agents.db")
    first = AgentManager(db_path=db)
    agent = first.create_agent(name="zombie", agent_type="monitor_operative")
    first.update_agent(agent["id"], status="running")
    first.close()

    reopened = AgentManager(db_path=db)
    refreshed = reopened.get_agent(agent["id"])
    assert refreshed["status"] == "needs_attention"
    assert "recovered" in (refreshed.get("current_activity") or "")


def test_init_leaves_non_running_statuses_alone(tmp_path):
    db = str(tmp_path / "agents.db")
    first = AgentManager(db_path=db)
    idle = first.create_agent(name="idle-one", agent_type="monitor_operative")
    paused = first.create_agent(name="paused-one", agent_type="monitor_operative")
    first.update_agent(paused["id"], status="paused")
    first.close()

    reopened = AgentManager(db_path=db)
    assert reopened.get_agent(idle["id"])["status"] == "idle"
    assert reopened.get_agent(paused["id"])["status"] == "paused"
