"""Weather tool — get live weather based on system location."""

from __future__ import annotations

from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _get_location() -> dict:
    """Get city/country from IP geolocation."""
    resp = httpx.get("http://ip-api.com/json/", timeout=5)
    resp.raise_for_status()
    return resp.json()


def _get_weather(city: str) -> dict:
    """Fetch weather data from wttr.in (free, no API key)."""
    resp = httpx.get(f"https://wttr.in/{city}?format=j1", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _format_current(data: dict, location: str) -> str:
    """Format current conditions into a readable string."""
    cur = data["current_condition"][0]
    desc = cur["weatherDesc"][0]["value"]
    temp_c = cur["temp_C"]
    temp_f = cur["temp_F"]
    feels_c = cur["FeelsLikeC"]
    humidity = cur["humidity"]
    wind_kmh = cur["windspeedKmph"]
    wind_dir = cur["winddir16Point"]
    uv = cur.get("uvIndex", "?")
    visibility = cur.get("visibility", "?")

    lines = [
        f"Weather in {location}:",
        f"  Condition: {desc}",
        f"  Temperature: {temp_c}C ({temp_f}F), feels like {feels_c}C",
        f"  Humidity: {humidity}%",
        f"  Wind: {wind_kmh} km/h {wind_dir}",
        f"  UV Index: {uv}",
        f"  Visibility: {visibility} km",
    ]

    # Add today's forecast
    if data.get("weather"):
        today = data["weather"][0]
        lines.append(
            f"  Today: {today['mintempC']}C - {today['maxtempC']}C, "
            f"sunrise {today['astronomy'][0]['sunrise']}, "
            f"sunset {today['astronomy'][0]['sunset']}"
        )

    # 3-day forecast summary
    for day in data.get("weather", [])[1:3]:
        date = day["date"]
        desc = day["hourly"][4]["weatherDesc"][0]["value"]  # midday
        lines.append(f"  {date}: {day['mintempC']}C-{day['maxtempC']}C, {desc}")

    return "\n".join(lines)


@ToolRegistry.register("weather")
class WeatherTool(BaseTool):
    """Get live weather for the user's location or a specified city."""

    tool_id = "weather"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="weather",
            description=(
                "Get the current weather and forecast. "
                "If no city is specified, auto-detects location from the user's IP. "
                "Returns temperature, conditions, wind, humidity, UV index, "
                "and a 3-day forecast. No API key required."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": (
                            "City name to get weather for. "
                            "Leave empty to auto-detect from location."
                        ),
                    },
                },
                "required": [],
            },
            category="information",
            requires_confirmation=False,
            timeout_seconds=15.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        city = params.get("city", "").strip()

        try:
            if not city:
                loc = _get_location()
                city = loc.get("city", "London")
                location_str = f"{city}, {loc.get('country', '')}"
            else:
                location_str = city

            data = _get_weather(city)
            content = _format_current(data, location_str)

            return ToolResult(
                tool_name="weather",
                content=content,
                success=True,
                metadata={"city": city},
            )
        except Exception as exc:
            return ToolResult(
                tool_name="weather",
                content=f"Failed to get weather: {exc}",
                success=False,
            )


__all__ = ["WeatherTool"]
