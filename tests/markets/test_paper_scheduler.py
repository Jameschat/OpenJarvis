from pathlib import Path


def test_schedule_paper_bot_defaults_to_dry_run_and_paper_only(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    result = paper_scheduler.schedule_paper_bot(
        ticker="qwen",
        strategy="signal",
        interval_minutes=15,
        config={"signals": [{"ts": 1, "action": "buy"}]},
        now_ts=1000,
    )

    assert result["ok"] is True
    assert result["bot"]["ticker"] == "QWEN"
    assert result["bot"]["strategy"] == "signal"
    assert result["bot"]["dry_run"] is True
    assert result["bot"]["paper_only"] is True
    assert result["bot"]["no_live_orders"] is True
    assert result["bot"]["next_run_at"] == 1900
    assert Path(result["path"]).exists()


def test_schedule_paper_bot_rejects_unapproved_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    result = paper_scheduler.schedule_paper_bot(
        ticker="BTC",
        strategy="dca",
        execute_paper=True,
        confirm_paper_execution=False,
    )

    assert result["ok"] is False
    assert "explicit approval" in result["error"]


def test_list_cancel_and_due_paper_bots(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    created = paper_scheduler.schedule_paper_bot(
        ticker="BTC",
        strategy="grid",
        interval_minutes=5,
        now_ts=100,
    )["bot"]

    assert paper_scheduler.list_paper_bots()["bots"][0]["id"] == created["id"]
    assert paper_scheduler.due_paper_bots(now_ts=399)["due"] == []
    assert paper_scheduler.due_paper_bots(now_ts=400)["due"][0]["id"] == created["id"]

    cancelled = paper_scheduler.cancel_paper_bot(created["id"])
    assert cancelled["ok"] is True
    assert paper_scheduler.list_paper_bots()["bots"][0]["status"] == "cancelled"


def test_mark_paper_bot_checked_rolls_next_run(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    created = paper_scheduler.schedule_paper_bot(
        ticker="BTC",
        strategy="dca",
        interval_minutes=10,
        now_ts=100,
    )["bot"]

    tick = paper_scheduler.mark_paper_bot_checked(created["id"], now_ts=700, note="dry run")

    assert tick["ok"] is True
    assert tick["bot"]["last_checked_at"] == 700
    assert tick["bot"]["next_run_at"] == 1300
    assert tick["bot"]["last_note"] == "dry run"


def test_markets_pro_paper_bot_helpers_route_scheduler(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.cli.brain_server import (
        _markets_pro_paper_bot_cancel,
        _markets_pro_paper_bot_list,
        _markets_pro_paper_bot_schedule,
    )

    created = _markets_pro_paper_bot_schedule(
        {"ticker": "btc", "strategy": "dca", "interval_minutes": 30, "config": {"base_order_gbp": 100}}
    )

    assert created["ok"] is True
    assert _markets_pro_paper_bot_list()["bots"][0]["ticker"] == "BTC"
    assert _markets_pro_paper_bot_cancel({"id": created["bot"]["id"]})["ok"] is True


def test_paper_bot_scheduler_tools_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets.markets_tools import (
        TOOL_DISPATCH,
        TOOL_SCHEMAS,
        list_paper_bot_schedules,
        schedule_paper_bot,
    )

    payload = schedule_paper_bot("BTC", strategy="dca", interval_minutes=15)
    listed = list_paper_bot_schedules()

    assert '"ok": true' in payload
    assert '"ticker": "BTC"' in listed
    assert TOOL_DISPATCH["schedule_paper_bot"] is schedule_paper_bot
    assert TOOL_DISPATCH["list_paper_bot_schedules"] is list_paper_bot_schedules
    assert any(schema["function"]["name"] == "schedule_paper_bot" for schema in TOOL_SCHEMAS)
    assert any(schema["function"]["name"] == "list_paper_bot_schedules" for schema in TOOL_SCHEMAS)
