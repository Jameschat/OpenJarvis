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


def test_run_due_paper_bots_executes_dry_run_backtest_and_rolls_forward(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    created = paper_scheduler.schedule_paper_bot(
        ticker="BTC",
        strategy="dca",
        interval_minutes=5,
        config={"base_order_gbp": 100.0},
        now_ts=100,
    )["bot"]
    calls = []

    def fake_backtest(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return {"ok": True, "strategy": "dca", "ticker": ticker, "roi_pct": 3.25, "max_drawdown_pct": 1.5}

    monkeypatch.setattr("openjarvis.markets.bot_lab.backtest_dca_from_history", fake_backtest)

    result = paper_scheduler.run_due_paper_bots(now_ts=400)

    assert result["ok"] is True
    assert result["checked"] == 1
    assert result["results"][0]["bot_id"] == created["id"]
    assert result["results"][0]["executed_paper"] is False
    assert result["results"][0]["backtest"]["roi_pct"] == 3.25
    assert calls == [("BTC", {"base_order_gbp": 100.0})]
    updated = paper_scheduler.list_paper_bots()["bots"][0]
    assert updated["last_checked_at"] == 400
    assert updated["next_run_at"] == 700
    assert "roi=3.25%" in updated["last_note"]


def test_run_due_paper_bots_refuses_execution_enabled_bots(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    created = paper_scheduler.schedule_paper_bot(
        ticker="BTC",
        strategy="dca",
        execute_paper=True,
        confirm_paper_execution=True,
        now_ts=100,
    )["bot"]

    result = paper_scheduler.run_due_paper_bots(now_ts=3700)

    assert result["ok"] is True
    assert result["checked"] == 1
    assert result["results"][0]["bot_id"] == created["id"]
    assert result["results"][0]["ok"] is False
    assert "signal strategy" in result["results"][0]["error"]


def test_approve_paper_execution_requires_exact_phrase(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    created = paper_scheduler.schedule_paper_bot(ticker="BTC", strategy="signal", now_ts=100)["bot"]

    rejected = paper_scheduler.approve_paper_execution(created["id"], approval_phrase="yes", now_ts=200)
    approved = paper_scheduler.approve_paper_execution(created["id"], approval_phrase="PAPER ONLY", now_ts=300)

    assert rejected["ok"] is False
    assert "PAPER ONLY" in rejected["error"]
    assert approved["ok"] is True
    assert approved["bot"]["execute_paper"] is True
    assert approved["bot"]["dry_run"] is False
    assert approved["bot"]["paper_execution_approved_at"] == 300


def test_run_due_paper_bots_executes_approved_signal_once(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.markets import paper_scheduler

    created = paper_scheduler.schedule_paper_bot(
        ticker="BTC",
        strategy="signal",
        interval_minutes=5,
        config={"paper_signal": {"id": "sig-1", "action": "buy", "amount_gbp": 25}},
        now_ts=100,
    )["bot"]
    paper_scheduler.approve_paper_execution(created["id"], approval_phrase="PAPER ONLY", now_ts=200)
    calls = []

    def fake_buy(ticker, gbp_amount, **kwargs):
        calls.append((ticker, gbp_amount, kwargs))
        return {"ok": True, "ticker": ticker, "gross_gbp": gbp_amount, "trade_id": "paper_1"}

    monkeypatch.setattr("openjarvis.markets.paper_broker.paper_buy", fake_buy)

    first = paper_scheduler.run_due_paper_bots(now_ts=400)
    second = paper_scheduler.run_due_paper_bots(now_ts=700)

    assert first["results"][0]["ok"] is True
    assert first["results"][0]["executed_paper"] is True
    assert first["results"][0]["paper_result"]["trade_id"] == "paper_1"
    assert calls == [("BTC", 25.0, {"stop": None, "tp1": None, "tp2": None})]
    updated = paper_scheduler.list_paper_bots()["bots"][0]
    assert updated["executed_signal_ids"] == ["sig-1"]
    assert second["results"][0]["ok"] is True
    assert second["results"][0]["executed_paper"] is False
    assert "already executed" in second["results"][0]["note"]
    assert calls == [("BTC", 25.0, {"stop": None, "tp1": None, "tp2": None})]


def test_paper_execution_endpoint_and_tool_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.cli.brain_server import _markets_pro_paper_bot_approve_execution
    from openjarvis.markets import paper_scheduler
    from openjarvis.markets.markets_tools import (
        TOOL_DISPATCH,
        TOOL_SCHEMAS,
        approve_paper_bot_execution,
    )

    created = paper_scheduler.schedule_paper_bot(ticker="BTC", strategy="signal", now_ts=100)["bot"]
    endpoint = _markets_pro_paper_bot_approve_execution(
        {"id": created["id"], "approval_phrase": "PAPER ONLY"}
    )
    payload = approve_paper_bot_execution(created["id"], "PAPER ONLY")

    assert endpoint["ok"] is True
    assert '"ok": true' in payload
    assert TOOL_DISPATCH["approve_paper_bot_execution"] is approve_paper_bot_execution
    assert any(schema["function"]["name"] == "approve_paper_bot_execution" for schema in TOOL_SCHEMAS)


def test_run_paper_bot_tool_and_endpoint_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_PAPER_BOT_DIR", str(tmp_path))
    from openjarvis.cli.brain_server import _markets_pro_paper_bot_run_due
    from openjarvis.markets import paper_scheduler
    from openjarvis.markets.markets_tools import (
        TOOL_DISPATCH,
        TOOL_SCHEMAS,
        run_due_paper_bots,
    )

    paper_scheduler.schedule_paper_bot(ticker="BTC", strategy="dca", now_ts=100)
    monkeypatch.setattr(
        "openjarvis.markets.bot_lab.backtest_dca_from_history",
        lambda ticker, **kwargs: {"ok": True, "strategy": "dca", "ticker": ticker, "roi_pct": 1.0},
    )

    endpoint = _markets_pro_paper_bot_run_due({"now_ts": 3700})
    payload = run_due_paper_bots()

    assert endpoint["ok"] is True
    assert '"ok": true' in payload
    assert TOOL_DISPATCH["run_due_paper_bots"] is run_due_paper_bots
    assert any(schema["function"]["name"] == "run_due_paper_bots" for schema in TOOL_SCHEMAS)
