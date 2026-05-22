from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_brain_html_contains_cognitive_operations_shell():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert 'id="cognitive-shell"' in html
    assert "Jarvis Cognitive Operations Center" in html
    assert "Active Directives" in html
    assert "Cognitive Graph Live" in html
    assert "Decision Console" in html
    assert 'id="cog-approve"' in html
    assert 'id="cog-disprove"' in html
    assert "cog-side-rail" in html
    assert "cog-top" in html
    assert "cog-bottom-nav" in html
    assert "cog-mobile-tabs" in html
    assert "starfield" in html
    assert "Cognitive Coach" in html
    assert "Approvals" in html
    assert 'data-cog-page="agents"' in html
    assert 'data-cog-page="settings"' in html
    assert "Open Memory Vault" in html
    assert "Open CodeGraph" in html
    assert "Open Markets" in html
    assert "Open System Monitor" in html
    assert 'href="/vault/summary"' in html
    assert 'href="/codegraph/status"' in html
    assert 'href="/jarvis-os/state"' in html


def test_cognitive_shell_uses_existing_live_status_endpoints():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert "refreshCognitiveOps" in html
    assert "cogFetchJson('/jarvis-os/state')" in html
    assert "cogFetchJson('/vault/summary')" in html
    assert "cogFetchJson('/codegraph/status')" in html
    assert "cogFetchJson('/graphify/status')" in html
    assert "setCogPage('directives')" in html
