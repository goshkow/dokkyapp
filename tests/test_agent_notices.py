import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from koddi.desktop import notices, web_host


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_notice_is_parsed_strictly():
    notice = notices.parse(json.dumps({"chat_id": 7, "kind": "question", "title": "Починить\nвход\x07 "}))
    assert notice == notices.Notice(7, "question", "Починить вход")
    assert notice.body == "Докки задал вопрос"
    assert notices.parse(json.dumps({"chat_id": 7, "kind": "done"})).title == "Докки"
    long = notices.parse(json.dumps({"chat_id": 1, "kind": "done", "title": "я" * 500}))
    assert len(long.title) == notices.TITLE_LIMIT and long.title.endswith("…")
    for bad in ("{", "[]", json.dumps({"chat_id": True, "kind": "done"}), json.dumps({"chat_id": 0, "kind": "done"}),
                json.dumps({"chat_id": "7", "kind": "done"}), json.dumps({"chat_id": 7, "kind": "run"}),
                json.dumps({"chat_id": 7, "kind": "done", "title": 5}), "x" * 5000,
                # служебный вид только от хоста, страница его прислать не может
                json.dumps({"chat_id": 7, "kind": "tray"})):
        assert notices.parse(bad) is None


def test_new_kinds_have_their_bodies():
    bodies = {
        "approval": "Нужно подтвердить действие",
        "stalled": "Докки временно не отвечает - работа на паузе",
        "limit": "Лимит восстановился - можно продолжить работу",
    }
    for kind, body in bodies.items():
        notice = notices.parse(json.dumps({"chat_id": 2, "kind": kind, "title": "Чат"}))
        assert notice is not None and notice.kind == kind and notice.body == body


def test_close_action_hides_only_user_close_with_tray_and_setting():
    from koddi.desktop.closing import HIDE, QUIT, close_action

    def act(**kw):
        return close_action(**{"user_close": True, "quitting": False, "close_to_tray": True, "has_tray": True, **kw})

    assert act() == HIDE
    assert act(user_close=False) == QUIT  # выход из системы, обновление, Cmd+Q из меню Dock
    assert act(quitting=True) == QUIT  # quit() шлёт closeEvent раньше выхода
    assert act(close_to_tray=False) == QUIT
    assert act(has_tray=False) == QUIT


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
        self._notice_times = {}
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
    assert host.tray.shown == [("Чат", "Агент закончил работу"), ("Другой", "Докки задал вопрос")]
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


class Closing(QMainWindow):
    """Закрытие окна без настоящей страницы: те же методы, что у WebHost."""

    closeEvent = web_host.WebHost.closeEvent
    close_window = web_host.WebHost.close_window
    quit_app = web_host.WebHost.quit_app
    hide_to_tray = web_host.WebHost.hide_to_tray
    _tray_hint_once = web_host.WebHost._tray_hint_once
    _save_geometry = lambda self: None  # noqa: E731

    def __init__(self, setting=True):
        super().__init__()
        self.tray = Tray()
        self.notices = None
        self._user_close = False
        self._quitting = False
        self._stop_confirmed = True
        self.setting = setting
        self.fronted = 0

    def _close_to_tray(self):
        return self.setting

    def _stop_agreed(self):
        return True

    def bring_to_front(self):
        self.fronted += 1


def test_close_window_hides_to_tray_and_hints_once(app, monkeypatch):
    from koddi.storage import db

    monkeypatch.setattr(web_host, "IS_MAC", False)
    store = {}
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(db, "set_setting", lambda key, value: store.__setitem__(key, value))
    host = Closing()
    host.show()
    host.close_window()
    assert not host.isVisible()
    assert [body for _title, body in host.tray.shown] == ["Докки работает в трее - агент продолжит задачу. Открыть или выйти можно из значка"]
    host.tray.click()
    assert host.fronted == 1
    host.show()
    host.close_window()
    assert not host.isVisible() and len(host.tray.shown) == 1


def test_quit_and_disabled_setting_close_the_window(app, monkeypatch):
    monkeypatch.setattr(web_host, "IS_MAC", False)
    monkeypatch.setattr(web_host.QApplication, "quit", lambda: None)
    host = Closing()
    host.show()
    host.quit_app()
    host.close()  # closeEvent от quit(): окно не прячется
    assert not host.isVisible() and host.tray.shown == []
    off = Closing(setting=False)
    off.show()
    off.close_window()
    assert not off.isVisible() and off.tray.shown == []


def test_quit_event_makes_close_real_not_hide():
    from PySide6.QtCore import QEvent, QObject

    from koddi.desktop.closing import QUIT, close_action

    class Host(QObject):
        _quitting = False

    host = Host()
    watch = web_host.QuitWatch(host)
    assert watch.eventFilter(host, QEvent(QEvent.Type.MouseMove)) is False
    assert host._quitting is False
    # Cmd+Q, Dock - «Выйти» и выход из системы приходят событием Quit: событие дальше идёт как обычно
    assert watch.eventFilter(host, QEvent(QEvent.Type.Quit)) is False
    assert host._quitting is True
    # даже «крестик» во время выхода закрывает окно, а не прячет в трей
    assert close_action(user_close=True, quitting=host._quitting, close_to_tray=True, has_tray=True) == QUIT


def test_same_notice_is_shown_once_in_five_seconds(app, monkeypatch):
    monkeypatch.setattr(web_host, "IS_MAC", False)
    host = Host(front=False)
    clock = [100.0]
    monkeypatch.setattr(web_host.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        host.agent_notice(notices.Notice(3, "approval", "Чат"))
    host.agent_notice(notices.Notice(3, "question", "Чат"))
    assert len(host.tray.shown) == 2
    clock[0] += 6
    host.agent_notice(notices.Notice(3, "approval", "Чат"))
    assert len(host.tray.shown) == 3
