"""Spotify Web API integration — search the public catalog via Client Credentials.

Uses the Client Credentials OAuth flow (app-only auth, no user login required).
You need a Spotify developer app:

    1. Go to https://developer.spotify.com/dashboard
    2. Create an app (any name, any redirect URI)
    3. Copy the Client ID and Client Secret
    4. Set them as environment variables:
         SPOTIFY_CLIENT_ID
         SPOTIFY_CLIENT_SECRET

This module only needs those two values — no redirect URLs, no user OAuth.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_TOKEN_CACHE = Path.home() / ".openjarvis" / "spotify_token.json"


@dataclass
class SpotifyTrack:
    uri: str            # spotify:track:xxx
    name: str
    artists: List[str]
    album: str
    duration_ms: int


@dataclass
class SpotifyAlbum:
    uri: str            # spotify:album:xxx
    name: str
    artists: List[str]
    total_tracks: int


@dataclass
class SpotifyPlaylist:
    uri: str            # spotify:playlist:xxx
    name: str
    owner: str
    total_tracks: int


@dataclass
class SpotifyArtist:
    uri: str            # spotify:artist:xxx
    name: str
    popularity: int
    genres: List[str]


class SpotifyClient:
    """Minimal Spotify Web API client using Client Credentials auth."""

    BASE_URL = "https://api.spotify.com/v1"
    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret = (
            client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        )
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._load_cached_token()

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _load_cached_token(self) -> None:
        try:
            if _TOKEN_CACHE.exists():
                data = json.loads(_TOKEN_CACHE.read_text())
                self._token = data.get("token")
                self._token_expires_at = float(data.get("expires_at", 0))
        except Exception:
            pass

    def _save_cached_token(self) -> None:
        try:
            _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_CACHE.write_text(
                json.dumps(
                    {
                        "token": self._token,
                        "expires_at": self._token_expires_at,
                    },
                    indent=2,
                )
            )
        except Exception:
            logger.exception("Failed to cache Spotify token")

    def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if not self.is_configured:
            raise RuntimeError(
                "Spotify is not configured. Set SPOTIFY_CLIENT_ID and "
                "SPOTIFY_CLIENT_SECRET environment variables."
            )

        now = time.time()
        if self._token and now < self._token_expires_at - 30:
            return self._token

        # Fetch a new token (Client Credentials flow)
        creds = f"{self.client_id}:{self.client_secret}"
        b64 = base64.b64encode(creds.encode()).decode()

        resp = httpx.post(
            self.TOKEN_URL,
            headers={"Authorization": f"Basic {b64}"},
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        self._token_expires_at = now + int(data.get("expires_in", 3600))
        self._save_cached_token()
        return self._token

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        token = self._ensure_token()
        resp = httpx.get(
            f"{self.BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Search methods
    # ------------------------------------------------------------------

    def search_track(self, query: str, limit: int = 5) -> List[SpotifyTrack]:
        """Search for tracks matching the query string."""
        data = self._request(
            "/search",
            params={"q": query, "type": "track", "limit": limit},
        )
        items = data.get("tracks", {}).get("items", [])
        return [
            SpotifyTrack(
                uri=t["uri"],
                name=t["name"],
                artists=[a["name"] for a in t.get("artists", [])],
                album=t.get("album", {}).get("name", ""),
                duration_ms=t.get("duration_ms", 0),
            )
            for t in items
        ]

    def search_artist(self, query: str, limit: int = 3) -> List[SpotifyArtist]:
        """Search for artists."""
        data = self._request(
            "/search",
            params={"q": query, "type": "artist", "limit": limit},
        )
        items = data.get("artists", {}).get("items", [])
        return [
            SpotifyArtist(
                uri=a["uri"],
                name=a["name"],
                popularity=a.get("popularity", 0),
                genres=a.get("genres", []),
            )
            for a in items
        ]

    def search_album(self, query: str, limit: int = 3) -> List[SpotifyAlbum]:
        """Search for albums."""
        data = self._request(
            "/search",
            params={"q": query, "type": "album", "limit": limit},
        )
        items = data.get("albums", {}).get("items", [])
        return [
            SpotifyAlbum(
                uri=a["uri"],
                name=a["name"],
                artists=[ar["name"] for ar in a.get("artists", [])],
                total_tracks=a.get("total_tracks", 0),
            )
            for a in items
        ]

    def search_playlist(self, query: str, limit: int = 3) -> List[SpotifyPlaylist]:
        """Search for playlists."""
        data = self._request(
            "/search",
            params={"q": query, "type": "playlist", "limit": limit},
        )
        items = data.get("playlists", {}).get("items", [])
        return [
            SpotifyPlaylist(
                uri=p["uri"],
                name=p["name"],
                owner=p.get("owner", {}).get("display_name", ""),
                total_tracks=p.get("tracks", {}).get("total", 0),
            )
            for p in items or []
            if p
        ]

    def artist_top_tracks(
        self, artist_uri: str, country: str = "GB"
    ) -> List[SpotifyTrack]:
        """Get an artist's top tracks in a market."""
        artist_id = artist_uri.split(":")[-1]
        data = self._request(
            f"/artists/{artist_id}/top-tracks",
            params={"market": country},
        )
        items = data.get("tracks", [])
        return [
            SpotifyTrack(
                uri=t["uri"],
                name=t["name"],
                artists=[a["name"] for a in t.get("artists", [])],
                album=t.get("album", {}).get("name", ""),
                duration_ms=t.get("duration_ms", 0),
            )
            for t in items
        ]


# Module-level singleton (lazily configured)
_client: Optional[SpotifyClient] = None


def get_client() -> SpotifyClient:
    global _client
    if _client is None:
        _client = SpotifyClient()
    return _client


__all__ = [
    "SpotifyClient",
    "SpotifyTrack",
    "SpotifyAlbum",
    "SpotifyArtist",
    "SpotifyPlaylist",
    "get_client",
]
