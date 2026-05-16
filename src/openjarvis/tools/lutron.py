"""Lutron Caseta/RadioRA lighting control via LEAP protocol.

Supports Lutron Caseta Smart Bridge Pro, RadioRA 2, and RadioRA 3.
Requires one-time pairing to generate TLS certificates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".openjarvis"
_LUTRON_CONFIG = _CONFIG_DIR / "lutron.json"
_CERT_DIR = _CONFIG_DIR / "lutron_certs"


def _load_config() -> Dict[str, Any]:
    if _LUTRON_CONFIG.exists():
        return json.loads(_LUTRON_CONFIG.read_text())
    return {}


def _save_config(data: Dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _LUTRON_CONFIG.write_text(json.dumps(data, indent=2))


def _run_async(coro):
    """Run an async coroutine from sync code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an existing loop — use a thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        return asyncio.run(coro)


async def _discover_bridges() -> List[Dict[str, str]]:
    """Scan for Lutron bridges on the network."""
    from pylutron_caseta.leap import open_connection
    from pylutron_caseta import smartbridge

    # Try to find bridges via mDNS
    try:
        from zeroconf import ServiceBrowser, Zeroconf
        import socket
        import time

        bridges = []

        class Listener:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    addr = socket.inet_ntoa(info.addresses[0])
                    bridges.append({"host": addr, "name": name})

            def remove_service(self, *a):
                pass

            def update_service(self, *a):
                pass

        zc = Zeroconf()
        ServiceBrowser(zc, "_lutron._tcp.local.", Listener())
        await asyncio.sleep(3)
        zc.close()

        if bridges:
            return bridges
    except ImportError:
        pass

    return []


async def _pair_bridge(host: str) -> str:
    """Pair with a Lutron bridge to generate TLS certificates."""
    from pylutron_caseta.pairing import async_pair

    _CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_file = str(_CERT_DIR / "caseta.crt")
    key_file = str(_CERT_DIR / "caseta.key")
    ca_file = str(_CERT_DIR / "caseta-bridge.crt")

    data = await async_pair(host)

    # Save certificates
    Path(cert_file).write_text(data["cert"])
    Path(key_file).write_text(data["key"])
    Path(ca_file).write_text(data["ca"])

    # Save config
    _save_config({
        "host": host,
        "cert": cert_file,
        "key": key_file,
        "ca": ca_file,
    })

    return f"Paired with Lutron bridge at {host}. Certificates saved."


async def _get_bridge():
    """Get a connected Smartbridge instance."""
    from pylutron_caseta.smartbridge import Smartbridge

    cfg = _load_config()
    if not cfg.get("host"):
        return None

    bridge = Smartbridge.create_tls(
        cfg["host"],
        cfg.get("cert", ""),
        cfg.get("key", ""),
        cfg.get("ca", ""),
    )
    await bridge.connect()
    return bridge


async def _list_devices() -> str:
    """List all Lutron devices."""
    bridge = await _get_bridge()
    if bridge is None:
        return "Not paired with a Lutron bridge. Say 'pair lutron' first."

    try:
        devices = bridge.get_devices()
        scenes = bridge.get_scenes()

        lines = ["Lutron Devices:\n"]
        for dev_id, dev in devices.items():
            name = dev.get("name", "?")
            dtype = dev.get("type", "?")
            zone = dev.get("zone", "")
            current = dev.get("current_state", -1)
            level = f" ({current}%)" if current >= 0 else ""
            lines.append(f"  [{dev_id}] {name} ({dtype}){level}")

        if scenes:
            lines.append("\nScenes:\n")
            for scene_id, scene in scenes.items():
                lines.append(f"  [{scene_id}] {scene.get('name', '?')}")

        return "\n".join(lines)
    finally:
        await bridge.close()


async def _set_brightness(target: str, level: int) -> str:
    """Set a device brightness by name or ID."""
    bridge = await _get_bridge()
    if bridge is None:
        return "Not paired with a Lutron bridge."

    try:
        devices = bridge.get_devices()
        target_lower = target.lower()

        # Find device by name (partial match) or ID
        dev_id = None
        dev_name = target
        for did, dev in devices.items():
            name = dev.get("name", "")
            if name.lower() == target_lower or target_lower in name.lower():
                dev_id = did
                dev_name = name
                break
            if did == target:
                dev_id = did
                dev_name = name
                break

        if dev_id is None:
            return f"No Lutron device matching '{target}' found."

        await bridge.set_value(dev_id, level)
        if level == 0:
            return f"Turned off {dev_name}."
        elif level == 100:
            return f"Turned on {dev_name}."
        else:
            return f"Set {dev_name} to {level}%."
    finally:
        await bridge.close()


async def _turn_on(target: str) -> str:
    return await _set_brightness(target, 100)


async def _turn_off(target: str) -> str:
    return await _set_brightness(target, 0)


async def _activate_scene(name: str) -> str:
    """Activate a Lutron scene by name."""
    bridge = await _get_bridge()
    if bridge is None:
        return "Not paired with a Lutron bridge."

    try:
        scenes = bridge.get_scenes()
        name_lower = name.lower()

        for scene_id, scene in scenes.items():
            sname = scene.get("name", "")
            if sname.lower() == name_lower or name_lower in sname.lower():
                await bridge.activate_scene(scene_id)
                return f"Activated scene: {sname}"

        return f"No scene matching '{name}' found."
    finally:
        await bridge.close()


async def _raise_lower(target: str, direction: str) -> str:
    """Raise or lower shades."""
    bridge = await _get_bridge()
    if bridge is None:
        return "Not paired with a Lutron bridge."

    try:
        devices = bridge.get_devices()
        target_lower = target.lower()

        for did, dev in devices.items():
            name = dev.get("name", "")
            if target_lower in name.lower():
                level = 100 if direction == "raise" else 0
                await bridge.set_value(did, level)
                return f"{'Raised' if direction == 'raise' else 'Lowered'} {name}."

        return f"No device matching '{target}' found."
    finally:
        await bridge.close()


@ToolRegistry.register("lutron")
class LutronTool(BaseTool):
    """Control Lutron Caseta/RadioRA lighting, dimmers, shades, and scenes."""

    tool_id = "lutron"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lutron",
            description=(
                "Control Lutron Caseta or RadioRA smart lighting system. "
                "Actions: 'on' (turn on), 'off' (turn off), 'set' (set brightness 0-100), "
                "'scene' (activate a scene), 'raise'/'lower' (for shades), "
                "'list' (show all devices and scenes), 'pair' (connect to bridge). "
                "Requires a Lutron Smart Bridge Pro on the network. "
                "First-time setup: press the pairing button on the bridge and say 'pair lutron'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["on", "off", "set", "scene", "raise", "lower", "list", "pair"],
                        "description": "Action to perform.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Device name, room name, or scene name.",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Brightness level 0-100 (for 'set' action).",
                    },
                    "host": {
                        "type": "string",
                        "description": "Bridge IP address (for 'pair' action, optional if auto-discovered).",
                    },
                },
                "required": ["action"],
            },
            category="smart_home",
            requires_confirmation=False,
            timeout_seconds=15.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "").strip().lower()
        target = params.get("target", "").strip()
        level = params.get("level", 100)
        host = params.get("host", "").strip()

        try:
            if action == "pair":
                if not host:
                    return ToolResult(
                        tool_name="lutron",
                        content=(
                            "To pair, I need the bridge IP address. "
                            "Press the small button on the back of your Lutron bridge, "
                            "then say 'pair lutron at 192.168.x.x' with your bridge IP."
                        ),
                        success=False,
                    )
                msg = _run_async(_pair_bridge(host))
                return ToolResult(tool_name="lutron", content=msg, success=True)

            elif action == "list":
                msg = _run_async(_list_devices())
                return ToolResult(tool_name="lutron", content=msg, success="Not paired" not in msg)

            elif action == "on":
                if not target:
                    return ToolResult(tool_name="lutron", content="Specify which device to turn on.", success=False)
                msg = _run_async(_turn_on(target))
                return ToolResult(tool_name="lutron", content=msg, success=True)

            elif action == "off":
                if not target:
                    return ToolResult(tool_name="lutron", content="Specify which device to turn off.", success=False)
                msg = _run_async(_turn_off(target))
                return ToolResult(tool_name="lutron", content=msg, success=True)

            elif action == "set":
                if not target:
                    return ToolResult(tool_name="lutron", content="Specify which device.", success=False)
                try:
                    level = int(level)
                except (TypeError, ValueError):
                    level = 100
                msg = _run_async(_set_brightness(target, max(0, min(100, level))))
                return ToolResult(tool_name="lutron", content=msg, success=True)

            elif action == "scene":
                if not target:
                    return ToolResult(tool_name="lutron", content="Specify which scene.", success=False)
                msg = _run_async(_activate_scene(target))
                return ToolResult(tool_name="lutron", content=msg, success=True)

            elif action in ("raise", "lower"):
                if not target:
                    return ToolResult(tool_name="lutron", content="Specify which shade.", success=False)
                msg = _run_async(_raise_lower(target, action))
                return ToolResult(tool_name="lutron", content=msg, success=True)

            else:
                return ToolResult(tool_name="lutron", content=f"Unknown action: {action}", success=False)

        except Exception as exc:
            return ToolResult(tool_name="lutron", content=f"Lutron error: {exc}", success=False)


__all__ = ["LutronTool"]
