import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton, QWidgetAction

from koddi import __version__
from koddi.desktop import native_web, tray, web_host

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


def menu_layout(menu):
    """Пункты меню Qt: виджеты, разделы, команды."""
    result = []
    for action in menu.actions():
        if isinstance(action, QWidgetAction):
            widget = action.defaultWidget()
            bars = widget.findChildren(QProgressBar)
            texts = [label.text() for label in widget.findChildren(QLabel)]
            buttons = [button.text() for button in widget.findChildren(QPushButton)]
            result.append(("widget", texts, buttons, [bar.value() for bar in bars]))
        elif action.isSeparator():
            result.append(("section", action.text()) if action.text() else ("separator",))
        else:
            result.append(("action", action.text()))
    return result


def test_windows_menu_matches_layout(app, monkeypatch):
    monkeypatch.setattr(tray.time, "time", lambda: NOW)
    calls = []
    icon = tray.QtTray(actions(calls))
    try:
        icon.update(tray.parse(payload()))
        assert menu_layout(icon.menu) == [
            ("widget", ["Докки", f"Версия {__version__}"], ["Нажмите, чтобы открыть"], []),
            ("separator",),
            ("section", "Лимиты использования плана · Plus"),
            ("widget", ["Лимит на 5 часов", "осталось 80 % · сброс через 2 ч"], [], [80]),
            ("widget", ["Подписка · все модели", "осталось 72 % · ещё 5 д."], [], [72]),
            ("separator",),
            ("action", "Настройки использования…"),
            ("separator",),
            ("action", "Завершить Докки"),
        ]
        assert not any(action.text() == "Обновить" for action in icon.menu.actions())
        icon.update(tray.parse(json.dumps({"plan": "Free", "window": None, "total": None})))
        assert [item[0] for item in menu_layout(icon.menu)] == ["widget", "separator", "action", "separator", "action"]
    finally:
        icon.remove()


def test_windows_menu_commands(app):
    calls = []
    icon = tray.QtTray(actions(calls))
    try:
        icon.menu.actions()[0].defaultWidget().findChild(QPushButton).click()
        commands = [action for action in icon.menu.actions() if not action.isSeparator() and not isinstance(action, QWidgetAction)]
        for action in commands:
            action.trigger()
        icon._activated(tray.QSystemTrayIcon.ActivationReason.Trigger)
        icon._activated(tray.QSystemTrayIcon.ActivationReason.Context)
        assert calls == ["open", "usage", "quit", "open"]
    finally:
        icon.remove()


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
