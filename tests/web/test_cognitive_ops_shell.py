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
    assert 'id="cog-approval-status"' in html
    assert 'id="cog-approval-commands"' in html
    assert 'data-approval-command=' in html
    assert "function submitApprovalCommand(command)" in html
    assert "Run Backtest" in html
    assert "Create Paper Bot" in html
    assert "Start Mission" in html
    assert "height: calc(100dvh - 16px);" in html
    assert "grid-template-rows: 66px minmax(420px, 1fr) 210px 48px;" in html
    assert "function submitApprovalDecision(kind)" in html
    assert "no generic chat task sent" in html
    approval_fn = html[
        html.index("function submitApprovalDecision(kind)") : html.index(
            "const cogApprove"
        )
    ]
    command_fn = html[
        html.index("function submitApprovalCommand(command)") : html.index(
            "document.querySelectorAll('[data-approval-command]')"
        )
    ]
    assert "sendChat();" not in approval_fn
    assert "sendChat();" in command_fn
    assert "cog-side-rail" in html
    assert "cog-top" in html
    assert "cog-bottom-nav" in html
    assert ".rail-btn,\n.cog-mobile-tabs button,\n.cog-bottom-nav a,\n.cog-bottom-nav button" in html
    assert "pointer-events: auto;" in html
    assert "#agent-layer {\n    display: none !important;" in html
    assert "cog-mobile-tabs" in html
    assert "starfield" in html
    assert "Cognitive Coach" in html
    assert "Approvals" in html
    assert 'data-cog-page="agents"' in html
    assert 'data-cog-page="settings"' in html
    assert "Open Memory Vault" in html
    assert "memory-vault.html" in (ROOT / "src" / "openjarvis" / "cli" / "brain_server.py").read_text(encoding="utf-8")
    assert (ROOT / "jarvis_web" / "memory-vault.html").exists()
    assert "Open CodeGraph" in html
    assert "Open Markets" in html
    assert "Open Studio" in html
    assert 'href="/memory-vault"' in html
    assert 'href="/codegraph"' in html
    assert 'href="/studio"' in html


def test_cognitive_shell_uses_existing_live_status_endpoints():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert "refreshCognitiveOps" in html
    assert "cogFetchJson('/studio/state')" in html
    assert "cogFetchJson('/vault/summary')" in html
    assert "cogFetchJson('/codegraph/status')" in html
    assert "cogFetchJson('/graphify/status')" in html
    assert "setCogPage('directives')" in html
