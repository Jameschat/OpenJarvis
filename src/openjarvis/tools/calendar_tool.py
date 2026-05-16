"""Outlook Calendar tool — read schedule via Microsoft Graph API with device flow auth."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_CONFIG_DIR = Path.home() / ".openjarvis"
_TOKEN_CACHE = _CONFIG_DIR / "ms_token_cache.json"

# Microsoft public client for device code flow (no app registration needed)
# Uses the "Microsoft Graph Command Line Tools" public client ID
_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_AUTHORITY = "https://login.microsoftonline.com/common"
_SCOPES = ["Calendars.Read"]
_GRAPH_URL = "https://graph.microsoft.com/v1.0"


def _get_msal_app():
    """Create MSAL public client with token cache."""
    import msal

    cache = msal.SerializableTokenCache()
    if _TOKEN_CACHE.exists():
        cache.deserialize(_TOKEN_CACHE.read_text())

    app = msal.PublicClientApplication(
        _CLIENT_ID, authority=_AUTHORITY, token_cache=cache,
    )
    return app, cache


def _save_cache(cache) -> None:
    if cache.has_state_changed:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE.write_text(cache.serialize())


def _get_token() -> Optional[str]:
    """Get a valid access token (from cache or refresh)."""
    app, cache = _get_msal_app()

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    return None


def _login() -> str:
    """Interactive device code login. Opens browser automatically."""
    import webbrowser

    app, cache = _get_msal_app()

    flow = app.initiate_device_flow(scopes=_SCOPES)
    if "user_code" not in flow:
        return "Failed to start login flow."

    code = flow.get("user_code", "")
    url = flow.get("verification_uri", "https://microsoft.com/devicelogin")

    # Copy code to clipboard
    try:
        import subprocess
        subprocess.run(
            ["powershell.exe", "-Command", f"Set-Clipboard -Value '{code}'"],
            timeout=5, capture_output=True,
        )
    except Exception:
        pass

    # Open the login page automatically
    webbrowser.open(url)

    # This blocks until auth completes (timeout ~15 min)
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" in result:
        _save_cache(cache)
        return "Logged in to Microsoft. Calendar access enabled."
    else:
        error = result.get("error_description", result.get("error", "Unknown error"))
        return f"Login failed: {error}"


def _graph_get(path: str, token: str, params: Optional[Dict] = None) -> Any:
    """Make a GET request to Microsoft Graph."""
    resp = httpx.get(
        f"{_GRAPH_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _format_events(events: List[Dict], label: str) -> str:
    """Format calendar events into readable text."""
    if not events:
        return f"{label}: No events scheduled."

    lines = [f"{label}:\n"]
    for ev in events:
        subj = ev.get("subject", "(No subject)")
        start = ev.get("start", {}).get("dateTime", "")
        end = ev.get("end", {}).get("dateTime", "")
        location = ev.get("location", {}).get("displayName", "")
        is_all_day = ev.get("isAllDay", False)

        if is_all_day:
            lines.append(f"  All day: {subj}")
        else:
            try:
                st = datetime.fromisoformat(start.replace("Z", "+00:00"))
                en = datetime.fromisoformat(end.replace("Z", "+00:00"))
                lines.append(f"  {st.strftime('%H:%M')}-{en.strftime('%H:%M')}: {subj}")
            except Exception:
                lines.append(f"  {subj}")

        if location:
            lines[-1] += f" ({location})"

    return "\n".join(lines)


@ToolRegistry.register("calendar")
class CalendarTool(BaseTool):
    """Read Outlook calendar entries via Microsoft Graph."""

    tool_id = "calendar"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar",
            description=(
                "Read the user's Outlook/Microsoft 365 calendar. "
                "Actions: 'today' (today's schedule), 'tomorrow', "
                "'week' (next 7 days), 'login' (authenticate with Microsoft). "
                "Requires one-time login via device code on first use."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["today", "tomorrow", "week", "login"],
                        "description": "What to show: today, tomorrow, week, or login to authenticate.",
                    },
                },
                "required": ["action"],
            },
            category="productivity",
            requires_confirmation=False,
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "today").strip().lower()

        if action == "login":
            try:
                msg = _login()
                return ToolResult(tool_name="calendar", content=msg, success="Logged in" in msg)
            except Exception as exc:
                return ToolResult(tool_name="calendar", content=f"Login error: {exc}", success=False)

        # Get token
        token = _get_token()
        if not token:
            return ToolResult(
                tool_name="calendar",
                content=(
                    "Not logged in to Microsoft. "
                    "Say 'Jarvis, login to calendar' to authenticate."
                ),
                success=False,
            )

        try:
            now = datetime.utcnow()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            if action == "today":
                start = today_start.isoformat() + "Z"
                end = (today_start + timedelta(days=1)).isoformat() + "Z"
                label = f"Today ({now.strftime('%A %d %B')})"
            elif action == "tomorrow":
                start = (today_start + timedelta(days=1)).isoformat() + "Z"
                end = (today_start + timedelta(days=2)).isoformat() + "Z"
                tmrw = today_start + timedelta(days=1)
                label = f"Tomorrow ({tmrw.strftime('%A %d %B')})"
            elif action == "week":
                start = today_start.isoformat() + "Z"
                end = (today_start + timedelta(days=7)).isoformat() + "Z"
                label = "Next 7 days"
            else:
                return ToolResult(
                    tool_name="calendar",
                    content=f"Unknown action: {action}",
                    success=False,
                )

            data = _graph_get(
                "/me/calendarview",
                token,
                params={
                    "startDateTime": start,
                    "endDateTime": end,
                    "$orderby": "start/dateTime",
                    "$top": "50",
                    "$select": "subject,start,end,location,isAllDay",
                },
            )

            events = data.get("value", [])
            content = _format_events(events, label)

            return ToolResult(
                tool_name="calendar",
                content=content,
                success=True,
                metadata={"action": action, "event_count": len(events)},
            )

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return ToolResult(
                    tool_name="calendar",
                    content="Session expired. Say 'Jarvis, login to calendar' to re-authenticate.",
                    success=False,
                )
            return ToolResult(tool_name="calendar", content=f"Calendar error: {exc}", success=False)
        except Exception as exc:
            return ToolResult(tool_name="calendar", content=f"Calendar error: {exc}", success=False)


__all__ = ["CalendarTool"]
