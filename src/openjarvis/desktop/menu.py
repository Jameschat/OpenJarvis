"""Native application menu for the Jarvis desktop app.

The menu *actions* live on a ``MenuController`` whose dependencies (window,
desktop API, browser-open) are injected, so they're unit-testable without
pywebview. ``build_pywebview_menu`` lazily turns the controller into pywebview
``Menu`` objects, and ``attach_menu`` wires it into ``webview.start`` with a
graceful fallback for pywebview builds that predate the menu API.
"""

from __future__ import annotations

import webbrowser
from typing import Any, Callable

REPO_URL = "https://github.com/Jameschat/OpenJarvis"


class MenuController:
    def __init__(
        self,
        *,
        studio_url: str,
        api: Any,
        get_window: Callable[[], Any] | None = None,
        browser_open: Callable[[str], Any] = webbrowser.open,
    ) -> None:
        self._studio_url = studio_url
        self._api = api
        self._get_window = get_window
        self._browser_open = browser_open

    def _window(self):
        return self._get_window() if self._get_window else None

    def open_in_browser(self) -> None:
        self._browser_open(self._studio_url)

    def restart_backend(self) -> Any:
        if self._api is None:
            return None
        return self._api.start_backend()

    def reload_studio(self) -> None:
        win = self._window()
        if win is not None:
            win.load_url(self._studio_url)

    def about(self) -> None:
        self._browser_open(REPO_URL)

    def quit(self) -> None:
        win = self._window()
        if win is not None:
            win.destroy()


def build_pywebview_menu(controller: MenuController) -> list[Any]:
    """Lazily build the pywebview Menu tree. Raises ImportError on pywebview
    builds without the menu API — callers should handle that gracefully."""
    from webview.menu import Menu, MenuAction, MenuSeparator

    return [
        Menu(
            "Jarvis",
            [
                MenuAction("Open Studio in Browser", controller.open_in_browser),
                MenuAction("Restart Backend", controller.restart_backend),
                MenuSeparator(),
                MenuAction("Quit", controller.quit),
            ],
        ),
        Menu("View", [MenuAction("Reload Studio", controller.reload_studio)]),
        Menu("Help", [MenuAction("About Jarvis", controller.about)]),
    ]


def start_with_menu(webview: Any, controller: MenuController) -> None:
    """Call webview.start with a native menu, falling back to a plain start if
    this pywebview build doesn't support the menu kwarg/API."""
    try:
        menu = build_pywebview_menu(controller)
        webview.start(menu=menu)
    except (ImportError, TypeError):
        webview.start()
