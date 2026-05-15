import pytest

from openjarvis.markets.bot_lab import (
    DCAConfig,
    GridConfig,
    backtest_dca,
    backtest_dca_from_history,
    backtest_grid,
    backtest_grid_from_history,
    sweep_dca,
    sweep_dca_from_history,
    sweep_grid,
)
from openjarvis.markets.markets_tools import (
    TOOL_DISPATCH,
    TOOL_SCHEMAS,
    backtest_dca_bot,
    backtest_grid_bot,
    sweep_dca_bot,
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


def test_dca_backtest_rejects_invalid_config_and_empty_history():
    with pytest.raises(ValueError, match="at least two bars"):
        backtest_dca([], DCAConfig(ticker="QWEN"))

    with pytest.raises(ValueError, match="base_order_gbp"):
        backtest_dca(
            [_bar(1, 100.0, 101.0, 99.0, 100.0), _bar(2, 100.0, 102.0, 99.0, 101.0)],
            DCAConfig(ticker="QWEN", base_order_gbp=0),
        )


def test_backtest_dca_from_history_uses_market_store(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]

    def fake_get_history(ticker, since_ts=None, limit=None):
        assert ticker == "QWEN"
        assert since_ts == 10
        assert limit == 50
        return bars

    monkeypatch.setattr("openjarvis.markets.store.get_history", fake_get_history)

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


def test_backtest_dca_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]

    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

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
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

    result = _markets_pro_bot_backtest(
        {"ticker": "qwen", "base_order_gbp": 100, "take_profit_pct": 2.0, "slippage_pct": 0.0}
    )

    assert result["ok"] is True
    assert result["ticker"] == "QWEN"
    assert result["closed_deals"] == 1


def test_markets_pro_bot_backtest_endpoint_helper_routes_grid(monkeypatch):
    from openjarvis.cli.brain_server import _markets_pro_bot_backtest

    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

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
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

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


def test_backtest_grid_from_history_uses_market_store(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]

    def fake_get_history(ticker, since_ts=None, limit=None):
        assert ticker == "QWEN"
        assert since_ts == 20
        assert limit == 60
        return bars

    monkeypatch.setattr("openjarvis.markets.store.get_history", fake_get_history)

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


def test_backtest_grid_bot_is_registered_as_llm_tool(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 106.0, 94.0, 104.0),
    ]
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

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


def test_sweep_dca_from_history_uses_market_store(monkeypatch):
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        _bar(2, 100.0, 104.0, 99.0, 103.0),
    ]
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

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
    monkeypatch.setattr("openjarvis.markets.store.get_history", lambda *args, **kwargs: bars)

    payload = sweep_dca_bot("QWEN", take_profit_pct_values=[1.0], safety_order_deviation_pct_values=[3.0])

    assert '"ok": true' in payload
    assert TOOL_DISPATCH["sweep_dca_bot"] is sweep_dca_bot
    assert any(schema["function"]["name"] == "sweep_dca_bot" for schema in TOOL_SCHEMAS)
