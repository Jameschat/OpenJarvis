from openjarvis.desktop.backend import BackendSupervisor


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


def test_ensure_running_noop_when_already_healthy():
    spawns = []
    sup = BackendSupervisor(
        launcher="X:/jarvis.bat",
        health_check=lambda: {"ok": True},
        spawn=lambda cmd: spawns.append(cmd),
    )
    result = sup.ensure_running()
    assert result == {"ready": True, "started": False, "reason": "already running"}
    assert spawns == []
    assert sup.started_backend() is False


def test_ensure_running_starts_launcher_when_down_then_ready():
    health = {"n": 0}

    def check():
        health["n"] += 1
        return {"ok": health["n"] >= 2}  # down at first, ready after start

    spawns = []
    sup = BackendSupervisor(
        launcher="X:/jarvis.bat",
        health_check=check,
        spawn=lambda cmd: spawns.append(cmd) or FakeProc(),
        sleep=lambda s: None,
        clock=lambda: 0.0,
    )
    result = sup.ensure_running(wait_timeout_s=10)
    assert result["started"] is True
    assert result["ready"] is True
    assert result["launcher"] == "X:/jarvis.bat"
    assert spawns == ["X:/jarvis.bat"]
    assert sup.started_backend() is True


def test_ensure_running_no_launcher_configured(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_DESKTOP_LAUNCHER", raising=False)
    monkeypatch.setattr(
        "openjarvis.desktop.backend.DEFAULT_LAUNCHER",
        __import__("pathlib").Path("Z:/nonexistent/jarvis.bat"),
    )
    sup = BackendSupervisor(health_check=lambda: {"ok": False})
    result = sup.ensure_running()
    assert result == {"ready": False, "started": False, "reason": "no launcher configured"}


def test_resolved_launcher_prefers_env(monkeypatch):
    monkeypatch.setenv("OPENJARVIS_DESKTOP_LAUNCHER", "C:/custom/start.bat")
    sup = BackendSupervisor(health_check=lambda: {"ok": False})
    assert sup.resolved_launcher() == "C:/custom/start.bat"


def test_stop_only_terminates_what_we_started():
    sup = BackendSupervisor(health_check=lambda: {"ok": False})
    # nothing started yet
    assert sup.stop() is False

    proc = FakeProc()
    sup2 = BackendSupervisor(
        launcher="X:/jarvis.bat",
        health_check=lambda: {"ok": False},
        spawn=lambda cmd: proc,
        sleep=lambda s: None,
        clock=iter([0.0, 0.0, 100.0]).__next__,
    )
    sup2.ensure_running(wait_timeout_s=1)  # down, will start then time out
    assert sup2.started_backend() is True
    assert sup2.stop() is True
    assert proc.terminated is True
    assert sup2.started_backend() is False
