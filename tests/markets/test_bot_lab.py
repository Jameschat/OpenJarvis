import pytest

from openjarvis.markets.bot_lab import (
    DCAConfig,
    GridConfig,
    SignalConfig,
    backtest_dca,
    backtest_dca_from_history,
    backtest_grid,
    backtest_grid_from_history,
    backtest_signal,
    backtest_signal_from_history,
    sweep_dca,
    sweep_dca_from_history,
    sweep_grid,
    sweep_grid_from_history,
)
from openjarvis.markets.markets_tools import (
    TOOL_DISPATCH,
    TOOL_SCHEMAS,
    backtest_dca_bot,
    backtest_grid_bot,
    backtest_signal_bot,
    sweep_dca_bot,
    sweep_grid_bot,
)


def _bar(ts, open_, high, low, close):
    return {
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


def _patch_live_history(monkeypatch, bars, *, expected_ticker="QWEN", expected_range=None):
    inserted = []

    def fake_fetch_history(ticker, range_str="3mo"):
        assert ticker == expected_ticker
        if expected_range is not None:
            assert range_str == expected_range
        return bars

    def fake_insert_history_bars(ticker, written_bars, source=""):
        assert ticker == expected_ticker
        assert source == "coingecko"
        inserted.extend(written_bars)
        return len(written_bars)

    monkeypatch.setattr("openjarvis.markets.sources.coingecko.fetch_history", fake_fetch_history)
    monkeypatch.setattr("openjarvis.markets.store.insert_history_bars", fake_insert_history_bars)
    return inserted


def test_dca_backtest_takes_profit_after_safety_order():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 99.0, 100.0, 94.0, 96.0),
        _bar(3, 96.0, 101.0, 95.0, 99.0),
    ]
    config = DCAConfig(
        ticker="QWEN",
        initial_cash_gbp=1000.0,
        base_order_gbp=100.0,
        safety_order_gbp=100.0,
        max_safety_orders=1,
        safety_order_deviation_pct=5.0,
        take_profit_pct=3.0,
        slippage_pct=0.0,
    )

    result = backtest_dca(bars, config)

    assert result["ok"] is True
    assert result["closed_deals"] == 1
    assert result["open_deals"] == 0
    assert result["realized_pnl_gbp"] > 0
    assert result["win_rate_pct"] == 100.0
    assert result["deals"][0]["close_reason"] == "take_profit"
    assert result["deals"][0]["safety_orders_used"] == 1
    assert [trade["kind"] for trade in result["trades"]] == ["base_order", "safety_order", "take_profit"]


def test_dca_backtest_reports_open_exposure_and_drawdown():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 97.0, 98.0, 94.0, 95.0),
        _bar(3, 91.0, 92.0, 88.0, 89.0),
    ]
    config = DCAConfig(
        ticker="QWEN",
        initial_cash_gbp=1000.0,
        base_order_gbp=100.0,
        safety_order_gbp=100.0,
        max_safety_orders=1,
        safety_order_deviation_pct=5.0,
        take_profit_pct=4.0,
        slippage_pct=0.0,
    )

    result = backtest_dca(bars, config)

    assert result["closed_deals"] == 0
    assert result["open_deals"] == 1
    assert result["unrealized_pnl_gbp"] < 0
    assert result["capital_locked_gbp"] > 0
    assert result["max_drawdown_pct"] > 0
    assert result["max_floating_drawdown_pct"] > 0


def test_dca_backtest_reopens_after_closed_deal_on_next_bar():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
        _bar(3, 103.0, 104.0, 101.0, 102.0),
        _bar(4, 102.0, 103.0, 100.0, 101.0),
    ]

    result = backtest_dca(
        bars,
        DCAConfig(ticker="QWEN", base_order_gbp=100.0, take_profit_pct=2.0, slippage_pct=0.0),
    )

    assert result["closed_deals"] == 1
    assert result["open_deals"] == 1
    assert [trade["kind"] for trade in result["trades"]] == ["base_order", "take_profit", "base_order"]
    assert result["trades"][-1]["deal_id"] == 2


def test_dca_backtest_rejects_invalid_config_and_empty_history():
    with pytest.raises(ValueError, match="at least two bars"):
        backtest_dca([], DCAConfig(ticker="QWEN"))

    with pytest.raises(ValueError, match="base_order_gbp"):
        backtest_dca(
            [_bar(1, 100.0, 101.0, 99.0, 100.0), _bar(2, 100.0, 102.0, 99.0, 101.0)],
            DCAConfig(ticker="QWEN", base_order_gbp=0),
        )


def test_backtest_dca_from_history_uses_live_market_history(monkeypatch):
    bars = [
        _bar(10, 100.0, 101.0, 99.0, 100.0),
        _bar(11, 100.0, 104.0, 99.0, 103.0),
    ]
    inserted = _patch_live_history(monkeypatch, bars, expected_range="3mo")

    result = backtest_dca_from_history(
        "QWEN",
        since_ts=10,
        limit=50,
        initial_cash_gbp=500,
        base_order_gbp=100,
        take_profit_pct=2.0,
        slippage_pct=0.0,
    )

    assert result["ticker"] == "QWEN"
    assert result["bars"] == 2
    assert result["closed_deals"] == 1
    assert inserted == bars


def test_backtest_dca_from_history_ignores_stale_cache_and_uses_live_crypto_history(monkeypatch):
    fetched_bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: [_bar(99, 1.0, 1.0, 1.0, 1.0)])
    inserted = _patch_live_history(monkeypatch, fetched_bars, expected_ticker="SOL", expected_range="3mo")

    result = backtest_dca_from_history("SOL", base_order_gbp=100, take_profit_pct=2.0, slippage_pct=0.0)

    assert result["ok"] is True
    assert result["ticker"] == "SOL"
    assert result["bars"] == 2
    assert inserted == fetched_bars


def test_backtest_dca_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]

    _patch_live_history(monkeypatch, bars)

    payload = backtest_dca_bot("QWEN", base_order_gbp=100, take_profit_pct=2.0, slippage_pct=0.0)

    assert '"ok": true' in payload
    assert TOOL_DISPATCH["backtest_dca_bot"] is backtest_dca_bot
    assert any(schema["function"]["name"] == "backtest_dca_bot" for schema in TOOL_SCHEMAS)


def test_markets_pro_bot_backtest_endpoint_helper(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_bot_backtest

    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = _markets_pro_bot_backtest(
        {"ticker": "qwen", "base_order_gbp": 100, "take_profit_pct": 2.0, "slippage_pct": 0.0}
    )

    assert result["ok"] is True
    assert result["ticker"] == "QWEN"
    assert result["closed_deals"] == 1


def test_direct_chat_sol_dca_backtest_returns_result_without_llm(monkeypatch):
    from openjarvis.cli import brain_server

    def fake_backtest(body):
        assert body["ticker"] == "SOL"
        assert body["strategy"] == "dca"
        assert body["limit"] == 500
        return {
            "ok": True,
            "ticker": "SOL",
            "bars": 92,
            "first_ts": 1747699200,
            "last_ts": 1779148800,
            "initial_cash_gbp": 1000,
            "ending_equity_gbp": 911.98,
            "realized_pnl_gbp": 55.45,
            "unrealized_pnl_gbp": -143.44,
            "roi_pct": -8.8,
            "closed_deals": 17,
            "open_deals": 1,
            "win_rate_pct": 100.0,
            "max_drawdown_pct": 15.84,
            "max_floating_drawdown_pct": 41.78,
            "capital_locked_gbp": 400,
        }

    monkeypatch.setattr(brain_server, "_markets_pro_bot_backtest", fake_backtest)

    response = brain_server._try_direct_markets_chat(
        "Run a live-data SOL DCA backtest using the current Bot Lab settings. "
        "Keep it paper-only and report profit, drawdown, win rate, and assumptions."
    )

    assert response is not None
    assert "Paper-only SOL DCA backtest complete" in response
    assert "Net profit: GBP -88.02" in response
    assert "Win rate: 100.0%" in response
    assert "Max drawdown: 15.84%" in response
    assert "No live order was placed" in response


def test_markets_pro_bot_backtest_endpoint_helper_routes_grid(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_bot_backtest

    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = _markets_pro_bot_backtest(
        {
            "strategy": "grid",
            "ticker": "qwen",
            "lower_price": 90,
            "upper_price": 110,
            "grid_count": 4,
            "order_gbp": 100,
            "slippage_pct": 0.0,
        }
    )

    assert result["ok"] is True
    assert result["strategy"] == "grid"
    assert result["ticker"] == "QWEN"


def test_markets_pro_bot_backtest_endpoint_helper_routes_dca_sweep(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_bot_backtest

    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = _markets_pro_bot_backtest(
        {
            "strategy": "dca_sweep",
            "ticker": "qwen",
            "take_profit_pct_values": [1.0],
            "safety_order_deviation_pct_values": [3.0],
            "max_safety_orders_values": [1],
        }
    )

    assert result["ok"] is True
    assert result["strategy"] == "dca_sweep"
    assert result["runs"] == 1


def test_grid_backtest_buys_and_sells_crossed_levels():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 101.0, 94.0, 96.0),
        _bar(3, 96.0, 106.0, 95.0, 104.0),
    ]
    config = GridConfig(
        ticker="QWEN",
        initial_cash_gbp=1000.0,
        lower_price=90.0,
        upper_price=110.0,
        grid_count=4,
        order_gbp=100.0,
        slippage_pct=0.0,
    )

    result = backtest_grid(bars, config)

    assert result["ok"] is True
    assert result["strategy"] == "grid"
    assert result["closed_grid_trades"] >= 1
    assert result["realized_pnl_gbp"] > 0
    assert result["grid_step"] == 5.0
    assert any(trade["kind"] == "grid_buy" for trade in result["trades"])
    assert any(trade["kind"] == "grid_sell" for trade in result["trades"])


def test_grid_backtest_reports_open_inventory_and_drawdown():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 100.0, 94.0, 95.0),
        _bar(3, 95.0, 96.0, 90.0, 91.0),
    ]
    config = GridConfig(
        ticker="QWEN",
        initial_cash_gbp=1000.0,
        lower_price=90.0,
        upper_price=110.0,
        grid_count=4,
        order_gbp=100.0,
        slippage_pct=0.0,
    )

    result = backtest_grid(bars, config)

    assert result["open_grid_orders"] > 0
    assert result["capital_locked_gbp"] > 0
    assert result["unrealized_pnl_gbp"] < 0
    assert result["max_drawdown_pct"] > 0


def test_grid_backtest_rejects_invalid_range():
    with pytest.raises(ValueError, match="upper_price"):
        backtest_grid(
            [_bar(1, 100.0, 101.0, 99.0, 100.0), _bar(2, 100.0, 102.0, 99.0, 101.0)],
            GridConfig(ticker="QWEN", lower_price=110.0, upper_price=90.0),
        )


def test_backtest_grid_from_history_uses_live_market_history(monkeypatch):
    bars = [
        _bar(20, 100.0, 101.0, 99.0, 100.0),
        _bar(21, 100.0, 106.0, 94.0, 104.0),
    ]
    inserted = _patch_live_history(monkeypatch, bars, expected_range="3mo")

    result = backtest_grid_from_history(
        "QWEN",
        since_ts=20,
        limit=60,
        lower_price=90,
        upper_price=110,
        grid_count=4,
        order_gbp=100,
        slippage_pct=0.0,
    )

    assert result["ticker"] == "QWEN"
    assert result["bars"] == 2
    assert inserted == bars


def test_backtest_grid_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    _patch_live_history(monkeypatch, bars)

    payload = backtest_grid_bot("QWEN", lower_price=90, upper_price=110, grid_count=4, order_gbp=100, slippage_pct=0.0)

    assert '"ok": true' in payload
    assert TOOL_DISPATCH["backtest_grid_bot"] is backtest_grid_bot
    assert any(schema["function"]["name"] == "backtest_grid_bot" for schema in TOOL_SCHEMAS)


def test_sweep_dca_ranks_parameter_variants():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 99.0, 100.0, 94.0, 96.0),
        _bar(3, 96.0, 103.0, 95.0, 101.0),
    ]

    result = sweep_dca(
        bars,
        ticker="QWEN",
        take_profit_pct_values=[1.0, 3.0],
        safety_order_deviation_pct_values=[3.0, 5.0],
        max_safety_orders_values=[1],
        base_order_gbp=100.0,
        safety_order_gbp=100.0,
        initial_cash_gbp=1000.0,
        slippage_pct=0.0,
    )

    assert result["ok"] is True
    assert result["strategy"] == "dca_sweep"
    assert result["runs"] == 4
    assert len(result["top_results"]) == 4
    assert result["top_results"][0]["score"] >= result["top_results"][-1]["score"]
    assert "take_profit_pct" in result["top_results"][0]["config"]


def test_sweep_grid_ranks_parameter_variants():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 101.0, 94.0, 96.0),
        _bar(3, 96.0, 106.0, 95.0, 104.0),
    ]

    result = sweep_grid(
        bars,
        ticker="QWEN",
        lower_price_values=[90.0],
        upper_price_values=[110.0],
        grid_count_values=[4, 5],
        order_gbp_values=[50.0, 100.0],
        initial_cash_gbp=1000.0,
        slippage_pct=0.0,
    )

    assert result["ok"] is True
    assert result["strategy"] == "grid_sweep"
    assert result["runs"] == 4
    assert len(result["top_results"]) == 4
    assert "grid_count" in result["top_results"][0]["config"]


def test_sweep_dca_from_history_uses_live_market_history(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = sweep_dca_from_history(
        "QWEN",
        take_profit_pct_values=[1.0],
        safety_order_deviation_pct_values=[3.0],
        max_safety_orders_values=[1],
    )

    assert result["ok"] is True
    assert result["ticker"] == "QWEN"
    assert result["runs"] == 1


def test_sweep_dca_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]
    _patch_live_history(monkeypatch, bars)

    payload = sweep_dca_bot("QWEN", take_profit_pct_values=[1.0], safety_order_deviation_pct_values=[3.0])

    assert '"ok": true' in payload
    assert TOOL_DISPATCH["sweep_dca_bot"] is sweep_dca_bot
    assert any(schema["function"]["name"] == "sweep_dca_bot" for schema in TOOL_SCHEMAS)


def test_sweep_grid_from_history_uses_live_market_history(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = sweep_grid_from_history(
        "QWEN",
        lower_price_values=[90.0],
        upper_price_values=[110.0],
        grid_count_values=[4],
        order_gbp_values=[100.0],
    )

    assert result["ok"] is True
    assert result["ticker"] == "QWEN"
    assert result["runs"] == 1


def test_sweep_grid_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    _patch_live_history(monkeypatch, bars)

    payload = sweep_grid_bot(
        "QWEN",
        lower_price_values=[90.0],
        upper_price_values=[110.0],
        grid_count_values=[4],
        order_gbp_values=[100.0],
        slippage_pct=0.0,
    )

    assert '"ok": true' in payload
    assert TOOL_DISPATCH["sweep_grid_bot"] is sweep_grid_bot
    assert any(schema["function"]["name"] == "sweep_grid_bot" for schema in TOOL_SCHEMAS)


def test_markets_pro_bot_backtest_endpoint_helper_routes_grid_sweep(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_bot_backtest

    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = _markets_pro_bot_backtest(
        {
            "strategy": "grid_sweep",
            "ticker": "qwen",
            "lower_price_values": [90.0],
            "upper_price_values": [110.0],
            "grid_count_values": [4],
            "order_gbp_values": [100.0],
            "slippage_pct": 0.0,
        }
    )

    assert result["ok"] is True
    assert result["strategy"] == "grid_sweep"
    assert result["runs"] == 1


def test_signal_backtest_replays_buy_and_sell_alerts():
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 104.0, 106.0, 103.0, 105.0),
        _bar(3, 109.0, 111.0, 108.0, 110.0),
    ]
    config = SignalConfig(
        ticker="QWEN",
        initial_cash_gbp=1000.0,
        default_order_gbp=200.0,
        signals=[
            {"ts": 1, "action": "buy"},
            {"ts": 3, "action": "sell"},
        ],
        slippage_pct=0.0,
    )

    result = backtest_signal(bars, config)

    assert result["ok"] is True
    assert result["strategy"] == "signal"
    assert result["signals_processed"] == 2
    assert result["closed_signal_trades"] == 1
    assert result["open_position"] is False
    assert result["realized_pnl_gbp"] > 0
    assert [trade["kind"] for trade in result["trades"]] == ["signal_buy", "signal_sell"]


def test_signal_backtest_from_history_uses_live_market_history(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 99.0, 105.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = backtest_signal_from_history(
        "QWEN",
        signals=[{"ts": 1, "action": "buy"}, {"ts": 2, "action": "sell"}],
        default_order_gbp=100.0,
        slippage_pct=0.0,
    )

    assert result["ok"] is True
    assert result["ticker"] == "QWEN"
    assert result["signals_processed"] == 2


def test_backtest_signal_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 99.0, 105.0),
    ]
    _patch_live_history(monkeypatch, bars)

    payload = backtest_signal_bot(
        "QWEN",
        signals=[{"ts": 1, "action": "buy"}, {"ts": 2, "action": "sell"}],
        default_order_gbp=100.0,
        slippage_pct=0.0,
    )

    assert '"ok": true' in payload
    assert TOOL_DISPATCH["backtest_signal_bot"] is backtest_signal_bot
    assert any(schema["function"]["name"] == "backtest_signal_bot" for schema in TOOL_SCHEMAS)


def test_markets_pro_bot_backtest_endpoint_helper_routes_signal(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_bot_backtest

    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 99.0, 105.0),
    ]
    _patch_live_history(monkeypatch, bars)

    result = _markets_pro_bot_backtest(
        {
            "strategy": "signal",
            "ticker": "qwen",
            "signals": [{"ts": 1, "action": "buy"}, {"ts": 2, "action": "sell"}],
            "default_order_gbp": 100.0,
            "slippage_pct": 0.0,
        }
    )

    assert result["ok"] is True
    assert result["strategy"] == "signal"
    assert result["signals_processed"] == 2
