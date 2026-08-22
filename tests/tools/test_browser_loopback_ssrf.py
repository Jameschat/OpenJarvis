"""Security tests for the browser tool's loopback carve-out.

SSRF protection blocks every private IP, which meant the agent could build and
serve a web app but never open it. `tools.browser.allow_loopback` re-enables
loopback for the BROWSER TOOL ONLY. Because that is a deliberate relaxation of a
security control, these tests pin exactly how far it goes:

  * default OFF  -> nothing is exempt;
  * ON           -> loopback literals and the exact host "localhost" only;
  * ON           -> LAN ranges, link-local/cloud-metadata and ordinary hostnames
                    are STILL refused, including a hostname that merely resolves
                    to loopback (DNS-rebinding).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.tools.browser import _loopback_navigation_allowed


class _Cfg:
    """Minimal stand-in for the loaded config."""

    def __init__(self, allow: bool):
        browser = type("B", (), {"allow_loopback": allow})()
        tools = type("T", (), {"browser": browser})()
        self.tools = tools


def _with_flag(allow: bool):
    return patch("openjarvis.core.config.load_config", return_value=_Cfg(allow))


LOOPBACK = [
    "http://127.0.0.1:8477/",
    "http://127.0.0.1/",
    "http://127.5.5.5/",  # all of 127/8 is loopback
    "http://localhost:3000/",
    "http://[::1]:8080/",
    "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6 must not slip past
]

MUST_STAY_BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://metadata.google.internal/",
    "http://10.0.0.5/",  # RFC1918
    "http://172.16.0.9/",
    "http://192.168.1.191:8080/",  # the worker box on this LAN
    "http://[fe80::1]/",  # IPv6 link-local
    "https://example.com/",
    "http://evil.example.com/",
]


class TestBrowserLoopbackCarveOut:
    @pytest.mark.parametrize("url", LOOPBACK + MUST_STAY_BLOCKED)
    def test_disabled_by_default_exempts_nothing(self, url):
        with _with_flag(False):
            assert _loopback_navigation_allowed(url) is False

    @pytest.mark.parametrize("url", LOOPBACK)
    def test_enabled_allows_loopback(self, url):
        with _with_flag(True):
            assert _loopback_navigation_allowed(url) is True

    @pytest.mark.parametrize("url", MUST_STAY_BLOCKED)
    def test_enabled_still_blocks_everything_else(self, url):
        with _with_flag(True):
            assert _loopback_navigation_allowed(url) is False

    def test_hostname_resolving_to_loopback_is_refused(self):
        """DNS rebinding: only IP literals and the exact host 'localhost' pass.

        A hostname an attacker controls could resolve to 127.0.0.1 and reach the
        Jarvis backend / LiteLLM / model lane, so resolution is never consulted.
        """
        with _with_flag(True):
            assert _loopback_navigation_allowed("http://rebind.attacker.test/") is False

    def test_fails_closed_when_config_unavailable(self):
        with patch(
            "openjarvis.core.config.load_config", side_effect=RuntimeError("boom")
        ):
            assert _loopback_navigation_allowed("http://127.0.0.1/") is False

    def test_malformed_url_refused(self):
        with _with_flag(True):
            assert _loopback_navigation_allowed("not-a-url") is False
            assert _loopback_navigation_allowed("") is False
