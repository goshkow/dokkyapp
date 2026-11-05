import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, QUrl
from PySide6.QtWidgets import QApplication

from koddi.desktop import tray, tray_panel, web_host

NOW = 1_800_000_000.0


def payload(**patch) -> str:
    data = {
        "plan": "Plus",
        "window": {"remaining": 80, "hours": 5, "resets_at": NOW + 2 * 3600},
        "total": {"remaining": 72, "resets_at": NOW + 5 * 86400},
    }
    data.update(patch)
    return json.dumps(data)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def actions(calls: list) -> tray.Actions:
    return tray.Actions(open=lambda: calls.append("open"), usage=lambda: calls.append("usage"), quit=lambda: calls.append("quit"))


def test_rows_follow_page_data():
    state = tray.parse(payload())
    assert state.plan == "Plus"
    assert tray.section_title(state) == "Лимиты использования плана · Plus"
    assert tray.rows(state, NOW) == [
        tray.Row("Лимит на 5 часов", 80, "осталось 80 % · сброс через 2 ч"),
        tray.Row("Подписка · все модели", 72, "осталось 72 % · ещё 5 д."),
    ]


def test_unknown_values_are_hidden():
    state = tray.parse(payload(window=None, total={"remaining": 30, "resets_at": None}))
    assert tray.rows(state, NOW) == [tray.Row("Подписка · все модели", 30, "осталось 30 %")]
    empty = tray.parse(json.dumps({"plan": None, "window": None, "total": None}))
    assert tray.rows(empty, NOW) == []
    assert tray.section_title(empty) == "Лимиты использования плана"
    # прошедший сброс не показывается
    past = tray.parse(payload(window={"remaining": 10, "hours": 5, "resets_at": NOW - 60}))
    assert tray.rows(past, NOW)[0].detail == "осталось 10 %"


@pytest.mark.parametrize("raw", [
    "", "{oops", "[]", "null", "x" * (tray.MAX_PAYLOAD + 1), 42,
])
def test_broken_payload_keeps_previous_menu(raw):
    assert tray.parse(raw) is None


def test_payload_values_are_normalized():
    state = tray.parse(json.dumps({
        "plan": "Plus\u0000\n" + "x" * 100,
        "window": {"remaining": 140.6, "hours": 1000, "resets_at": "завтра"},
        "total": {"remaining": True, "resets_at": NOW},
    }))
    assert state.plan == "Plus" + "x" * 36
    assert state.window == tray.Limit(100, None)
    assert state.window_hours == 5
    assert state.total is None
    assert tray.parse(payload(window={"remaining": -5, "hours": 3})).window == tray.Limit(0, None)
    assert tray.parse(json.dumps({"window": {"remaining": float("nan")}}).replace("NaN", "1e999")).window is None


@pytest.mark.parametrize(("seconds", "text"), [
    (None, ""), (0, ""), (20, "1 мин"), (59 * 60 + 1, "60 мин"), (3600, "1 ч"), (5.4 * 3600, "5 ч"),
    (23.6 * 3600, "24 ч"), (86400, "1 д."), (4.6 * 86400, "5 д."),
])
def test_until(seconds, text):
    assert tray.until(None if seconds is None else NOW + seconds, NOW) == text


@pytest.mark.parametrize(("count", "text"), [(1, "1 час"), (3, "3 часа"), (5, "5 часов"), (11, "11 часов"), (21, "21 час"), (24, "24 часа")])
def test_hours_text(count, text):
    assert tray.hours_text(count) == text


def panel_state(**patch):
    data = json.loads(payload())
    data.update({"theme": "dark", "chats": [
        {"id": 3, "title": "Фризы", "mark": "working"},
        {"id": 5, "title": "Настройки", "mark": "question"},
        {"id": 8, "title": "Карта", "mark": "done"},
        {"id": 9, "title": "Лишний", "mark": "done"},
    ]})
    data.update(patch)
    return tray.parse(json.dumps(data))


def test_chats_are_validated_sorted_and_capped():
    state = panel_state()
    assert [(chat.id, chat.mark) for chat in state.chats] == [(5, "question"), (3, "working"), (8, "done"), (9, "done")]
    many = panel_state(chats=[{"id": n, "title": str(n), "mark": "done"} for n in range(1, 30)])
    assert len(many.chats) == tray.MAX_CHATS
    bad = panel_state(chats=[{"id": True, "mark": "done"}, {"id": -1, "mark": "done"}, {"id": "7", "mark": "done"},
                             {"id": 4, "mark": "evil"}, {"id": 6, "title": "Ок\u0000" + "x" * 200, "mark": "done"}, "x"])
    assert [(chat.id, len(chat.title)) for chat in bad.chats] == [(6, 80)]
    assert panel_state(theme="light").theme == "light"
    assert panel_state(theme="<script>").theme == "dark"
    assert tray.parse(payload()).chats == ()


def test_windows_panel_opens_on_right_click_and_runs_commands(app, monkeypatch):
    monkeypatch.setattr(tray.time, "time", lambda: NOW)
    calls = []
    acts = tray.Actions(open=lambda: calls.append("open"), usage=lambda: calls.append("usage"),
                        quit=lambda: calls.append("quit"), open_chat=lambda chat: calls.append(("chat", chat)))
    icon = tray.QtTray(acts)
    try:
        icon.update(panel_state())
        icon._activated(tray.QSystemTrayIcon.ActivationReason.Context)
        assert icon.panel.isVisible()
        pixmap = tray_panel.grab(icon.panel)
        assert not pixmap.isNull() and pixmap.width() == tray_panel.WIDTH + 2 * tray_panel.SHADOW
        # окно без рамки и кнопок окна: всплывающее, закрывается кликом мимо
        flags = icon.panel.windowFlags()
        assert flags & tray.Qt.WindowType.Popup and flags & tray.Qt.WindowType.FramelessWindowHint
        # четыре чата помещаются целиком, без строки «Ещё»
        assert {"close", "open", "usage", "quit", "chat:5", "chat:3", "chat:8", "chat:9"} <= set(icon.panel.targets)
        assert "more" not in icon.panel.targets
        for target in ("chat:5", "usage", "quit", "open"):
            icon.panel.activate(target)
        assert calls == [("chat", 5), "usage", "quit", "open"]
        assert not icon.panel.isVisible()
        # левый щелчок по значку открывает окно
        icon._activated(tray.QSystemTrayIcon.ActivationReason.Trigger)
        assert calls[-1] == "open"
    finally:
        icon.remove()


def test_panel_closes_when_cursor_goes_away(app, monkeypatch):
    calls = []
    icon = tray.QtTray(actions(calls))
    try:
        icon.update(panel_state())
        icon.panel.popup_at(QRect(500, 500, 20, 20))
        far = icon.panel.frameGeometry().topLeft() - QPoint(400, 400)
        monkeypatch.setattr(tray_panel.QCursor, "pos", staticmethod(lambda: far))
        for _ in range(4):
            icon.panel._watch_cursor()
        assert not icon.panel.isVisible()
        # рядом с меню - остаётся открытым
        icon.panel.popup_at(QRect(500, 500, 20, 20))
        near = icon.panel.frameGeometry().center()
        monkeypatch.setattr(tray_panel.QCursor, "pos", staticmethod(lambda: near))
        for _ in range(10):
            icon.panel._watch_cursor()
        assert icon.panel.isVisible()
    finally:
        icon.remove()


def test_title_is_centered_by_cap_height(app):
    font = tray_panel.make_font(15, tray_panel.QFont.Weight.Bold)
    metrics = tray_panel.QFontMetricsF(font)
    base = tray_panel.baseline_for(100, font)
    # заглавные буквы от базовой линии вверх на capHeight: середина ровно на 100
    assert abs((base - metrics.capHeight() / 2) - 100) < 0.01


def test_windows_icon_has_tray_sizes(app):
    icon = tray.windows_icon()
    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()} >= {16, 24, 32}


def bridge_for(url: str, role: str = "main"):
    updates = []
    host = SimpleNamespace(api_base="http://127.0.0.1:8877", tray=SimpleNamespace(update=updates.append))
    bridge = SimpleNamespace(host=host, view=SimpleNamespace(url=lambda: QUrl(url)), role=role)
    bridge._own_page = lambda: web_host.WindowBridge._own_page(bridge)
    return bridge, updates


def test_bridge_accepts_only_main_local_page():
    bridge, updates = bridge_for("http://127.0.0.1:8877/ui/")
    web_host.WindowBridge.tray(bridge, payload())
    web_host.WindowBridge.tray(bridge, "{oops")
    assert [state.plan for state in updates] == ["Plus"]
    for url, role in (("https://evil.example/", "main"), ("http://127.0.0.1:8878/ui/", "main"), ("http://127.0.0.1:8877/ui/", "sidebar")):
        other, foreign = bridge_for(url, role)
        web_host.WindowBridge.tray(other, payload())
        assert foreign == []
    bridge.host.tray = None
    web_host.WindowBridge.tray(bridge, payload())


@pytest.mark.skipif(sys.platform != "darwin", reason="нативный мост есть только на macOS")
def test_mac_bridge_exposes_tray():
    from koddi.desktop import native_web

    assert "tray" in native_web.BRIDGE_METHODS


@pytest.mark.skipif(sys.platform != "darwin", reason="строка меню есть только на macOS")
def test_mac_menu_matches_layout(app, monkeypatch):
    from AppKit import NSProgressIndicator

    from koddi.desktop import macos_tray

    monkeypatch.setattr(tray.time, "time", lambda: NOW)
    calls = []
    icon = macos_tray.MacTray(actions(calls))
    try:
        icon.update(tray.parse(payload()))
        items = list(icon.menu.itemArray())
        titles = [str(item.title()) for item in items]
        assert titles[2] == "Лимиты использования плана · Plus"
        assert titles[-3:] == ["Настройки использования…", "", "Завершить Докки"]
        assert "Обновить" not in titles
        bars = [view for item in items if item.view() is not None for view in item.view().subviews() if isinstance(view, NSProgressIndicator)]
        assert [bar.doubleValue() for bar in bars] == [80, 72]
        assert icon.item.button().image() is not None and icon.item.button().image().isTemplate()
        icon.update(tray.parse(json.dumps({"plan": None, "window": None, "total": None})))
        assert len(icon.menu.itemArray()) == 5
    finally:
        icon.remove()


def test_focus_view_moves_keyboard_only_for_known_roles(monkeypatch):
    # singleShot выполняем сразу: проверяется, какие роли доходят до хоста
    monkeypatch.setattr(web_host.QTimer, "singleShot", lambda _ms, fn: fn())
    bridge, _ = bridge_for("http://127.0.0.1:8877/ui/?role=composer", role="composer")
    asked = []
    bridge.host.focus_view = asked.append
    for role in ("sidebar", "main", "inspector", "composer", "product", "evil"):
        web_host.WindowBridge.focusView(bridge, role)
    assert asked == ["sidebar", "main", "inspector", "composer"]
    foreign, _ = bridge_for("https://evil.example/", role="main")
    foreign.host.focus_view = asked.append
    web_host.WindowBridge.focusView(foreign, "sidebar")
    assert asked == ["sidebar", "main", "inspector", "composer"]


@pytest.mark.skipif(sys.platform != "darwin", reason="нативный мост есть только на macOS")
def test_mac_bridge_exposes_focus_view():
    from koddi.desktop import native_web

    assert "focusView" in native_web.BRIDGE_METHODS


def test_long_chat_list_folds_under_more(app):
    calls = []
    icon = tray.QtTray(actions(calls))
    try:
        icon.update(panel_state(chats=[{"id": n, "title": f"Чат {n}", "mark": "done"} for n in range(1, 8)]))
        icon.panel.popup_at(QRect(500, 500, 20, 20))
        tray_panel.grab(icon.panel)
        assert {f"chat:{n}" for n in (1, 2, 3)} <= set(icon.panel.targets) and "chat:4" not in icon.panel.targets
        assert "more" in icon.panel.targets
        folded = icon.panel.height()
        icon.panel.activate("more")
        # список раскрывается в том же окне, меню не закрывается
        assert icon.panel.isVisible() and icon.panel.height() > folded
        tray_panel.grab(icon.panel)
        assert {f"chat:{n}" for n in range(1, 8)} <= set(icon.panel.targets) and "more" not in icon.panel.targets
        assert calls == []
    finally:
        icon.remove()


def test_chats_word():
    assert [tray_panel.chats_word(n) for n in (1, 2, 5, 11, 21, 22)] == ["чат", "чата", "чатов", "чатов", "чат", "чата"]
