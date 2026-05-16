"""Sonos speaker control — play, pause, volume, groups, and favourites."""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_TIMEOUT = 3


_SONOS_CACHE = Path.home() / ".openjarvis" / "sonos.json"
_SONOS_DEFAULT = Path.home() / ".openjarvis" / "sonos_default.json"


def _get_default_speaker_name() -> Optional[str]:
    """Return the configured default speaker name, or None."""
    import json
    if _SONOS_DEFAULT.exists():
        try:
            data = json.loads(_SONOS_DEFAULT.read_text())
            return data.get("name", "") or None
        except Exception:
            pass
    return None


def _set_default_speaker_name(name: str) -> None:
    """Persist the default speaker name to disk."""
    import json
    _SONOS_DEFAULT.parent.mkdir(parents=True, exist_ok=True)
    _SONOS_DEFAULT.write_text(json.dumps({"name": name}, indent=2))


def _discover_speakers() -> List[Dict[str, str]]:
    """Find Sonos speakers via cached IPs + SSDP discovery."""
    import json

    speakers: List[Dict[str, str]] = []
    seen_ips: set = set()

    # 1. Try cached IPs first (instant)
    if _SONOS_CACHE.exists():
        try:
            cached = json.loads(_SONOS_CACHE.read_text())
            for entry in cached:
                ip = entry["ip"]
                try:
                    resp = httpx.get(f"http://{ip}:1400/status/zp", timeout=0.5)
                    if resp.status_code == 200:
                        name_m = re.search(r"<ZoneName>(.*?)</ZoneName>", resp.text)
                        name = name_m.group(1) if name_m else ip
                        speakers.append({"ip": ip, "name": name})
                        seen_ips.add(ip)
                except Exception:
                    pass
        except Exception:
            pass

    if speakers:
        return speakers

    # 2. SSDP multicast discovery
    SSDP_ADDR = "239.255.255.250"
    SSDP_PORT = 1900
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n"
        "\r\n"
    )
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(3)
        sock.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))
        while True:
            data, addr = sock.recvfrom(1024)
            ip = addr[0]
            if ip not in seen_ips:
                seen_ips.add(ip)
                try:
                    resp = httpx.get(f"http://{ip}:1400/status/zp", timeout=1)
                    name_m = re.search(r"<ZoneName>(.*?)</ZoneName>", resp.text)
                    name = name_m.group(1) if name_m else ip
                    speakers.append({"ip": ip, "name": name})
                except Exception:
                    pass
    except socket.timeout:
        pass
    except Exception:
        pass

    # 3. Fallback: quick scan of known Sonos IPs from earlier discovery
    if not speakers:
        local_ip = socket.gethostbyname(socket.gethostname())
        subnet = ".".join(local_ip.split(".")[:3])
        # Only check a handful of likely IPs
        for ip_suffix in [28, 83, 107, 199]:
            ip = f"{subnet}.{ip_suffix}"
            if ip in seen_ips:
                continue
            try:
                resp = httpx.get(f"http://{ip}:1400/status/zp", timeout=0.5)
                if resp.status_code == 200:
                    name_m = re.search(r"<ZoneName>(.*?)</ZoneName>", resp.text)
                    name = name_m.group(1) if name_m else ip
                    speakers.append({"ip": ip, "name": name})
            except Exception:
                pass

    # Cache results
    if speakers:
        _SONOS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SONOS_CACHE.write_text(json.dumps(speakers, indent=2))

    return speakers


# Cache discovered speakers
_speaker_cache: List[Dict[str, str]] = []


def _get_speakers() -> List[Dict[str, str]]:
    global _speaker_cache
    if not _speaker_cache:
        _speaker_cache = _discover_speakers()
    return _speaker_cache


def _find_speaker(name: str) -> Optional[Dict[str, str]]:
    """Find speaker by name (case-insensitive partial match)."""
    speakers = _get_speakers()
    name_lower = name.lower()
    # Exact match
    for s in speakers:
        if s["name"].lower() == name_lower:
            return s
    # Partial match
    for s in speakers:
        if name_lower in s["name"].lower():
            return s
    return None


def _resolve_coordinator_ip(ip: str) -> str:
    """Return the IP of the group coordinator for the zone containing ``ip``.

    When Sonos speakers are grouped, playback commands must be sent to the
    coordinator — sending them to a slave is silently ignored.  If the
    speaker is its own coordinator (ungrouped or it IS the coordinator),
    this returns ``ip`` unchanged.
    """
    import html

    try:
        resp = _soap_action(ip, "ZoneGroupTopology", "GetZoneGroupState")
    except Exception:
        return ip

    # The <ZoneGroupState> element contains entity-escaped DIDL-ish XML
    m = re.search(r"<ZoneGroupState>(.*?)</ZoneGroupState>", resp, re.DOTALL)
    if not m:
        return ip

    state = html.unescape(m.group(1))
    # Find each ZoneGroup with its coordinator UUID + member list
    for group_match in re.finditer(
        r'<ZoneGroup\s+Coordinator="([^"]+)"[^>]*>(.*?)</ZoneGroup>',
        state,
        re.DOTALL,
    ):
        coord_uuid = group_match.group(1)
        members_xml = group_match.group(2)
        members = re.findall(
            r'<ZoneGroupMember\s+UUID="([^"]+)"[^>]*Location="http://([^:]+):',
            members_xml,
        )
        # Is this speaker in this group?
        if not any(mem_ip == ip for _, mem_ip in members):
            continue
        # Find the coordinator's IP
        for uuid, mem_ip in members:
            if uuid == coord_uuid:
                return mem_ip
        break

    return ip


def _soap_action(ip: str, service: str, action: str, args: str = "") -> str:
    """Send a UPnP SOAP action to a Sonos speaker."""
    service_paths = {
        "AVTransport": "/MediaRenderer/AVTransport/Control",
        "RenderingControl": "/MediaRenderer/RenderingControl/Control",
        "ContentDirectory": "/MediaServer/ContentDirectory/Control",
        "ZoneGroupTopology": "/ZoneGroupTopology/Control",
    }
    service_urns = {
        "AVTransport": "urn:schemas-upnp-org:service:AVTransport:1",
        "RenderingControl": "urn:schemas-upnp-org:service:RenderingControl:1",
        "ContentDirectory": "urn:schemas-upnp-org:service:ContentDirectory:1",
        "ZoneGroupTopology": "urn:schemas-upnp-org:service:ZoneGroupTopology:1",
    }

    path = service_paths.get(service, service_paths["AVTransport"])
    urn = service_urns.get(service, service_urns["AVTransport"])
    url = f"http://{ip}:1400{path}"

    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{urn}">'
        f"{args}"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )

    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f"{urn}#{action}",
    }

    # Sonos sometimes resets connections under rapid-fire requests or
    # when DHCP changes its IP.  Retry once with a short backoff; on the
    # second failure, try invalidating the speaker cache and retrying one
    # more time with a freshly-discovered IP.
    import time as _time

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                url, content=body, headers=headers, timeout=_TIMEOUT
            )
            return resp.text
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, OSError) as exc:
            last_exc = exc
            if attempt == 0:
                _time.sleep(0.3)
                continue
            if attempt == 1:
                # Invalidate the speakers cache and rediscover — the
                # speaker's IP may have changed.
                try:
                    _time.sleep(0.5)
                    if _SONOS_CACHE.exists():
                        _SONOS_CACHE.unlink()
                    global _speaker_cache
                    _speaker_cache = []
                    fresh = _discover_speakers()
                    # Try to find a speaker whose name matches the same
                    # zone we were targeting — if we can't correlate it,
                    # just fall through to the final raise.
                    if fresh:
                        for s in fresh:
                            url = f"http://{s['ip']}:1400{path}"
                            try:
                                resp = httpx.post(
                                    url, content=body, headers=headers,
                                    timeout=_TIMEOUT,
                                )
                                return resp.text
                            except Exception:
                                continue
                except Exception:
                    pass
                break

    # All retries failed
    raise RuntimeError(
        f"Sonos connection failed after retries: {last_exc}"
    )


def _play(ip: str) -> str:
    _soap_action(ip, "AVTransport", "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")
    return "Playing"


def _pause(ip: str) -> str:
    _soap_action(ip, "AVTransport", "Pause", "<InstanceID>0</InstanceID>")
    return "Paused"


def _stop(ip: str) -> str:
    _soap_action(ip, "AVTransport", "Stop", "<InstanceID>0</InstanceID>")
    return "Stopped"


def _next_track(ip: str) -> str:
    _soap_action(ip, "AVTransport", "Next", "<InstanceID>0</InstanceID>")
    return "Skipped to next track"


def _prev_track(ip: str) -> str:
    _soap_action(ip, "AVTransport", "Previous", "<InstanceID>0</InstanceID>")
    return "Previous track"


def _set_volume(ip: str, volume: int) -> str:
    vol = max(0, min(100, volume))
    _soap_action(
        ip, "RenderingControl", "SetVolume",
        f"<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>{vol}</DesiredVolume>",
    )
    return f"Volume set to {vol}%"


def _get_volume(ip: str) -> int:
    resp = _soap_action(
        ip, "RenderingControl", "GetVolume",
        "<InstanceID>0</InstanceID><Channel>Master</Channel>",
    )
    m = re.search(r"<CurrentVolume>(\d+)</CurrentVolume>", resp)
    return int(m.group(1)) if m else 0


def _get_now_playing(ip: str) -> str:
    resp = _soap_action(
        ip, "AVTransport", "GetPositionInfo",
        "<InstanceID>0</InstanceID>",
    )
    title = re.search(r"dc:title&gt;(.*?)&lt;/dc:title", resp)
    artist = re.search(r"dc:creator&gt;(.*?)&lt;/dc:creator", resp)
    album = re.search(r"upnp:album&gt;(.*?)&lt;/upnp:album", resp)

    parts = []
    if title:
        parts.append(title.group(1))
    if artist:
        parts.append(f"by {artist.group(1)}")
    if album:
        parts.append(f"from {album.group(1)}")

    return " ".join(parts) if parts else "Nothing playing"


_DIDL_NS = {
    "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
    "r": "urn:schemas-rinconnetworks-com:metadata-1-0/",
}


def _browse_favourites(ip: str) -> List[Dict[str, str]]:
    """Browse Sonos favourites (FV:2) and return a structured list.

    Each entry has:
        name:     human-readable title
        uri:      res URI to play
        metadata: complete DIDL-Lite XML (for SetAVTransportURI)
        protocol: the res element's protocolInfo attribute
    """
    import html
    from xml.etree import ElementTree as ET

    resp = _soap_action(
        ip,
        "ContentDirectory",
        "Browse",
        "<ObjectID>FV:2</ObjectID>"
        "<BrowseFlag>BrowseDirectChildren</BrowseFlag>"
        "<Filter>*</Filter>"
        "<StartingIndex>0</StartingIndex>"
        "<RequestedCount>100</RequestedCount>"
        "<SortCriteria></SortCriteria>",
    )

    # Extract the <Result> element's text — it contains HTML-escaped DIDL-Lite XML
    try:
        soap_root = ET.fromstring(resp)
    except ET.ParseError:
        return []

    result_text = ""
    # Walk the SOAP body looking for a <Result> element
    for elem in soap_root.iter():
        tag = elem.tag.split("}")[-1]  # strip namespace
        if tag == "Result" and elem.text:
            result_text = elem.text
            break

    if not result_text:
        return []

    # The Result is already the un-escaped DIDL-Lite XML string at this point
    # (ElementTree decodes entities when it reads element text content)
    try:
        didl_root = ET.fromstring(result_text)
    except ET.ParseError:
        return []

    favourites: List[Dict[str, str]] = []
    for item in didl_root.findall("didl:item", _DIDL_NS):
        title_el = item.find("dc:title", _DIDL_NS)
        res_el = item.find("didl:res", _DIDL_NS)
        if title_el is None or res_el is None or not res_el.text:
            continue

        # Some favourites store the real playback metadata in r:resMD,
        # which needs to be used instead of rebuilding it from the FV:2 item.
        res_md_el = item.find("r:resMD", _DIDL_NS)
        if res_md_el is not None and res_md_el.text:
            metadata = res_md_el.text
        else:
            # Fall back to a minimal DIDL wrapper around the item we have
            metadata = ET.tostring(didl_root, encoding="unicode")

        protocol = res_el.get("protocolInfo", "")

        favourites.append(
            {
                "name": title_el.text or "",
                "uri": res_el.text,
                "metadata": metadata,
                "protocol": protocol,
            }
        )

    return favourites


def _xml_escape(text: str) -> str:
    """Escape XML entities for use inside a SOAP argument."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _get_favourites(ip: str) -> List[str]:
    """Return the list of favourite titles for display."""
    return [f["name"] for f in _browse_favourites(ip)]


def _play_favourite(ip: str, name: str) -> str:
    """Find a favourite by fuzzy name match and play it.

    If no favourite matches and Spotify is configured, falls back to
    searching Spotify and playing the top result as an arbitrary track.
    """
    # Route playback commands to the group coordinator
    ip = _resolve_coordinator_ip(ip)

    favourites = _browse_favourites(ip)
    name_lower = name.lower().strip()

    # 1. Exact match
    chosen = None
    for fav in favourites:
        if fav["name"].lower() == name_lower:
            chosen = fav
            break
    # 2. Starts-with
    if chosen is None:
        for fav in favourites:
            if fav["name"].lower().startswith(name_lower):
                chosen = fav
                break
    # 3. Substring
    if chosen is None:
        for fav in favourites:
            if name_lower in fav["name"].lower():
                chosen = fav
                break

    # 4. Fall back to Spotify search
    if chosen is None:
        try:
            from openjarvis.tools.spotify import get_client

            client = get_client()
            if client.is_configured:
                return _search_and_play_spotify(ip, name)
        except Exception as exc:
            return (
                f"I couldn't find a favourite matching '{name}', and Spotify "
                f"search failed: {exc}"
            )

        available = (
            ", ".join(f["name"] for f in favourites[:6])
            if favourites
            else "(none)"
        )
        return (
            f"I couldn't find a favourite matching '{name}'. "
            f"Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to search "
            f"the Spotify catalog. Available favourites: {available}."
        )

    # SetAVTransportURI requires the URI and metadata both escaped inside
    # the SOAP body.  Use the item's stored resMD for Spotify etc.
    uri_escaped = _xml_escape(chosen["uri"])
    metadata_escaped = _xml_escape(chosen["metadata"])

    _soap_action(
        ip,
        "AVTransport",
        "SetAVTransportURI",
        "<InstanceID>0</InstanceID>"
        f"<CurrentURI>{uri_escaped}</CurrentURI>"
        f"<CurrentURIMetaData>{metadata_escaped}</CurrentURIMetaData>",
    )
    _play(ip)
    return f"Playing {chosen['name']}"


# ---------------------------------------------------------------------------
# Spotify-on-Sonos: synthesise URIs + metadata for arbitrary Spotify content
# ---------------------------------------------------------------------------

_SONOS_SPOTIFY_CACHE = Path.home() / ".openjarvis" / "sonos_spotify.json"


def _split_song_and_artist(query: str) -> tuple[str, str]:
    """Split a 'song by artist' query into (song, artist).

    Returns (query, '') if there's no 'by' in the query.
    """
    # Match "X by Y" where Y is at the end
    m = re.match(r"^(.+?)\s+by\s+(.+?)\s*$", query.strip(), re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return query.strip(), ""


def _query_tokens(s: str) -> set[str]:
    """Lowercase alphanumeric word tokens, minus common stopwords."""
    stop = {"the", "a", "an", "of", "to", "in", "on", "at", "for", "is", "it"}
    toks = re.findall(r"[a-z0-9]+", s.lower())
    return {t for t in toks if t not in stop and len(t) > 1}


_BAD_TITLE_WORDS = {
    "karaoke", "instrumental", "cover", "tribute", "performed",
    "remix", "mashup", "medley", "edit", "demo", "rehearsal",
    "workout", "nursery", "lullaby", "piano", "acoustic", "sped",
    "slowed", "reverb", "8d", "nightcore", "8-bit", "chipmunk",
    "sample", "beat", "inspired",
}


def _score_track(track, song_query: str, artist_query: str) -> float:
    """Score a track against the user's intent. Higher = better match."""
    title_tokens = _query_tokens(track.name)
    song_tokens = _query_tokens(song_query)
    artist_query_tokens = _query_tokens(artist_query) if artist_query else set()

    # --- Title match ---
    if song_tokens:
        title_overlap = len(title_tokens & song_tokens) / len(song_tokens)
    else:
        title_overlap = 0.0

    # --- Artist match ---
    # Compute per-artist overlap and take the MAX, so a track with multiple
    # artists doesn't get diluted. Also award bonus for near-exact match.
    best_artist_overlap = 0.0
    artist_exact_match = False
    if artist_query_tokens:
        artist_q_norm = " ".join(sorted(artist_query_tokens))
        for a in track.artists:
            a_tokens = _query_tokens(a)
            if not a_tokens:
                continue
            # Token overlap relative to the query
            overlap = (
                len(a_tokens & artist_query_tokens)
                / len(artist_query_tokens)
            )
            if overlap > best_artist_overlap:
                best_artist_overlap = overlap
            # Exact-set match (e.g. "john lennon" == "john lennon")
            if " ".join(sorted(a_tokens)) == artist_q_norm:
                artist_exact_match = True

    # --- Penalties ---
    penalty = 0.0

    # Length penalty (avoid medleys, "live at X" expansions)
    if song_tokens and len(title_tokens) > len(song_tokens) * 4:
        penalty += 0.2

    # Big penalty for cover/tribute/karaoke/live words in the title
    if title_tokens & _BAD_TITLE_WORDS:
        penalty += 0.35

    # "- Live" variants — check the raw name for " - Live"
    if " - live" in track.name.lower() or "(live" in track.name.lower():
        penalty += 0.25

    # Penalty when an artist was specified but the match is weak
    if artist_query_tokens and best_artist_overlap < 0.5:
        penalty += 0.5

    # --- Bonuses ---
    bonus = 0.0
    if artist_exact_match:
        bonus += 0.25

    # --- Combine ---
    if artist_query_tokens:
        score = (title_overlap * 0.55) + (best_artist_overlap * 0.45)
    else:
        score = title_overlap

    return max(0.0, score + bonus - penalty)


def _search_and_play_spotify(ip: str, query: str) -> str:
    """Search Spotify for a track/artist/album/playlist and play the best match.

    Spotify's search with ``limit=1`` returns non-deterministic / trending
    results that often don't match the user's intent.  We always fetch a
    batch (limit=10), then score locally for title + artist overlap and
    pick the best match.
    """
    from openjarvis.tools.spotify import get_client

    client = get_client()
    if not client.is_configured:
        return (
            "Spotify is not configured. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET environment variables."
        )

    q = query.strip().lower()

    # Entity-prefixed queries: "artist X", "album X", "playlist X"
    if q.startswith("artist "):
        artist_name = query.strip()[7:]
        tracks = client.search_track(f"artist:{artist_name}", limit=10)
        if not tracks:
            return f"No tracks found for artist '{artist_name}'."
        best = max(
            tracks,
            key=lambda t: _score_track(t, "", artist_name),
        )
        title = f"{best.name} by {', '.join(best.artists)}"
        return _play_spotify_on_sonos(ip, best.uri, title, "track")

    if q.startswith("playlist "):
        playlist_name = query.strip()[9:]
        playlists = client.search_playlist(playlist_name, limit=5)
        if not playlists:
            return f"No Spotify playlist matching '{playlist_name}' found."
        pl = playlists[0]  # playlist search is usually accurate
        return _play_spotify_on_sonos(ip, pl.uri, pl.name, "playlist")

    if q.startswith("album "):
        album_name = query.strip()[6:]
        albums = client.search_album(album_name, limit=5)
        if not albums:
            return f"No Spotify album matching '{album_name}' found."
        album = albums[0]
        title = f"{album.name} by {', '.join(album.artists)}"
        return _play_spotify_on_sonos(ip, album.uri, title, "album")

    # Default: search tracks, scoring top-10 results for best match.
    song, artist = _split_song_and_artist(query)

    tracks = client.search_track(query, limit=10)
    if not tracks:
        # Try the song portion alone
        if song and song != query:
            tracks = client.search_track(song, limit=10)
    if not tracks:
        return f"I couldn't find anything on Spotify for '{query}'."

    # Score every candidate and pick the best
    scored = [(t, _score_track(t, song or query, artist)) for t in tracks]
    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]

    title = f"{best.name} by {', '.join(best.artists)}"
    return _play_spotify_on_sonos(ip, best.uri, title, "track")


def _detect_spotify_service_info(ip: str) -> Optional[Dict[str, str]]:
    """Read an existing Spotify favourite to learn the service sid, sn, and desc.

    Returns a dict with ``sid``, ``sn``, and ``desc`` (the full
    ``SA_RINCON<...>`` descriptor), or None if no Spotify favourite is
    configured on the bridge.
    """
    import json

    if _SONOS_SPOTIFY_CACHE.exists():
        try:
            return json.loads(_SONOS_SPOTIFY_CACHE.read_text())
        except Exception:
            pass

    favourites = _browse_favourites(ip)
    for fav in favourites:
        uri = fav.get("uri", "")
        meta = fav.get("metadata", "")
        if "spotify" not in uri.lower():
            continue

        # Pull sid and sn out of the URI query string
        sid_m = re.search(r"[?&]sid=(\d+)", uri)
        sn_m = re.search(r"[?&]sn=(\d+)", uri)
        if not sid_m or not sn_m:
            continue

        # Pull the SA_RINCON<...> descriptor out of metadata
        desc_m = re.search(
            r'<desc[^>]*nameSpace="urn:schemas-rinconnetworks-com:metadata-1-0/"[^>]*>'
            r"([^<]+)</desc>",
            meta,
        )
        if not desc_m:
            continue

        info = {
            "sid": sid_m.group(1),
            "sn": sn_m.group(1),
            "desc": desc_m.group(1),
        }
        try:
            _SONOS_SPOTIFY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _SONOS_SPOTIFY_CACHE.write_text(json.dumps(info, indent=2))
        except Exception:
            pass
        return info

    return None


def _build_spotify_track_uri(track_id: str, sid: str, sn: str) -> str:
    """Build the Sonos URI for a Spotify track."""
    return f"x-sonos-spotify:spotify%3atrack%3a{track_id}?sid={sid}&flags=8224&sn={sn}"


def _build_spotify_album_uri(album_id: str, sid: str, sn: str) -> str:
    """Build the Sonos URI for a Spotify album (as a cpcontainer)."""
    return (
        f"x-rincon-cpcontainer:1004206cspotify%3aalbum%3a{album_id}"
        f"?sid={sid}&flags=8300&sn={sn}"
    )


def _build_spotify_playlist_uri(playlist_id: str, sid: str, sn: str) -> str:
    """Build the Sonos URI for a Spotify playlist (as a cpcontainer)."""
    return (
        f"x-rincon-cpcontainer:1006206cspotify%3aplaylist%3a{playlist_id}"
        f"?sid={sid}&flags=8300&sn={sn}"
    )


def _build_spotify_metadata(
    title: str, item_kind: str, spotify_id: str, desc: str
) -> str:
    """Build the DIDL-Lite metadata string for SetAVTransportURI.

    Args:
        title: human-readable title.
        item_kind: 'track' | 'album' | 'playlist'.
        spotify_id: the Spotify content ID (not the full URI).
        desc: the SA_RINCON<...> service descriptor.
    """
    import html

    safe_title = html.escape(title)

    if item_kind == "track":
        item_id = f"10032020spotify%3atrack%3a{spotify_id}"
        parent_id = "dummy"
        upnp_class = "object.item.audioItem.musicTrack"
    elif item_kind == "album":
        item_id = f"1004206cspotify%3aalbum%3a{spotify_id}"
        parent_id = "dummy"
        upnp_class = "object.container.album.musicAlbum"
    elif item_kind == "playlist":
        item_id = f"1006206cspotify%3aplaylist%3a{spotify_id}"
        parent_id = "dummy"
        upnp_class = "object.container.playlistContainer"
    else:
        raise ValueError(f"Unknown item_kind: {item_kind}")

    return (
        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
        f'<item id="{item_id}" parentID="{parent_id}" restricted="true">'
        f"<dc:title>{safe_title}</dc:title>"
        f"<upnp:class>{upnp_class}</upnp:class>"
        f'<desc id="cdudn" '
        'nameSpace="urn:schemas-rinconnetworks-com:metadata-1-0/">'
        f"{desc}</desc>"
        "</item></DIDL-Lite>"
    )


def _play_spotify_on_sonos(
    ip: str,
    spotify_uri: str,
    title: str,
    item_kind: str,
) -> str:
    """Play an arbitrary Spotify URI on a Sonos speaker.

    Args:
        ip: speaker IP address (may be a slave in a group — we'll route
            to the coordinator automatically).
        spotify_uri: the ``spotify:track:xxx`` / ``spotify:album:xxx`` /
            ``spotify:playlist:xxx`` URI from the Spotify Web API.
        title: human-readable title for metadata.
        item_kind: 'track' | 'album' | 'playlist'.
    """
    # Playback commands must go to the zone coordinator, not a slave
    ip = _resolve_coordinator_ip(ip)

    info = _detect_spotify_service_info(ip)
    if info is None:
        return (
            "I can't play Spotify on Sonos yet — please link Spotify to Sonos "
            "in the Sonos app first, and save any track as a favourite so I "
            "can learn your service ID."
        )

    spotify_id = spotify_uri.split(":")[-1]

    if item_kind == "track":
        sonos_uri = _build_spotify_track_uri(spotify_id, info["sid"], info["sn"])
    elif item_kind == "album":
        sonos_uri = _build_spotify_album_uri(spotify_id, info["sid"], info["sn"])
    elif item_kind == "playlist":
        sonos_uri = _build_spotify_playlist_uri(
            spotify_id, info["sid"], info["sn"]
        )
    else:
        return f"Unknown Spotify content type: {item_kind}"

    metadata = _build_spotify_metadata(
        title, item_kind, spotify_id, info["desc"]
    )

    uri_esc = _xml_escape(sonos_uri)
    meta_esc = _xml_escape(metadata)

    _soap_action(
        ip,
        "AVTransport",
        "SetAVTransportURI",
        "<InstanceID>0</InstanceID>"
        f"<CurrentURI>{uri_esc}</CurrentURI>"
        f"<CurrentURIMetaData>{meta_esc}</CurrentURIMetaData>",
    )
    _play(ip)
    return f"Playing {title}"


@ToolRegistry.register("sonos")
class SonosTool(BaseTool):
    """Control Sonos speakers — play, pause, volume, favourites."""

    tool_id = "sonos"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="sonos",
            description=(
                "Control Sonos speakers on the local network. "
                "Actions: play, pause, stop, next, previous, volume_up, volume_down, "
                "set_volume, now_playing, favourites, play_favourite, list, "
                "set_default (remember a speaker as the default for ungrouped commands). "
                "Target a specific speaker by room name or control all."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "play", "pause", "stop", "next", "previous",
                            "volume_up", "volume_down", "set_volume",
                            "now_playing", "favourites", "play_favourite",
                            "list", "set_default",
                        ],
                        "description": "Action to perform.",
                    },
                    "speaker": {
                        "type": "string",
                        "description": "Speaker/room name (e.g. 'Lounge', 'Bedroom'). Default: first found.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Volume level (0-100) for set_volume, or favourite name for play_favourite.",
                    },
                },
                "required": ["action"],
            },
            category="media",
            requires_confirmation=False,
            timeout_seconds=15.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "").strip().lower()
        speaker_name = params.get("speaker", "").strip()
        value = params.get("value", "").strip()

        try:
            speakers = _get_speakers()
            if not speakers:
                return ToolResult(
                    tool_name="sonos",
                    content="No Sonos speakers found on the network.",
                    success=False,
                )

            if action == "list":
                lines = ["Sonos speakers:\n"]
                default_name = _get_default_speaker_name() or ""
                for s in speakers:
                    vol = _get_volume(s["ip"])
                    np = _get_now_playing(s["ip"])
                    marker = "  (default)" if s["name"] == default_name else ""
                    lines.append(f"  {s['name']}: vol {vol}% — {np}{marker}")
                return ToolResult(tool_name="sonos", content="\n".join(lines), success=True)

            if action == "set_default":
                # Speaker name comes in via the `speaker` param
                target = speaker_name or value
                if not target:
                    return ToolResult(
                        tool_name="sonos",
                        content="Specify which speaker to set as default.",
                        success=False,
                    )
                match = _find_speaker(target)
                if not match:
                    return ToolResult(
                        tool_name="sonos",
                        content=(
                            f"No speaker matching '{target}'. "
                            f"Available: {', '.join(s['name'] for s in speakers)}"
                        ),
                        success=False,
                    )
                _set_default_speaker_name(match["name"])
                return ToolResult(
                    tool_name="sonos",
                    content=f"{match['name']} is now the default Sonos speaker.",
                    success=True,
                )

            # Find target speaker
            if speaker_name:
                speaker = _find_speaker(speaker_name)
                if not speaker:
                    return ToolResult(
                        tool_name="sonos",
                        content=f"No speaker matching '{speaker_name}'. Available: {', '.join(s['name'] for s in speakers)}",
                        success=False,
                    )
            else:
                # Check for configured default speaker
                default_name = _get_default_speaker_name()
                speaker = None
                if default_name:
                    speaker = _find_speaker(default_name)
                if speaker is None:
                    speaker = speakers[0]

            ip = speaker["ip"]
            name = speaker["name"]

            if action == "play":
                msg = _play(ip)
            elif action == "pause":
                msg = _pause(ip)
            elif action == "stop":
                msg = _stop(ip)
            elif action == "next":
                msg = _next_track(ip)
            elif action == "previous":
                msg = _prev_track(ip)
            elif action == "volume_up":
                vol = min(100, _get_volume(ip) + 10)
                msg = _set_volume(ip, vol)
            elif action == "volume_down":
                vol = max(0, _get_volume(ip) - 10)
                msg = _set_volume(ip, vol)
            elif action == "set_volume":
                try:
                    vol = int(value)
                except ValueError:
                    return ToolResult(tool_name="sonos", content="Specify a volume 0-100.", success=False)
                msg = _set_volume(ip, vol)
            elif action == "now_playing":
                np = _get_now_playing(ip)
                msg = f"{name}: {np}"
            elif action == "favourites":
                favs = _get_favourites(ip)
                msg = f"Favourites: {', '.join(favs)}" if favs else "No favourites found."
            elif action == "play_favourite":
                if not value:
                    return ToolResult(tool_name="sonos", content="Specify a favourite name.", success=False)
                msg = _play_favourite(ip, value)
            else:
                return ToolResult(tool_name="sonos", content=f"Unknown action: {action}", success=False)

            return ToolResult(
                tool_name="sonos",
                content=f"{name}: {msg}",
                success=True,
                metadata={"speaker": name, "action": action},
            )

        except Exception as exc:
            return ToolResult(tool_name="sonos", content=f"Sonos error: {exc}", success=False)


__all__ = ["SonosTool"]
