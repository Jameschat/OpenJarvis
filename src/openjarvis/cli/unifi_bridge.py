"""Bridge between the UniFi Site Manager API (api.ui.com) and Mission Control.

Polls every ``POLL_INTERVAL`` seconds, caches the snapshot, fans out via SSE
to the HUD's ``/unifi_events`` channel, and emits activity-log lines on
state changes (site offline, device offline, firmware available, etc.).

Read-only by design — Mission Control v1 doesn't issue any writes back to
UniFi. Auth is a single API key sourced from ``OPENJARVIS_UNIFI_KEY`` env
var. Without the key, the bridge stays dormant and the HUD shows
"unifi offline" gracefully.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

POLL_INTERVAL = 45.0   # seconds — well inside UniFi's 100/min rate limit
API_BASE = "https://api.ui.com"
USER_AGENT = "OpenJarvis-MissionControl/1.0"


def _api_key() -> str:
    return os.environ.get("OPENJARVIS_UNIFI_KEY", "").strip()


# ---------------------------------------------------------------------------
# DNS-over-HTTPS fallback — bypasses the user's local resolver
# ---------------------------------------------------------------------------
# Many UniFi gateways act as their own DNS resolver and intermittently fail
# to resolve api.ui.com (their own service!) — possibly a caching quirk.
# When system DNS fails, we fall back to Cloudflare's DoH endpoint at
# 1.1.1.1, which is itself reachable by IP so it never depends on local DNS.

_DOH_URL = "https://1.1.1.1/dns-query"
_dns_cache: Dict[str, Tuple[str, float]] = {}     # host -> (ip, expires_at)
_DNS_TTL = 600                                     # cache resolutions for 10 min


def _ssl_context() -> ssl.SSLContext:
    """SSL context with certifi's CA bundle. Python on Windows doesn't use
    the OS cert store by default, so plain ``ssl.create_default_context()``
    fails verification. ``certifi`` is already a transitive dep of the project
    (urllib3 / requests / openai)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _https_request_via_ip(ip: str, hostname: str, path: str,
                          headers: Dict[str, str], timeout: int = 12
                          ) -> Tuple[Optional[int], Optional[bytes]]:
    """Make an HTTPS request to ``ip`` but with ``hostname`` for SNI + Host.
    Bypasses system DNS entirely. Returns (status_code, body_bytes) or
    (None, None) on failure. Implemented with raw socket + ssl.wrap_socket
    because http.client.HTTPSConnection doesn't expose server_hostname in
    its constructor across all Python versions."""
    try:
        sock = socket.create_connection((ip, 443), timeout=timeout)
        ssock = _ssl_context().wrap_socket(sock, server_hostname=hostname)
        # Build the HTTP request
        lines = [f"GET {path} HTTP/1.1", f"Host: {hostname}", "Connection: close"]
        for k, v in headers.items():
            if k.lower() == "host":
                continue   # already added
            lines.append(f"{k}: {v}")
        request = "\r\n".join(lines) + "\r\n\r\n"
        ssock.sendall(request.encode("utf-8"))
        # Read full response
        data = b""
        while True:
            chunk = ssock.recv(8192)
            if not chunk:
                break
            data += chunk
        ssock.close()
        # Parse status line + body
        sep = data.find(b"\r\n\r\n")
        if sep == -1:
            return (None, None)
        head = data[:sep].decode("ascii", errors="replace")
        body = data[sep + 4:]
        # Status: "HTTP/1.1 200 OK"
        status_line = head.split("\r\n", 1)[0].split(" ", 2)
        status = int(status_line[1]) if len(status_line) >= 2 else None
        # Handle chunked transfer encoding (Cloudflare DoH uses it)
        if "transfer-encoding: chunked" in head.lower():
            body = _dechunk(body)
        return (status, body)
    except Exception as exc:
        logger.debug("via-IP request to %s/%s failed: %s", ip, path, exc)
        return (None, None)


def _dechunk(body: bytes) -> bytes:
    """Decode HTTP/1.1 chunked transfer encoding to a flat byte string."""
    out = bytearray()
    i = 0
    while i < len(body):
        # Read chunk size line (hex digits ending in CRLF)
        nl = body.find(b"\r\n", i)
        if nl == -1:
            break
        try:
            size = int(body[i:nl].split(b";")[0], 16)
        except ValueError:
            break
        i = nl + 2
        if size == 0:
            break
        out.extend(body[i:i + size])
        i += size + 2   # skip trailing CRLF
    return bytes(out)


def _doh_resolve(hostname: str) -> Optional[str]:
    """Resolve a hostname via Cloudflare DoH (1.1.1.1). Caches for 10 min."""
    cached = _dns_cache.get(hostname)
    if cached and cached[1] > time.time():
        return cached[0]
    status, body = _https_request_via_ip(
        "1.1.1.1", "cloudflare-dns.com",
        f"/dns-query?name={urllib.parse.quote(hostname)}&type=A",
        {"accept": "application/dns-json"},
        timeout=8,
    )
    if status != 200 or not body:
        logger.debug("DoH lookup failed for %s (status %s)", hostname, status)
        return None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None
    for ans in data.get("Answer") or []:
        if ans.get("type") == 1 and ans.get("data"):
            ip = ans["data"]
            _dns_cache[hostname] = (ip, time.time() + _DNS_TTL)
            logger.info("unifi: resolved %s -> %s via Cloudflare DoH", hostname, ip)
            return ip
    return None


def _request_via_ip(hostname: str, ip: str, path: str, headers: Dict[str, str],
                    timeout: int = 12) -> Optional[Any]:
    """HTTPS request to ip with SNI=hostname, returning parsed JSON or None."""
    status, body = _https_request_via_ip(ip, hostname, path, headers, timeout)
    if status == 401:
        logger.warning("unifi: auth failed — check OPENJARVIS_UNIFI_KEY")
        return None
    if status == 429:
        logger.info("unifi: rate-limited, backing off")
        return None
    if status != 200 or not body:
        if status is not None:
            logger.warning("unifi: HTTP %s on %s (via IP %s)", status, path, ip)
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("unifi via-IP JSON parse failed: %s", exc)
        return None


def _fetch(path: str, timeout: int = 12) -> Optional[Any]:
    """GET ``api.ui.com/v1/<path>``. Tries system DNS first (fast path);
    if that fails (e.g. UniFi gateway resolver flapping), falls back to
    Cloudflare DoH + direct-IP HTTPS. Returns parsed JSON or None.
    """
    key = _api_key()
    if not key:
        return None
    headers = {
        "X-API-KEY":  key,
        "Accept":     "application/json",
        "User-Agent": USER_AGENT,
    }
    full_path = ("/" if not path.startswith("/") else "") + path.lstrip("/")
    full_path = "/v1" + full_path if not full_path.startswith("/v1") else full_path
    # Wait — the path arg already includes '/v1/...' from callers. Just use it.
    full_path = path if path.startswith("/") else "/" + path

    # --- Path 1: standard urllib using system DNS ---
    try:
        url = API_BASE + full_path
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            logger.warning("unifi: auth failed — check OPENJARVIS_UNIFI_KEY")
            return None
        if exc.code == 429:
            logger.info("unifi: rate-limited, backing off")
            return None
        logger.warning("unifi: HTTP %s on %s", exc.code, path)
        return None
    except Exception as exc:
        logger.debug("unifi system-DNS fetch failed: %s — trying DoH fallback", exc)

    # --- Path 2: DoH lookup + direct-IP HTTPS with SNI ---
    ip = _doh_resolve("api.ui.com")
    if not ip:
        logger.warning("unifi: DoH fallback couldn't resolve api.ui.com either")
        return None
    return _request_via_ip("api.ui.com", ip, full_path, headers, timeout=timeout)


# ---------------------------------------------------------------------------
# Snapshot building
# ---------------------------------------------------------------------------


def _summarise() -> Dict[str, Any]:
    """Pull all the data we need in one API call and return a single
    snapshot dict the HUD + activity log can consume.

    Uses ``GET /v1/sites`` which returns every site across every host the
    account owns — the response shape is documented at developer.ui.com.
    """
    if not _api_key():
        return {"online": False, "reason": "no API key set", "sites": [],
                "totals": {"sites": 0, "devices": 0, "clients": 0,
                           "devices_online": 0, "alerts": 0}}

    sites_resp = _fetch("/v1/sites")
    if sites_resp is None:
        # Differentiated: API was unreachable (DNS, timeout, network).
        # The HUD now sees online=False with a real reason, not phantom-online-zero.
        return {"online": False, "reason": "api.ui.com unreachable", "sites": [],
                "totals": {"sites": 0, "devices": 0, "clients": 0,
                           "devices_online": 0, "alerts": 0}}
    sites = sites_resp.get("data") or []

    sites_out: List[Dict[str, Any]] = []
    total_devices = total_online = total_clients = total_alerts = 0

    for s in sites:
        site_id = s.get("siteId") or s.get("id") or ""
        meta = s.get("meta") or {}
        stats = s.get("statistics") or {}
        counts = stats.get("counts") or {}
        gateway = stats.get("gateway") or {}
        wans = stats.get("wans") or {}
        percentages = stats.get("percentages") or {}

        # Friendly site name: meta.desc is often "Default" for every site,
        # so fall back to gateway-model + short id for disambiguation.
        desc = (meta.get("desc") or "").strip()
        gw_model = (gateway.get("shortname") or "").strip()
        if desc and desc.lower() != "default":
            site_name = desc
        elif gw_model:
            site_name = f"{desc or 'Default'} ({gw_model})"
        else:
            site_name = desc or site_id[:8]

        devices = counts.get("totalDevice") or 0
        offline = counts.get("offlineDevice") or 0
        online  = max(0, devices - offline)
        clients = (counts.get("wifiClient") or 0) + (counts.get("wiredClient") or 0)

        # Alerts = critical notifications + pending firmware updates
        alerts = (counts.get("criticalNotification") or 0) + \
                 (counts.get("pendingUpdateDevice") or 0)

        # WAN health — if any WAN has issues or 0 uptime we flag degraded/down
        wan_uptime_pct = percentages.get("wanUptime", 100)
        wan_issues = []
        for wan_name, wan_data in wans.items():
            if (wan_data or {}).get("wanIssues"):
                wan_issues.append(wan_name)

        if wan_uptime_pct == 0 and not wan_issues:
            internet_up = False
        elif wan_uptime_pct < 50:
            internet_up = False
        else:
            internet_up = True

        # Status priority: down > degraded > ok
        if not internet_up:
            status = "down"
        elif offline > 0 or wan_issues:
            status = "degraded"
        else:
            status = "ok"

        total_devices += devices
        total_online  += online
        total_clients += clients
        total_alerts  += alerts

        sites_out.append({
            "id":              site_id,
            "name":            site_name,
            "host_id":         s.get("hostId") or "",
            "gateway_model":   gw_model,
            "timezone":        meta.get("timezone") or "",
            "status":          status,
            "devices_total":   devices,
            "devices_online":  online,
            "devices_offline": offline,
            "clients":         clients,
            "alerts":          alerts,
            "internet_up":     internet_up,
            "wan_uptime_pct":  wan_uptime_pct,
            "wan_issues":      wan_issues,
        })

    # Sort: degraded/down first (so problems jump out), then by clients desc
    sites_out.sort(key=lambda s: (
        0 if s["status"] == "down" else 1 if s["status"] == "degraded" else 2,
        -s["clients"],
    ))

    return {
        "online": True,
        "ts":     time.time(),
        "sites":  sites_out,
        "totals": {
            "sites":          len(sites_out),
            "devices":        total_devices,
            "devices_online": total_online,
            "clients":        total_clients,
            "alerts":         total_alerts,
        },
    }


# ---------------------------------------------------------------------------
# State + SSE pub-sub
# ---------------------------------------------------------------------------


class _UnifiBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.snapshot: Dict[str, Any] = {"online": False, "sites": [],
                                          "totals": {"sites": 0, "devices": 0,
                                                     "clients": 0, "devices_online": 0,
                                                     "alerts": 0}}
        self._serialised = json.dumps(self.snapshot)
        self._clients: List[Any] = []

    def update(self, snapshot: Dict[str, Any]) -> None:
        serialised = json.dumps(snapshot)
        with self._lock:
            if serialised == self._serialised:
                return
            old = self.snapshot
            self.snapshot = snapshot
            self._serialised = serialised
            dead = []
            msg = ("data: " + serialised + "\n\n").encode("utf-8")
            for w in self._clients:
                try:
                    w.write(msg); w.flush()
                except Exception:
                    dead.append(w)
            for d in dead:
                self._clients.remove(d)
        # Emit per-site change events to the activity log
        try:
            _emit_changes(old, snapshot)
        except Exception:
            logger.exception("unifi change emission failed (non-fatal)")

    def subscribe(self, w: Any) -> None:
        with self._lock:
            self._clients.append(w)

    def unsubscribe(self, w: Any) -> None:
        with self._lock:
            self._clients = [c for c in self._clients if c is not w]

    def current(self) -> Dict[str, Any]:
        with self._lock:
            return self.snapshot


_bus = _UnifiBus()


def _emit_changes(old: Dict[str, Any], new: Dict[str, Any]) -> None:
    """When a site flips online/offline or a device count changes, fire a
    single, human-readable line into the vault event bus so the activity
    log shows it. We piggyback on the existing obsidian event channel for
    simplicity — events are tagged source='unifi' so the HUD can route them."""
    try:
        from openjarvis.tools import obsidian_brain as ob
    except Exception:
        return
    old_by_id = {s["id"]: s for s in (old.get("sites") or [])}
    for s in new.get("sites") or []:
        prev = old_by_id.get(s["id"])
        if prev is None:
            ob._emit_event("read", f"site joined: {s['name']}",
                           kind="unifi", source="unifi")
            continue
        if prev["status"] != s["status"]:
            ob._emit_event("read", f"{s['name']} → {s['status']}",
                           kind="unifi", source="unifi")
        if prev["devices_offline"] != s["devices_offline"]:
            delta = s["devices_offline"] - prev["devices_offline"]
            verb = "device went offline" if delta > 0 else "device came back online"
            ob._emit_event("read", f"{s['name']}: {verb} ({s['devices_offline']} down)",
                           kind="unifi", source="unifi")
        if (prev.get("alerts") or 0) != (s.get("alerts") or 0):
            ob._emit_event("read", f"{s['name']}: {s['alerts']} alert(s)",
                           kind="unifi", source="unifi")


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------


_thread: Optional[threading.Thread] = None


def _poll_loop() -> None:
    not_configured_logged = False
    while True:
        try:
            snap = _summarise()
            _bus.update(snap)
            if not snap.get("online") and not not_configured_logged:
                logger.info("unifi bridge dormant — set OPENJARVIS_UNIFI_KEY to enable")
                not_configured_logged = True
            elif snap.get("online") and not_configured_logged:
                logger.info("unifi bridge online — %d sites, %d devices",
                            snap["totals"]["sites"], snap["totals"]["devices"])
                not_configured_logged = False
        except Exception:
            logger.exception("unifi poll iteration crashed (continuing)")
        time.sleep(POLL_INTERVAL)


def start_unifi_bridge() -> None:
    """Start the background poller. Idempotent. Always safe to call —
    the bridge is dormant if no API key is set."""
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="unifi-bridge")
    _thread.start()


def get_snapshot() -> Dict[str, Any]:
    return _bus.current()


def subscribe(wfile: Any) -> None:
    _bus.subscribe(wfile)


def unsubscribe(wfile: Any) -> None:
    _bus.unsubscribe(wfile)


__all__ = ["start_unifi_bridge", "get_snapshot", "subscribe", "unsubscribe"]
