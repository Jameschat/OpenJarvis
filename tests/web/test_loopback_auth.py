from openjarvis.cli.brain_server import _loopback_auth_ok

PORT = 7710


def gh(headers):
    low = {k.lower(): v for k, v in headers.items()}
    return lambda name: low.get(name.lower())


def test_local_desktop_app_is_trusted(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_TRUST_LOOPBACK", raising=False)
    assert _loopback_auth_ok(gh({}), "127.0.0.1", PORT) is True
    assert _loopback_auth_ok(gh({}), "::1", PORT) is True


def test_local_with_local_origin_is_trusted(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_TRUST_LOOPBACK", raising=False)
    assert _loopback_auth_ok(gh({"Origin": "http://127.0.0.1:7710"}), "127.0.0.1", PORT) is True
    assert _loopback_auth_ok(gh({"Origin": "http://localhost:7710/"}), "127.0.0.1", PORT) is True


def test_tunnel_traffic_is_not_trusted(monkeypatch):
    # Cloudflare tunnel forwards from loopback but stamps these headers.
    monkeypatch.delenv("OPENJARVIS_TRUST_LOOPBACK", raising=False)
    assert _loopback_auth_ok(gh({"Cf-Connecting-Ip": "1.2.3.4"}), "127.0.0.1", PORT) is False
    assert _loopback_auth_ok(gh({"X-Forwarded-For": "1.2.3.4"}), "127.0.0.1", PORT) is False
    assert _loopback_auth_ok(gh({"X-Forwarded-Proto": "https"}), "127.0.0.1", PORT) is False
    assert _loopback_auth_ok(gh({"Cf-Visitor": '{"scheme":"https"}'}), "127.0.0.1", PORT) is False


def test_non_loopback_is_not_trusted(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_TRUST_LOOPBACK", raising=False)
    assert _loopback_auth_ok(gh({}), "192.168.1.50", PORT) is False


def test_cross_origin_is_not_trusted(monkeypatch):
    # Blocks DNS-rebinding / cross-site requests a malicious page makes at 127.0.0.1.
    monkeypatch.delenv("OPENJARVIS_TRUST_LOOPBACK", raising=False)
    assert _loopback_auth_ok(gh({"Origin": "http://evil.example"}), "127.0.0.1", PORT) is False


def test_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("OPENJARVIS_TRUST_LOOPBACK", "0")
    assert _loopback_auth_ok(gh({}), "127.0.0.1", PORT) is False
