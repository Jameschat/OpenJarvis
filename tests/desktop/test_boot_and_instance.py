from openjarvis.desktop import app
from openjarvis.desktop.boot import boot_html


def test_boot_html_embeds_studio_url_and_polls_api():
    out = boot_html("http://127.0.0.1:7710/studio", title="J.A.R.V.I.S.")
    assert "http://127.0.0.1:7710/studio" in out
    assert "J.A.R.V.I.S." in out
    assert "pywebview.api" in out
    assert "a.ready()" in out
    assert "window.location.replace(studioUrl)" in out
    assert "Start backend" in out


def test_boot_html_escapes_title():
    out = boot_html("http://x/studio", title="A<b>")
    assert "A<b>" not in out
    assert "A&lt;b&gt;" in out


def test_desktop_api_ready_delegates_to_health():
    api = app.DesktopApi("http://x/studio", health_check=lambda: {"ok": True})
    assert api.ready() is True
    assert api.studio_url() == "http://x/studio"

    api2 = app.DesktopApi("http://x/studio", health_check=lambda: {"ok": False})
    assert api2.ready() is False


def test_desktop_api_start_backend_without_supervisor():
    api = app.DesktopApi("http://x/studio", health_check=lambda: {"ok": False})
    result = api.start_backend()
    assert result["started"] is False


def test_desktop_api_start_backend_delegates_to_supervisor():
    class FakeSup:
        def ensure_running(self, *, wait_timeout_s):
            return {"ready": True, "started": True}

    api = app.DesktopApi("http://x/studio", supervisor=FakeSup())
    assert api.start_backend() == {"ready": True, "started": True}


def test_single_instance_guard_blocks_second_acquire():
    import socket

    # find a free port
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    first = app.acquire_single_instance(port)
    assert first is not None
    try:
        second = app.acquire_single_instance(port)
        assert second is None  # already held
    finally:
        first.close()

    # released -> can acquire again
    third = app.acquire_single_instance(port)
    assert third is not None
    third.close()
