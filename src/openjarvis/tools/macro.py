"""Macro tool — simulate keyboard/mouse inputs via voice commands."""

from __future__ import annotations

import ctypes
import time
import sys
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Windows input simulation via SendInput
if sys.platform == "win32":
    INPUT_KEYBOARD = 1
    INPUT_MOUSE = 0
    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_EXTENDEDKEY = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("union", _INPUT_UNION)]


# Virtual key code mapping
VK_MAP: Dict[str, int] = {
    # Numbers
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    # Letters
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    # Function keys
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    # Modifiers
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    # Navigation
    "escape": 0x1B, "esc": 0x1B, "tab": 0x09, "space": 0x20,
    "enter": 0x0D, "return": 0x0D, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E,
    # Mouse
    "mouse_left": -1, "mouse_right": -2,
}

# ---------------------------------------------------------------------------
# Game profiles — switchable macro sets
# ---------------------------------------------------------------------------

GAME_PROFILES: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    # ======================================================================
    # RUST — default key bindings
    # ======================================================================
    "rust": {
        # --- Movement ---
        "sprint": [{"key": "shift", "hold": 0.5}],
        "crouch": [{"key": "ctrl", "hold": 0.05}],
        "jump": [{"key": "space", "hold": 0.05}],
        "swim up": [{"key": "space", "hold": 0.5}],
        "swim down": [{"key": "ctrl", "hold": 0.5}],
        # --- Combat / Items ---
        "attack": [{"key": "mouse_left", "hold": 0.05}],
        "aim": [{"key": "mouse_right", "hold": 0.5}],
        "reload": [{"key": "r", "hold": 0.05}],
        "throw": [{"key": "mouse_right", "hold": 0.8}],
        # --- Hotbar slots ---
        "slot 1": [{"key": "1", "hold": 0.05}],
        "slot 2": [{"key": "2", "hold": 0.05}],
        "slot 3": [{"key": "3", "hold": 0.05}],
        "slot 4": [{"key": "4", "hold": 0.05}],
        "slot 5": [{"key": "5", "hold": 0.05}],
        "slot 6": [{"key": "6", "hold": 0.05}],
        "weapon 1": [{"key": "1", "hold": 0.05}],
        "weapon 2": [{"key": "2", "hold": 0.05}],
        "primary": [{"key": "1", "hold": 0.05}],
        "secondary": [{"key": "2", "hold": 0.05}],
        "rock": [{"key": "1", "hold": 0.05}],
        "torch": [{"key": "2", "hold": 0.05}],
        "bandage": [{"key": "5", "hold": 0.05}],
        # --- Interaction ---
        "use": [{"key": "e", "hold": 0.05}],
        "interact": [{"key": "e", "hold": 0.05}],
        "loot": [{"key": "e", "hold": 0.05}],
        "pickup": [{"key": "e", "hold": 0.05}],
        "open door": [{"key": "e", "hold": 0.05}],
        "close door": [{"key": "e", "hold": 0.05}],
        "mount": [{"key": "e", "hold": 0.05}],
        "dismount": [{"key": "e", "hold": 0.05}],
        # --- Inventory / UI ---
        "inventory": [{"key": "tab", "hold": 0.05}],
        "crafting": [{"key": "q", "hold": 0.05}],
        "map": [{"key": "g", "hold": 0.05}],
        "tech tree": [{"key": "q", "hold": 0.05}],
        # --- Building ---
        "upgrade": [{"key": "e", "hold": 0.8}],  # hold E on building piece
        "rotate": [{"key": "r", "hold": 0.05}],  # while placing
        "remove": [{"key": "e", "hold": 0.8}],
        # --- Weapon mods ---
        "flashlight": [{"key": "f", "hold": 0.05}],
        "laser": [{"key": "g", "hold": 0.05}],
        # --- Communication ---
        "voice chat": [{"key": "v", "hold": 2.0}],
        "push to talk": [{"key": "v", "hold": 2.0}],
        "chat": [{"key": "t", "hold": 0.05}],
        "team chat": [{"key": "enter", "hold": 0.05}],
        # --- Camera / View ---
        "look behind": [{"key": "alt", "hold": 0.5}],
        "free look": [{"key": "alt", "hold": 0.5}],
        # --- Combo macros (Rust-specific) ---
        "heal up": [
            {"key": "5", "hold": 0.05},     # bandage slot
            {"wait": 0.15},
            {"key": "mouse_left", "hold": 0.05},  # use it
        ],
        "quick bandage": [
            {"key": "5", "hold": 0.05},
            {"wait": 0.15},
            {"key": "mouse_left", "hold": 0.05},
        ],
        "quick syringe": [
            {"key": "6", "hold": 0.05},
            {"wait": 0.15},
            {"key": "mouse_left", "hold": 0.05},
        ],
        "quick med": [
            {"key": "6", "hold": 0.05},
            {"wait": 0.15},
            {"key": "mouse_left", "hold": 0.05},
        ],
        "swap and shoot": [
            {"key": "2", "hold": 0.05},     # switch to secondary
            {"wait": 0.2},
            {"key": "mouse_left", "hold": 0.05},
        ],
        "peek right": [
            {"key": "e", "hold": 0.05},     # lean/peek
        ],
        "peek left": [
            {"key": "q", "hold": 0.05},
        ],
        "cancel craft": [{"key": "escape", "hold": 0.05}],
        "close menu": [{"key": "escape", "hold": 0.05}],
        "screenshot": [{"key": "f12", "hold": 0.05}],
        "console": [{"key": "f1", "hold": 0.05}],
        # --- Quick weapon swaps ---
        "hatchet": [{"key": "3", "hold": 0.05}],
        "pickaxe": [{"key": "4", "hold": 0.05}],
        "gun": [{"key": "1", "hold": 0.05}],
        "rifle": [{"key": "1", "hold": 0.05}],
        "shotgun": [{"key": "2", "hold": 0.05}],
        "melee": [{"key": "3", "hold": 0.05}],
        "tool": [{"key": "3", "hold": 0.05}],
        "meds": [{"key": "5", "hold": 0.05}],
        "food": [{"key": "6", "hold": 0.05}],
        "eat": [
            {"key": "6", "hold": 0.05},
            {"wait": 0.15},
            {"key": "mouse_left", "hold": 0.05},
        ],
        "drink": [
            {"key": "6", "hold": 0.05},
            {"wait": 0.15},
            {"key": "mouse_left", "hold": 0.05},
        ],
    },

    # ======================================================================
    # DEFAULT — generic profile
    # ======================================================================
    "default": {
        "shield": [{"key": "5", "hold": 0.05}],
        "heal": [{"key": "4", "hold": 0.05}],
        "reload": [{"key": "r", "hold": 0.05}],
        "crouch": [{"key": "c", "hold": 0.05}],
        "sprint": [{"key": "shift", "hold": 0.5}],
        "inventory": [{"key": "tab", "hold": 0.05}],
        "map": [{"key": "m", "hold": 0.05}],
        "use": [{"key": "e", "hold": 0.05}],
        "interact": [{"key": "e", "hold": 0.05}],
        "flashlight": [{"key": "f", "hold": 0.05}],
        "melee": [{"key": "v", "hold": 0.05}],
        "grenade": [{"key": "g", "hold": 0.05}],
        "slot 1": [{"key": "1", "hold": 0.05}],
        "slot 2": [{"key": "2", "hold": 0.05}],
        "slot 3": [{"key": "3", "hold": 0.05}],
        "slot 4": [{"key": "4", "hold": 0.05}],
        "slot 5": [{"key": "5", "hold": 0.05}],
        "slot 6": [{"key": "6", "hold": 0.05}],
        "weapon 1": [{"key": "1", "hold": 0.05}],
        "weapon 2": [{"key": "2", "hold": 0.05}],
        "primary": [{"key": "1", "hold": 0.05}],
        "secondary": [{"key": "2", "hold": 0.05}],
        "quick heal": [
            {"key": "4", "hold": 0.05},
            {"wait": 0.2},
            {"key": "mouse_left", "hold": 0.05},
        ],
        "drop item": [{"key": "g", "hold": 0.05}],
        "screenshot": [{"key": "f12", "hold": 0.05}],
        "push to talk": [{"key": "v", "hold": 2.0}],
        "voice chat": [{"key": "v", "hold": 2.0}],
    },
}

# Active profile — starts with default, switched via "activate <game>"
_active_profile: str = "default"


def get_active_macros() -> Dict[str, List[Dict[str, Any]]]:
    """Return the currently active macro set."""
    return GAME_PROFILES.get(_active_profile, GAME_PROFILES["default"])


def set_active_profile(name: str) -> bool:
    """Switch to a game profile. Returns True if profile exists."""
    global _active_profile
    key = name.lower().strip()
    if key in GAME_PROFILES:
        _active_profile = key
        return True
    return False


# Backward compat alias
NAMED_MACROS = get_active_macros()


def _send_key(vk: int, down: bool = True) -> None:
    """Send a single key event via SendInput."""
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    scan = user32.MapVirtualKeyW(vk, 0)

    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = scan
    inp.union.ki.dwFlags = 0 if down else KEYEVENTF_KEYUP
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))

    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _send_mouse(button: int, down: bool = True) -> None:
    """Send a mouse click event."""
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    inp = INPUT()
    inp.type = INPUT_MOUSE
    if button == -1:  # left
        inp.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    elif button == -2:  # right
        inp.union.mi.dwFlags = MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP
    inp.union.mi.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))

    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _execute_step(step: Dict[str, Any]) -> None:
    """Execute a single macro step (key press or wait)."""
    if "wait" in step:
        time.sleep(step["wait"])
        return

    key_name = step.get("key", "").lower()
    hold = step.get("hold", 0.05)

    vk = VK_MAP.get(key_name)
    if vk is None:
        return

    if vk < 0:
        # Mouse button
        _send_mouse(vk, down=True)
        time.sleep(hold)
        _send_mouse(vk, down=False)
    else:
        # Keyboard
        _send_key(vk, down=True)
        time.sleep(hold)
        _send_key(vk, down=False)


def _run_macro(steps: List[Dict[str, Any]]) -> str:
    """Execute a sequence of macro steps."""
    for step in steps:
        _execute_step(step)
        time.sleep(0.02)  # Small gap between steps
    return "Macro executed"


@ToolRegistry.register("macro")
class MacroTool(BaseTool):
    """Execute keyboard/mouse macros by voice — gaming hotkeys, combos, etc."""

    tool_id = "macro"

    @property
    def spec(self) -> ToolSpec:
        macro_names = ", ".join(sorted(NAMED_MACROS.keys()))
        return ToolSpec(
            name="macro",
            description=(
                "Execute a keyboard or mouse macro. Use for in-game actions "
                "like switching weapons, healing, reloading, etc. "
                "Can run named macros or press specific keys.\n\n"
                f"Named macros: {macro_names}\n\n"
                "Can also press any key directly: 'press key 5', 'press f1', etc. "
                "Keys can be held for a duration."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "macro_name": {
                        "type": "string",
                        "description": (
                            "Name of a predefined macro to run. "
                            f"Options: {macro_names}"
                        ),
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "A specific key to press (e.g. '5', 'f1', 'e', 'shift'). "
                            "Used when macro_name is not provided."
                        ),
                    },
                    "hold": {
                        "type": "number",
                        "description": (
                            "How long to hold the key in seconds (default 0.05). "
                            "Use longer for hold-to-use actions."
                        ),
                    },
                },
            },
            category="system",
            requires_confirmation=False,
            timeout_seconds=10.0,
            metadata={"platform": "windows"},
        )

    def execute(self, **params: Any) -> ToolResult:
        if sys.platform != "win32":
            return ToolResult(
                tool_name="macro",
                content="Macros only supported on Windows.",
                success=False,
            )

        macro_name = params.get("macro_name", "").strip().lower()
        key = params.get("key", "").strip().lower()
        hold = params.get("hold", 0.05)

        try:
            hold = float(hold)
        except (TypeError, ValueError):
            hold = 0.05

        try:
            if macro_name:
                steps = NAMED_MACROS.get(macro_name)
                if steps is None:
                    return ToolResult(
                        tool_name="macro",
                        content=f"Unknown macro '{macro_name}'. Available: {', '.join(sorted(NAMED_MACROS.keys()))}",
                        success=False,
                    )
                _run_macro(steps)
                return ToolResult(
                    tool_name="macro",
                    content=f"Executed macro: {macro_name}",
                    success=True,
                    metadata={"macro": macro_name},
                )

            if key:
                vk = VK_MAP.get(key)
                if vk is None:
                    return ToolResult(
                        tool_name="macro",
                        content=f"Unknown key '{key}'. Available: {', '.join(sorted(VK_MAP.keys()))}",
                        success=False,
                    )
                _execute_step({"key": key, "hold": hold})
                return ToolResult(
                    tool_name="macro",
                    content=f"Pressed key: {key}" + (f" (held {hold}s)" if hold > 0.1 else ""),
                    success=True,
                    metadata={"key": key, "hold": hold},
                )

            return ToolResult(
                tool_name="macro",
                content="Specify either macro_name or key.",
                success=False,
            )

        except Exception as exc:
            return ToolResult(
                tool_name="macro",
                content=f"Macro failed: {exc}",
                success=False,
            )


__all__ = ["MacroTool"]
