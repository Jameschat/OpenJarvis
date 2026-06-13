"""WhatsApp pairing flow (Phase 8 #1).

The Baileys bridge already renders the QR to its own stderr via
``qrcode-terminal`` and emits structured JSON events on stdout. So pairing
just spawns the bridge with stderr INHERITED (QR appears in the operator's
terminal) and watches stdout for the ``status: connected`` event, then
reports the env vars to wire up notifications.

The pure event/decision logic here is unit-tested; the actual scan is the
operator's (needs their phone), so ``run_pairing`` is a thin orchestrator.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BRIDGE_SRC = Path(__file__).resolve().parent / "whatsapp_baileys_bridge"
_RUNTIME_DIR = Path.home() / ".openjarvis" / "whatsapp_baileys_bridge"


@dataclass
class PairingState:
    """Accumulated state across bridge events. Pure — drive it with parsed
    events and read the verdict."""

    qr_shown: bool = False
    connected: bool = False
    jid: Optional[str] = None
    error: Optional[str] = None
    events: List[str] = field(default_factory=list)

    def apply(self, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")
        self.events.append(etype)
        if etype == "qr":
            self.qr_shown = True
        elif etype == "status" and event.get("status") == "connected":
            self.connected = True
        elif etype == "self" and event.get("jid"):
            # bridge announces its own jid once authenticated
            self.jid = str(event.get("jid"))
        elif etype == "error":
            self.error = str(event.get("message") or "bridge error")

    @property
    def done(self) -> bool:
        return self.connected or (self.error is not None)


def parse_bridge_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse one stdout line as a bridge event, or None if not JSON."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


@dataclass
class PreflightResult:
    ok: bool
    reason: str = ""
    bridge_js: Optional[Path] = None


def preflight(build_if_missing: bool = True) -> PreflightResult:
    """Check node + a compiled bridge are present, building the bridge if
    needed. Returns the path to the runnable dist/bridge.js."""
    if shutil.which("node") is None:
        return PreflightResult(False, "Node.js not found on PATH (install Node 22+).")
    if shutil.which("npm") is None:
        return PreflightResult(False, "npm not found on PATH.")

    src_bridge = _BRIDGE_SRC / "dist" / "bridge.js"
    if not src_bridge.exists():
        if not build_if_missing:
            return PreflightResult(False, f"Bridge not compiled: {src_bridge} missing.")
        try:
            if not (_BRIDGE_SRC / "node_modules").exists():
                subprocess.run(["npm", "install"], cwd=str(_BRIDGE_SRC), check=True,
                               capture_output=True, timeout=600)
            subprocess.run(["npm", "run", "build"], cwd=str(_BRIDGE_SRC), check=True,
                           capture_output=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return PreflightResult(False, f"Bridge build failed: {exc}")
        if not src_bridge.exists():
            return PreflightResult(False, "Bridge build produced no dist/bridge.js.")
    return PreflightResult(True, bridge_js=src_bridge)


def _stage_runtime(bridge_js: Path) -> Path:
    """Copy the compiled bridge + node_modules into the runtime dir the
    channel uses, so a later `jarvis serve` finds it ready."""
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        src = _BRIDGE_SRC / name
        if src.exists():
            shutil.copy2(src, _RUNTIME_DIR / name)
    dist_dst = _RUNTIME_DIR / "dist"
    if dist_dst.exists():
        shutil.rmtree(dist_dst)
    shutil.copytree(bridge_js.parent, dist_dst)
    nm_src = _BRIDGE_SRC / "node_modules"
    nm_dst = _RUNTIME_DIR / "node_modules"
    if nm_src.exists() and not nm_dst.exists():
        shutil.copytree(nm_src, nm_dst)
    return _RUNTIME_DIR / "dist" / "bridge.js"


def run_pairing(timeout_s: int = 120, build_if_missing: bool = True) -> Dict[str, Any]:
    """Run the interactive pairing. The QR renders to this process's stderr
    (inherited from the bridge). Returns a result dict; never raises."""
    pf = preflight(build_if_missing=build_if_missing)
    if not pf.ok:
        return {"paired": False, "reason": pf.reason}

    assert pf.bridge_js is not None
    try:
        runtime_bridge = _stage_runtime(pf.bridge_js)
    except OSError as exc:
        return {"paired": False, "reason": f"could not stage bridge runtime: {exc}"}

    auth_dir = str(_RUNTIME_DIR / "auth")
    Path(auth_dir).mkdir(parents=True, exist_ok=True)

    state = PairingState()
    try:
        proc = subprocess.Popen(
            ["node", str(runtime_bridge), "--auth-dir", auth_dir],
            stdout=subprocess.PIPE,
            stderr=None,  # inherit — bridge renders the scannable QR here
            text=True,
            bufsize=1,
        )
    except Exception as exc:  # noqa: BLE001
        return {"paired": False, "reason": f"failed to start bridge: {exc}"}

    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline and proc.stdout is not None:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            event = parse_bridge_line(line)
            if event:
                state.apply(event)
                if state.done:
                    break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass

    if state.connected:
        return {"paired": True, "jid": state.jid, "auth_dir": auth_dir}
    return {
        "paired": False,
        "reason": state.error or ("timed out — QR not scanned in time" if not state.qr_shown
                                  else "scan not completed before timeout"),
        "qr_shown": state.qr_shown,
    }


__all__ = ["PairingState", "parse_bridge_line", "preflight", "run_pairing"]
