from openjarvis.desktop import app


def test_resolve_studio_url_defaults():
    assert app.resolve_studio_url() == "http://127.0.0.1:7710/studio"


def test_resolve_studio_url_custom_host_port_path():
    assert (
        app.resolve_studio_url("192.168.1.5", 7711, "studio")
        == "http://192.168.1.5:7711/studio"
    )
    assert app.resolve_studio_url(port=8000, path="/x") == "http://127.0.0.1:8000/x"


def test_backend_ready_true_when_health_ok():
    assert app.backend_ready(health_check=lambda: {"ok": True}) is True


def test_backend_ready_false_when_health_not_ok():
    assert app.backend_ready(health_check=lambda: {"ok": False}) is False


def test_backend_ready_false_when_health_raises():
    def boom():
        raise RuntimeError("backend down")

    assert app.backend_ready(health_check=boom) is False


def test_wait_for_backend_returns_true_once_ready():
    calls = {"n": 0}

    def health():
        calls["n"] += 1
        return {"ok": calls["n"] >= 3}  # ready on the 3rd poll

    ticks = {"t": 0.0}

    def clock():
        return ticks["t"]

    def sleep(seconds):
        ticks["t"] += seconds

    assert app.wait_for_backend(
        timeout_s=30, interval_s=1, health_check=health, sleep=sleep, clock=clock
    ) is True
    assert calls["n"] == 3


def test_wait_for_backend_times_out():
    def clock_seq():
        # 0.0 (start/deadline calc), then advancing past the deadline
        yield 0.0
        yield 0.0
        yield 100.0

    gen = clock_seq()

    assert app.wait_for_backend(
        timeout_s=5,
        interval_s=1,
        health_check=lambda: {"ok": False},
        sleep=lambda s: None,
        clock=lambda: next(gen),
    ) is False


def test_launch_returns_2_without_pywebview(monkeypatch, capsys):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            raise ImportError("no pywebview")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # single_instance=False isolates this from any real running app instance.
    code = app.launch(health_check=lambda: {"ok": True}, wait_timeout_s=0, single_instance=False)
    assert code == 2
    out = capsys.readouterr().out
    assert "pywebview is not installed" in out
