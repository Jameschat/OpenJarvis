from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_briefing_and_digest_fetch_prefer_same_origin_before_host_param():
    html = (ROOT / "jarvis_web" / "brain.html").read_text(encoding="utf-8")

    assert "async function fetchHudJson(path, date)" in html
    assert "new URL(path, window.location.origin)" in html
    assert "params.get('host')" in html
    assert "await fetch(primary.toString(), { credentials: 'include' })" in html

    briefing_start = html.index("async function loadBriefing")
    briefing_block = html[briefing_start : briefing_start + 500]
    assert "fetchHudJson('/briefing', date)" in briefing_block
    assert "`${proto}://${host}/briefing`" not in briefing_block

    digest_start = html.index("async function loadDigest")
    digest_block = html[digest_start : digest_start + 500]
    assert "fetchHudJson('/digest', date)" in digest_block
    assert "`${proto}://${host}/digest`" not in digest_block
