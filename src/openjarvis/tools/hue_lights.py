"""Philips Hue lighting control — lights, colours, scenes, and rooms."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Suppress SSL warnings for local bridge (self-signed cert)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_CONFIG_DIR = Path.home() / ".openjarvis"
_HUE_CONFIG = _CONFIG_DIR / "hue.json"

# Colour presets (xy colour space for Hue API)
COLOUR_PRESETS: Dict[str, list] = {
    "red": [0.675, 0.322],
    "green": [0.409, 0.518],
    "blue": [0.167, 0.04],
    "cyan": [0.153, 0.048],
    "purple": [0.263, 0.124],
    "pink": [0.396, 0.198],
    "orange": [0.588, 0.393],
    "yellow": [0.462, 0.475],
    "warm white": [0.459, 0.41],
    "cool white": [0.318, 0.331],
    "daylight": [0.313, 0.329],
    "relaxed": [0.505, 0.415],
    "energize": [0.313, 0.329],
    "concentrate": [0.373, 0.367],
    "reading": [0.443, 0.407],
    # Gaming vibes
    "gaming": [0.167, 0.04],  # blue
    "lava": [0.675, 0.322],   # red
    "forest": [0.409, 0.518], # green
    "sunset": [0.588, 0.393], # orange
    "ice": [0.153, 0.048],    # cyan
    "party": [0.263, 0.124],  # purple
}


def _load_config() -> Dict[str, str]:
    """Load Hue bridge config (IP + username)."""
    if _HUE_CONFIG.exists():
        return json.loads(_HUE_CONFIG.read_text())
    return {}


def _save_config(data: Dict[str, str]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _HUE_CONFIG.write_text(json.dumps(data, indent=2))


def _discover_bridge() -> Optional[str]:
    """Find bridge IP via Philips cloud discovery."""
    try:
        resp = httpx.get("https://discovery.meethue.com/", timeout=5)
        bridges = resp.json()
        if bridges:
            return bridges[0]["internalipaddress"]
    except Exception:
        pass
    return None


def _api(method: str, path: str, bridge_ip: str, username: str, body: Any = None) -> Any:
    """Call the Hue API."""
    url = f"http://{bridge_ip}/api/{username}{path}"
    kwargs: dict = {"timeout": 5}
    if body is not None:
        kwargs["json"] = body
    resp = getattr(httpx, method)(url, **kwargs)
    return resp.json()


def _register(bridge_ip: str) -> Optional[str]:
    """Register with the bridge (user must press the link button first)."""
    url = f"https://{bridge_ip}/api"
    body = {"devicetype": "openjarvis#voice", "generateclientkey": True}
    resp = httpx.post(url, json=body, timeout=5, verify=False)
    data = resp.json()
    if isinstance(data, list) and data:
        if "success" in data[0]:
            return data[0]["success"]["username"]
        if "error" in data[0]:
            return None
    return None


def _ensure_connected() -> tuple[str, str]:
    """Ensure we have a bridge IP and username. Raises RuntimeError if not."""
    cfg = _load_config()
    bridge_ip = cfg.get("bridge_ip") or _discover_bridge()
    username = cfg.get("username", "")

    if not bridge_ip:
        raise RuntimeError(
            "No Hue bridge found on your network. "
            "Make sure it's powered on and connected."
        )

    if not username:
        raise RuntimeError(
            f"Hue bridge found at {bridge_ip} but not paired. "
            "Press the link button on your Hue bridge, then try again."
        )

    return bridge_ip, username


def _get_all_lights(bridge_ip: str, username: str) -> Dict[str, Any]:
    return _api("get", "/lights", bridge_ip, username)


def _get_groups(bridge_ip: str, username: str) -> Dict[str, Any]:
    return _api("get", "/groups", bridge_ip, username)


def _find_light_id(name: str, lights: Dict[str, Any]) -> Optional[str]:
    """Find light ID by name (case-insensitive, partial match)."""
    name_lower = name.lower()
    for lid, ldata in lights.items():
        if ldata.get("name", "").lower() == name_lower:
            return lid
    for lid, ldata in lights.items():
        if name_lower in ldata.get("name", "").lower():
            return lid
    return None


def _find_group_id(name: str, groups: Dict[str, Any]) -> Optional[str]:
    """Find group/room ID by name (case-insensitive, partial match)."""
    name_lower = name.lower()
    for gid, gdata in groups.items():
        if gdata.get("name", "").lower() == name_lower:
            return gid
    for gid, gdata in groups.items():
        if name_lower in gdata.get("name", "").lower():
            return gid
    return None


@ToolRegistry.register("hue_lights")
class HueLightsTool(BaseTool):
    """Control Philips Hue lights — on/off, brightness, colours, rooms."""

    tool_id = "hue_lights"

    @property
    def spec(self) -> ToolSpec:
        colours = ", ".join(sorted(COLOUR_PRESETS.keys()))
        return ToolSpec(
            name="hue_lights",
            description=(
                "Control Philips Hue smart lights. Can turn lights on/off, "
                "set brightness (0-254), change colours, and control rooms/groups. "
                "Actions: 'on', 'off', 'set' (brightness/colour), 'list' (show all lights), "
                "'pair' (register with bridge). "
                f"Colour presets: {colours}. "
                "Target can be a light name, room name, or 'all'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["on", "off", "set", "list", "pair"],
                        "description": "Action: on, off, set (brightness/colour), list, pair.",
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "Light name, room name, or 'all'. "
                            "E.g. 'desk lamp', 'bedroom', 'all'."
                        ),
                    },
                    "colour": {
                        "type": "string",
                        "description": (
                            f"Colour preset name. Options: {colours}"
                        ),
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Brightness 0-100 (percent).",
                    },
                },
                "required": ["action"],
            },
            category="smart_home",
            requires_confirmation=False,
            timeout_seconds=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "").strip().lower()
        target = params.get("target", "all").strip()
        colour = params.get("colour", "").strip().lower()
        brightness = params.get("brightness")

        try:
            if action == "pair":
                return self._pair()
            elif action == "list":
                return self._list_lights()
            elif action in ("on", "off", "set"):
                return self._control(action, target, colour, brightness)
            else:
                return ToolResult(
                    tool_name="hue_lights",
                    content=f"Unknown action: {action}",
                    success=False,
                )
        except RuntimeError as exc:
            return ToolResult(
                tool_name="hue_lights",
                content=str(exc),
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="hue_lights",
                content=f"Hue error: {exc}",
                success=False,
            )

    def _pair(self) -> ToolResult:
        """Register with the Hue bridge."""
        bridge_ip = _discover_bridge()
        if not bridge_ip:
            return ToolResult(
                tool_name="hue_lights",
                content="No Hue bridge found on the network.",
                success=False,
            )

        # Try to register (user must have pressed link button)
        username = _register(bridge_ip)
        if not username:
            return ToolResult(
                tool_name="hue_lights",
                content=(
                    f"Bridge found at {bridge_ip}. "
                    "Press the link button on your Hue bridge and say 'pair lights' again."
                ),
                success=False,
            )

        _save_config({"bridge_ip": bridge_ip, "username": username})
        return ToolResult(
            tool_name="hue_lights",
            content=f"Paired with Hue bridge at {bridge_ip}. Lights are now controllable.",
            success=True,
        )

    def _list_lights(self) -> ToolResult:
        bridge_ip, username = _ensure_connected()
        lights = _get_all_lights(bridge_ip, username)
        groups = _get_groups(bridge_ip, username)

        lines = ["Lights:\n"]
        for lid, ldata in lights.items():
            name = ldata.get("name", "?")
            on = "ON" if ldata.get("state", {}).get("on") else "OFF"
            bri = ldata.get("state", {}).get("bri", 0)
            bri_pct = int(bri / 254 * 100)
            lines.append(f"  {name}: {on} ({bri_pct}%)")

        lines.append("\nRooms:\n")
        for gid, gdata in groups.items():
            name = gdata.get("name", "?")
            on = "ON" if gdata.get("state", {}).get("any_on") else "OFF"
            light_count = len(gdata.get("lights", []))
            lines.append(f"  {name}: {on} ({light_count} lights)")

        return ToolResult(
            tool_name="hue_lights",
            content="\n".join(lines),
            success=True,
        )

    def _control(
        self, action: str, target: str, colour: str, brightness: Any
    ) -> ToolResult:
        bridge_ip, username = _ensure_connected()
        lights = _get_all_lights(bridge_ip, username)
        groups = _get_groups(bridge_ip, username)

        # Build the state body
        state: Dict[str, Any] = {}
        if action == "on":
            state["on"] = True
        elif action == "off":
            state["on"] = False
        elif action == "set":
            state["on"] = True

        if brightness is not None:
            try:
                bri_pct = int(brightness)
                state["bri"] = max(1, min(254, int(bri_pct / 100 * 254)))
            except (TypeError, ValueError):
                pass

        if colour and colour in COLOUR_PRESETS:
            state["xy"] = COLOUR_PRESETS[colour]
            state["on"] = True

        if not state:
            return ToolResult(
                tool_name="hue_lights",
                content="Nothing to change — specify on/off, brightness, or colour.",
                success=False,
            )

        # Apply to target
        target_lower = target.lower()
        affected: List[str] = []

        if target_lower == "all":
            # Use group 0 (all lights)
            _api("put", "/groups/0/action", bridge_ip, username, state)
            affected.append("all lights")
        else:
            # Try room/group first
            gid = _find_group_id(target, groups)
            if gid:
                _api("put", f"/groups/{gid}/action", bridge_ip, username, state)
                affected.append(groups[gid]["name"])
            else:
                # Try individual light
                lid = _find_light_id(target, lights)
                if lid:
                    _api("put", f"/lights/{lid}/state", bridge_ip, username, state)
                    affected.append(lights[lid]["name"])
                else:
                    return ToolResult(
                        tool_name="hue_lights",
                        content=f"No light or room matching '{target}' found.",
                        success=False,
                    )

        # Build response
        parts = []
        if action == "on":
            parts.append("Turned on")
        elif action == "off":
            parts.append("Turned off")
        else:
            parts.append("Set")

        parts.append(", ".join(affected))

        if colour:
            parts.append(f"to {colour}")
        if brightness is not None:
            parts.append(f"at {brightness}%")

        return ToolResult(
            tool_name="hue_lights",
            content=" ".join(parts) + ".",
            success=True,
        )


__all__ = ["HueLightsTool"]
