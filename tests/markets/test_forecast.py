from openjarvis.markets.forecast import generate_forecast
from openjarvis.markets.chart_analyst import (
    _forecast_overlay_series,
    _format_forecast_markdown,
)


def _bars(start=100.0, step=1.0, count=80):
    bars = []
    price = start
    for i in range(count):
        open_ = price
        close = price + step
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        bars.append({
            "ts": 1_700_000_000 + i * 14_400,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000 + i,
        })
        price = close
    return bars


def _ind(last, atr=2.0, ema20=None, ema50=None, rsi=55.0):
    ema20 = last * 0.98 if ema20 is None else ema20
    ema50 = last * 0.95 if ema50 is None else ema50
    return {
        "last": last,
        "atr": atr,
        "ema20_last": ema20,
        "ema50_last": ema50,
        "rsi_last": rsi,
        "support": [last * 0.96, last * 0.92],
        "resistance": [last * 1.04, last * 1.09],
        "swing_highs_recent": [last * 1.04, last * 1.09],
        "swing_lows_recent": [last * 0.96, last * 0.92],
    }


def test_forecast_returns_three_scenarios_and_probabilities_sum_to_100():
    bars = _bars()
    last = bars[-1]["close"]
    forecast = generate_forecast(bars, _ind(last), timeframe="4h", horizon="3d")

    assert forecast["timeframe"] == "4h"
    assert forecast["horizon"] == "3d"
    assert forecast["steps"] == 18
    assert len(forecast["scenarios"]) == 3
    assert {s["key"] for s in forecast["scenarios"]} == {"base", "bull", "bear"}
    assert sum(s["probability"] for s in forecast["scenarios"]) == 100


def test_uptrend_weights_bull_or_base_above_bear():
    bars = _bars(step=1.5)
    last = bars[-1]["close"]
    forecast = generate_forecast(
        bars,
        _ind(last, ema20=last * 0.98, ema50=last * 0.94, rsi=62.0),
        timeframe="4h",
        horizon="3d",
    )
    probs = {s["key"]: s["probability"] for s in forecast["scenarios"]}

    assert probs["bear"] < probs["base"]
    assert probs["bear"] < probs["bull"]


def test_downtrend_weights_bear_or_base_above_bull():
    bars = _bars(start=180.0, step=-1.4)
    last = bars[-1]["close"]
    forecast = generate_forecast(
        bars,
        _ind(last, ema20=last * 1.02, ema50=last * 1.06, rsi=38.0),
        timeframe="4h",
        horizon="3d",
    )
    probs = {s["key"]: s["probability"] for s in forecast["scenarios"]}

    assert probs["bull"] < probs["base"]
    assert probs["bull"] < probs["bear"]


def test_bull_and_bear_targets_are_directionally_coherent():
    bars = _bars()
    last = bars[-1]["close"]
    forecast = generate_forecast(bars, _ind(last), timeframe="4h", horizon="24h")
    scenarios = {s["key"]: s for s in forecast["scenarios"]}

    assert scenarios["bull"]["target"] > last
    assert scenarios["bull"]["stop"] < last
    assert scenarios["bear"]["target"] < last
    assert scenarios["bear"]["stop"] > last
    assert len(scenarios["base"]["path"]) == 7
    assert scenarios["base"]["path"][0]["price"] == last


def test_too_few_bars_returns_empty_forecast():
    forecast = generate_forecast(_bars(count=5), _ind(105.0), timeframe="4h", horizon="3d")

    assert forecast["available"] is False
    assert forecast["scenarios"] == []


def test_forecast_markdown_formats_three_scenarios():
    bars = _bars()
    last = bars[-1]["close"]
    forecast = generate_forecast(bars, _ind(last), timeframe="4h", horizon="3d")

    md = _format_forecast_markdown(forecast)

    assert "## 4H Forecast Scenarios" in md
    assert "Base" in md
    assert "Bull" in md
    assert "Bear" in md
    assert "%" in md


def test_forecast_overlay_series_expands_to_selected_horizon():
    bars = _bars(count=80)
    last = bars[-1]["close"]
    forecast = generate_forecast(bars, _ind(last), timeframe="4h", horizon="30d")

    overlay = _forecast_overlay_series(
        bars[-1]["ts"],
        4 * 3600,
        forecast,
    )

    assert overlay is not None
    assert len(overlay["times"]) == 181
    assert overlay["times"][0].timestamp() == bars[-1]["ts"]
    assert overlay["times"][-1].timestamp() == bars[-1]["ts"] + (180 * 4 * 3600)
    assert set(overlay["paths"]) == {"base", "bull", "bear"}
