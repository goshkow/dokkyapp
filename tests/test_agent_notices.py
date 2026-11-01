import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from koddi.desktop import notices, web_host


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_notice_is_parsed_strictly():
    notice = notices.parse(json.dumps({"chat_id": 7, "kind": "question", "title": "Починить\nвход\x07 "}))
    assert notice == notices.Notice(7, "question", "Починить вход")
    assert notice.body == "Агент ждёт вашего ответа"
    assert notices.parse(json.dumps({"chat_id": 7, "kind": "done"})).title == "Докки"
    long = notices.parse(json.dumps({"chat_id": 1, "kind": "done", "title": "я" * 500}))
    assert len(long.title) == notices.TITLE_LIMIT and long.title.endswith("…")
    for bad in ("{", "[]", json.dumps({"chat_id": True, "kind": "done"}), json.dumps({"chat_id": 0, "kind": "done"}),
                json.dumps({"chat_id": "7", "kind": "done"}), json.dumps({"chat_id": 7, "kind": "run"}),
                json.dumps({"chat_id": 7, "kind": "done", "title": 5}), "x" * 5000):
        assert notices.parse(bad) is None


class Tray:
    def __init__(self):
        self.shown = []

    def notify(self, title, body, on_click):
        self.shown.append((title, body))
        self.click = on_click


class Host(QWidget):
    """Логика уведомлений окна без настоящей страницы: те же методы, что у WebHost."""

    agent_notice = web_host.WebHost.agent_notice
    open_notice_chat = web_host.WebHost.open_notice_chat
    _notices_seen = web_host.WebHost._notices_seen

    def __init__(self, front: bool):
        super().__init__()
        self.front = front
        self.tray = Tray()
        self.notices = None
        self._unseen_notices = 0
        self._notice_chat = 0
        self.calls = []

    def _in_front(self):
        return self.front

    def bring_to_front(self):
        self.calls.append("front")

    def menu_action(self, action):
        self.calls.append(action)


def test_window_in_front_gets_no_notice(app, monkeypatch):
    monkeypatch.setattr(web_host, "IS_MAC", False)
    host = Host(front=True)
    host.agent_notice(notices.Notice(3, "done", "Чат"))
    assert host.tray.shown == [] and host._unseen_notices == 0


def test_background_window_notifies_and_click_opens_that_chat(app, monkeypatch):
    monkeypatch.setattr(web_host, "IS_MAC", False)
    host = Host(front=False)
    host.agent_notice(notices.Notice(3, "done", "Чат"))
    host.agent_notice(notices.Notice(4, "question", "Другой"))
    assert host.tray.shown == [("Чат", "Агент закончил работу"), ("Другой", "Агент ждёт вашего ответа")]
    assert host._unseen_notices == 2
    host.tray.click()
    assert host.calls == ["front", "open-chat:4"]
    host._notices_seen()
    assert host._unseen_notices == 0


def test_dock_bounces_only_for_questions_on_mac(app, monkeypatch):
    from koddi.desktop import macos_notices

    alerts = []
    monkeypatch.setattr(web_host, "IS_MAC", True)
    monkeypatch.setattr(macos_notices, "set_badge", lambda count: None)
    monkeypatch.setattr(web_host.QApplication, "alert", lambda widget, msec=0: alerts.append(msec))
    host = Host(front=False)
    host.agent_notice(notices.Notice(3, "done", "Чат"))
    assert alerts == [] and host._unseen_notices == 1
    host.agent_notice(notices.Notice(3, "question", "Чат"))
    assert alerts == [0]


class Permission:
    def __init__(self, origin: str, kind):
        from PySide6.QtCore import QUrl

        self._origin, self._kind, self.answer = QUrl(origin), kind, None

    def origin(self):
        return self._origin

    def permissionType(self):  # noqa: N802
        return self._kind

    def grant(self):
        self.answer = "grant"

    def deny(self):
        self.answer = "deny"


def test_camera_and_microphone_only_for_own_page_and_dokky_site(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineCore import QWebEnginePermission

    kinds = QWebEnginePermission.PermissionType
    monkeypatch.setattr(web_host, "site_origin", lambda: QUrl("https://dokky.goshkow.com"))
    page = SimpleNamespace(host=SimpleNamespace(api_base="http://127.0.0.1:8765"))

    def answer(origin, kind):
        permission = Permission(origin, kind)
        web_host.KoddiPage._permission(page, permission)
        return permission.answer

    for kind in (kinds.MediaAudioCapture, kinds.MediaVideoCapture, kinds.MediaAudioVideoCapture):
        assert answer("https://dokky.goshkow.com", kind) == "grant"
        assert answer("http://127.0.0.1:8765", kind) == "grant"
        assert answer("https://evil.example", kind) == "deny"
    assert answer("https://dokky.goshkow.com", kinds.Geolocation) == "deny"
    assert answer("https://dokky.goshkow.com", kinds.DesktopVideoCapture) == "deny"
