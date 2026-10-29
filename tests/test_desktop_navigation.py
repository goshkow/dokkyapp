from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage

from koddi.desktop import web_host


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8877/ui/", "http://127.0.0.1:8878/ui/",
    "http://localhost:8877/ui/", "https://evil.example/", "about:blank",
])
def test_window_commands_and_frame_handshake_reject_foreign_origin(url):
    called = []
    host = SimpleNamespace(
        api_base="http://127.0.0.1:8877", view=SimpleNamespace(url=lambda: QUrl(url)),
        _caption_ready=False, window_action=called.append,
        _window_frame_state=lambda: called.append("state"),
    )
    bridge = SimpleNamespace(host=host, view=host.view, role="main", frameChanged=SimpleNamespace(emit=called.append))
    for action in ("minimize", "maximize", "close", "move", "unknown"):
        web_host.WindowBridge.command(bridge, action)
    web_host.WindowBridge.requestState(bridge)
    assert called == []
    assert not host._caption_ready


def test_window_commands_and_initial_maximized_state_from_trusted_origin(monkeypatch):
    called = []
    state = {"mode": "windows-caption-glyph", "maximized": True, "hover": ""}
    host = SimpleNamespace(
        api_base="http://127.0.0.1:8877",
        view=SimpleNamespace(url=lambda: QUrl("http://127.0.0.1:8877/ui/")),
        _caption_ready=False, window_action=called.append,
        _window_frame_state=lambda: state,
    )
    bridge = SimpleNamespace(host=host, view=host.view, role="main", frameChanged=SimpleNamespace(emit=called.append))
    monkeypatch.setattr(web_host.QApplication, "mouseButtons", lambda: web_host.Qt.MouseButton.NoButton)
    for action in ("minimize", "maximize", "close", "move", "unknown"):
        web_host.WindowBridge.command(bridge, action)
    web_host.WindowBridge.requestState(bridge)
    assert called == ["minimize", "maximize", "close", state]
    assert host._caption_ready


def test_native_bridge_only_accepts_local_page_and_http_links(monkeypatch):
    opened = []
    monkeypatch.setattr(
        web_host.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    host = SimpleNamespace(api_base="http://127.0.0.1:8877")
    host.handle_app_url = lambda url, current: web_host.WebHost.handle_app_url(host, url, current)
    host.navigation_allowed = lambda *args: web_host.WebHost.navigation_allowed(host, *args)
    page = SimpleNamespace(host=host, url=lambda: QUrl("http://127.0.0.1:8877/ui/"))
    kind = QWebEnginePage.NavigationType.NavigationTypeOther
    assert not web_host.KoddiPage.acceptNavigationRequest(
        page, QUrl("koddi://open-url?url=https%3A%2F%2Fexample.com%2Fdoc"), kind, True
    )
    assert opened == ["https://example.com/doc"]
    assert not web_host.KoddiPage.acceptNavigationRequest(
        page, QUrl("koddi://open-url?url=file%3A%2F%2F%2FC%3A%2Fsecret.txt"), kind, True
    )
    assert len(opened) == 1
    page.url = lambda: QUrl("https://example.com/")
    assert not web_host.KoddiPage.acceptNavigationRequest(
        page, QUrl("koddi://open-url?url=https%3A%2F%2Fexample.org"), kind, True
    )
    assert len(opened) == 1


def test_download_accepts_local_plan_blob_but_not_foreign_origin(monkeypatch):
    class Download:
        accepted = False
        cancelled = False

        def __init__(self, url):
            self.source = url

        def url(self):
            return QUrl(self.source)

        def downloadFileName(self):
            return "plan.md"

        def cancel(self):
            self.cancelled = True

        def accept(self):
            self.accepted = True

        def setDownloadDirectory(self, value):
            self.directory = value

        def setDownloadFileName(self, value):
            self.filename = value

    monkeypatch.setattr(
        web_host.QFileDialog, "getSaveFileName", lambda *args: ("C:/test/plan.md", "")
    )
    host = SimpleNamespace(api_base="http://127.0.0.1:8877")
    for url, allowed in [
        ("blob:http://127.0.0.1:8877/id", True),
        ("http://127.0.0.1:8877/api/file", True),
        ("blob:https://evil.example/id", False),
        ("file:///C:/secret", False),
    ]:
        download = Download(url)
        web_host.WebHost._save_download(host, download)
        assert download.accepted is allowed
        assert download.cancelled is not allowed


def test_native_view_navigation_policy(monkeypatch):
    opened = []
    monkeypatch.setattr(web_host.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    host = SimpleNamespace(api_base="http://127.0.0.1:8877")
    host.handle_app_url = lambda url, current: web_host.WebHost.handle_app_url(host, url, current)
    host.navigation_allowed = lambda *args: web_host.WebHost.navigation_allowed(host, *args)
    view = SimpleNamespace(url=lambda: QUrl("http://127.0.0.1:8877/ui/"))

    def decide(url, main=True, clicked=False):
        return web_host.WebHost.decide_navigation(host, view, QUrl(url), main, clicked)

    assert decide("http://127.0.0.1:8877/ui/?role=sidebar")
    assert not decide("https://example.com/", clicked=True) and opened == ["https://example.com/"]
    assert not decide("https://example.com/", clicked=False) and len(opened) == 1  # скрипт сам уйти со страницы не может
    assert decide("https://example.com/", main=False)  # встроенный кадр
    assert not decide("koddi://open-url?url=https%3A%2F%2Fexample.org") and len(opened) == 2
    view.url = lambda: QUrl("https://evil.example/")
    assert not decide("koddi://open-url?url=https%3A%2F%2Fexample.net") and len(opened) == 2


def test_site_frame_of_chat_mode_stays_on_its_page(monkeypatch):
    opened = []
    monkeypatch.setattr(web_host.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    host = SimpleNamespace(api_base="http://127.0.0.1:8877")
    host.handle_app_url = lambda url, current: web_host.WebHost.handle_app_url(host, url, current)
    host.navigation_allowed = lambda *args: web_host.WebHost.navigation_allowed(host, *args)
    view = SimpleNamespace(url=lambda: QUrl("http://127.0.0.1:8877/ui/"))

    def decide(url, main=False, clicked=False):
        return web_host.WebHost.decide_navigation(host, view, QUrl(url), main, clicked)

    assert decide("https://dokky.goshkow.com/app-surface?theme=dark")
    assert decide("https://dokky.goshkow.com/app-surface#x", clicked=True)
    # ссылка внутри фрейма уводит не фрейм, а в браузер системы
    assert not decide("https://dokky.goshkow.com/pay/abc", clicked=True)
    assert not decide("https://example.com/", clicked=True)
    assert opened == ["https://dokky.goshkow.com/pay/abc", "https://example.com/"]
    assert not decide("file:///etc/passwd", clicked=True) and len(opened) == 2
    # команды окна из фрейма не принимаются, даже когда главная страница своя
    assert not decide("koddi://open-url?url=https%3A%2F%2Fexample.org", clicked=True) and len(opened) == 2
    # сайт не может стать главной страницей окна
    assert not decide("https://dokky.goshkow.com/app-surface", main=True)


def test_site_frame_may_use_microphone_and_downloads_but_nothing_else():
    assert web_host.trusted_origin(QUrl("https://dokky.goshkow.com"), "http://127.0.0.1:8877")
    assert web_host.trusted_origin(QUrl("blob:https://dokky.goshkow.com/id"), "http://127.0.0.1:8877")
    assert web_host.trusted_origin(QUrl("http://127.0.0.1:8877/ui/"), "http://127.0.0.1:8877")
    for url in ("http://dokky.goshkow.com", "https://dokky.goshkow.com.evil.example", "https://evil.example",
                "http://127.0.0.1:8878", "blob:https://evil.example/id"):
        assert not web_host.trusted_origin(QUrl(url), "http://127.0.0.1:8877")


def test_new_windows_from_page_open_in_system_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(web_host.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    page = SimpleNamespace(deleteLater=lambda: opened.append("closed"))
    kind = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert not web_host.ExternalPage.acceptNavigationRequest(page, QUrl("https://example.com/a"), kind, True)
    assert not web_host.ExternalPage.acceptNavigationRequest(page, QUrl("javascript:alert(1)"), kind, True)
    assert not web_host.ExternalPage.acceptNavigationRequest(page, QUrl("https://u:p@example.com/"), kind, True)
    assert opened == ["https://example.com/a", "closed", "closed", "closed"]
