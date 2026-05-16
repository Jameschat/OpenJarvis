"""App launcher tool — open applications, games, and URLs by name."""

from __future__ import annotations

import glob
import os
import subprocess
import sys
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Steam game name → app ID mapping
STEAM_GAMES: dict[str, int] = {
    "rust": 252490,
    "counter-strike": 730,
    "cs2": 730,
    "csgo": 730,
    "dota": 570,
    "dota 2": 570,
    "team fortress": 440,
    "tf2": 440,
    "apex legends": 1172470,
    "apex": 1172470,
    "pubg": 578080,
    "elden ring": 1245620,
    "valheim": 892970,
    "terraria": 105600,
    "gta v": 271590,
    "gta 5": 271590,
    "ark": 346110,
    "dayz": 221100,
    "lethal company": 1966720,
    "palworld": 1623730,
    "helldivers": 553850,
    "helldivers 2": 553850,
    "satisfactory": 526870,
    "the forest": 242760,
    "sons of the forest": 1326470,
    "sea of thieves": 1172620,
    "no mans sky": 275850,
    "civilization": 289070,
    "civ 6": 289070,
    "stardew valley": 413150,
    "rimworld": 294100,
    "factorio": 427520,
    "destiny 2": 1085660,
    "warframe": 230410,
    "dead by daylight": 381210,
    "phasmophobia": 739630,
    "among us": 945360,
    "fall guys": 1097150,
    "rocket league": 252950,
    "rainbow six siege": 359550,
    "r6": 359550,
}


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "www."))


def _find_shortcut(name: str) -> Optional[str]:
    """Search Start Menu and Desktop for a .lnk matching *name*."""
    search_dirs = []
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("PROGRAMDATA", "")
    userprofile = os.environ.get("USERPROFILE", "")

    if userprofile:
        search_dirs.append(os.path.join(userprofile, "Desktop"))
    if appdata:
        search_dirs.append(
            os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")
        )
    if programdata:
        search_dirs.append(
            os.path.join(programdata, "Microsoft", "Windows", "Start Menu", "Programs")
        )

    name_lower = name.lower()

    # First pass: exact match on shortcut filename (without .lnk)
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for lnk in glob.glob(os.path.join(d, "**", "*.lnk"), recursive=True):
            basename = os.path.splitext(os.path.basename(lnk))[0].lower()
            if basename == name_lower:
                return lnk

    # Second pass: partial match (name is contained in shortcut name)
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for lnk in glob.glob(os.path.join(d, "**", "*.lnk"), recursive=True):
            basename = os.path.splitext(os.path.basename(lnk))[0].lower()
            if name_lower in basename or basename in name_lower:
                return lnk

    return None


def _launch_shortcut(lnk_path: str) -> str:
    """Launch a .lnk shortcut file."""
    os.startfile(lnk_path)
    name = os.path.splitext(os.path.basename(lnk_path))[0]
    return f"Launched {name}"


def _launch_steam_game(name: str) -> Optional[str]:
    """Try to launch a Steam game by name. Returns message or None."""
    lookup = name.lower().strip()
    app_id = STEAM_GAMES.get(lookup)
    if app_id is None:
        return None
    uri = f"steam://rungameid/{app_id}"
    os.startfile(uri)
    return f"Launching {name} via Steam (app ID {app_id})"


def _launch_windows(target: str, args: str) -> str:
    """Launch a target on Windows. Returns status message."""
    # 1. URLs — open in default browser
    if _is_url(target):
        os.startfile(target)
        return f"Opened {target}"

    # 2. Steam game by name
    steam_result = _launch_steam_game(target)
    if steam_result:
        return steam_result

    # 3. Search for a matching shortcut on Desktop / Start Menu
    lnk = _find_shortcut(target)
    if lnk:
        return _launch_shortcut(lnk)

    # 4. Try protocol URIs (steam://, minecraft://, etc.)
    if "://" in target:
        os.startfile(target)
        return f"Opened {target}"

    # 5. Try os.startfile (handles executables on PATH, registered apps)
    try:
        if args:
            raise OSError("use subprocess for args")
        os.startfile(target)
        return f"Launched {target}"
    except OSError:
        pass

    # 6. Try subprocess with detached process
    cmd_parts = [target] + (args.split() if args else [])
    try:
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            cmd_parts,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return f"Launched {target}" + (f" with args: {args}" if args else "")
    except FileNotFoundError:
        pass

    # 7. Last resort: shell start command
    try:
        shell_cmd = f'start "" "{target}"'
        if args:
            shell_cmd += f" {args}"
        subprocess.Popen(shell_cmd, shell=True)
        return f"Launched {target} via shell"
    except Exception:
        pass

    return f"Could not find or launch '{target}'"


@ToolRegistry.register("app_launcher")
class AppLauncherTool(BaseTool):
    """Launch applications, games, and URLs on the local system."""

    tool_id = "app_launcher"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="app_launcher",
            description=(
                "Launch an application, game, or URL on the user's Windows computer. "
                "Supports app names (e.g. 'Discord', 'Chrome', 'Steam'), "
                "Steam games by name (e.g. 'Rust', 'CS2', 'Apex Legends'), "
                "URLs (e.g. 'https://google.com'), "
                "and executable paths. "
                "Searches Desktop shortcuts and Start Menu to find apps. "
                "The app opens in the background and stays running."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "What to launch: an app name (Discord, Chrome, Steam, "
                            "Blender, MSI Afterburner), "
                            "a game name (Rust, CS2, Apex Legends), "
                            "a URL (https://...), or an executable path."
                        ),
                    },
                    "args": {
                        "type": "string",
                        "description": (
                            "Optional command-line arguments to pass to the app."
                        ),
                    },
                },
                "required": ["target"],
            },
            category="system",
            requires_confirmation=False,
            timeout_seconds=10.0,
            required_capabilities=["system:launch"],
            metadata={"platform": "windows"},
        )

    def execute(self, **params: Any) -> ToolResult:
        target = params.get("target", "").strip()
        args = params.get("args", "").strip()

        if not target:
            return ToolResult(
                tool_name="app_launcher",
                content="No target specified.",
                success=False,
            )

        try:
            if sys.platform == "win32":
                msg = _launch_windows(target, args)
            else:
                if _is_url(target):
                    import webbrowser

                    webbrowser.open(target)
                    msg = f"Opened {target}"
                else:
                    cmd = [target] + (args.split() if args else [])
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    msg = f"Launched {target}"

            success = "Could not find" not in msg
            return ToolResult(
                tool_name="app_launcher",
                content=msg,
                success=success,
                metadata={"target": target, "args": args},
            )
        except Exception as exc:
            return ToolResult(
                tool_name="app_launcher",
                content=f"Failed to launch '{target}': {exc}",
                success=False,
                metadata={"target": target},
            )


__all__ = ["AppLauncherTool"]
