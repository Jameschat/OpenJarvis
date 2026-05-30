from openjarvis.desktop import menu


class FakeWindow:
    def __init__(self):
        self.loaded = None
        self.destroyed = False

    def load_url(self, url):
        self.loaded = url

    def destroy(self):
        self.destroyed = True


class FakeApi:
    def __init__(self):
        self.started = 0

    def start_backend(self):
        self.started += 1
        return {"started": True}


def _controller(window=None, api=None, opened=None):
    return menu.MenuController(
        studio_url="http://127.0.0.1:7710/studio",
        api=api,
        get_window=(lambda: window) if window is not None else None,
        browser_open=lambda u: opened.append(u) if opened is not None else None,
    )


def test_open_in_browser_uses_studio_url():
    opened = []
    _controller(opened=opened).open_in_browser()
    assert opened == ["http://127.0.0.1:7710/studio"]


def test_restart_backend_delegates_to_api():
    api = FakeApi()
    result = _controller(api=api).restart_backend()
    assert api.started == 1
    assert result == {"started": True}


def test_reload_studio_loads_url_on_window():
    win = FakeWindow()
    _controller(window=win).reload_studio()
    assert win.loaded == "http://127.0.0.1:7710/studio"


def test_quit_destroys_window():
    win = FakeWindow()
    _controller(window=win).quit()
    assert win.destroyed is True


def test_about_opens_repo():
    opened = []
    _controller(opened=opened).about()
    assert opened == [menu.REPO_URL]


def test_actions_safe_without_window():
    # No window wired -> reload/quit must not raise.
    c = _controller(window=None)
    c.reload_studio()
    c.quit()


class _RecordingWebview:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs):
        self.calls.append(kwargs)


def test_start_with_menu_passes_menu_when_builder_succeeds(monkeypatch):
    monkeypatch.setattr(menu, "build_pywebview_menu", lambda c: ["MENU"])
    wv = _RecordingWebview()
    menu.start_with_menu(wv, _controller())
    assert wv.calls == [{"menu": ["MENU"]}]


def test_start_with_menu_falls_back_when_menu_api_missing(monkeypatch):
    def boom(_controller):
        raise ImportError("no menu api on this pywebview build")

    monkeypatch.setattr(menu, "build_pywebview_menu", boom)
    wv = _RecordingWebview()
    menu.start_with_menu(wv, _controller())
    assert wv.calls == [{}]  # plain start, no menu kwarg
