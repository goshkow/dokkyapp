from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QRect

from koddi.desktop import web_host

WEB = Path("backend/koddi/desktop/web")


def test_navigation_overlays_page_without_separate_top_strip():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "app-topbar" not in html
    for name in ("toggle-left", "nav-back", "nav-forward", "toggle-right", "account-menu-toggle", "sidebar-settings"):
        assert f'id="{name}"' in html
    assert "sidebar-bottom-actions" not in html
    # «Новый чат» первым в списке разделов, а не синей кнопкой в подвале
    nav = html.split('<nav class="sidebar-nav"', 1)[1]
    assert nav.index("new-chat") < nav.index('id="chat-search"') < nav.index('data-page="projects"')
    css = (WEB / "app-shell.css").read_text(encoding="utf-8")
    assert "--side: var(--bg)" in css
    assert ".shell-controls {" in css and "position: absolute" in css


def test_shell_script_loads_before_dokky_so_window_controls_are_injected():
    # после dokky.js любой скрипт ломает внедрение кнопок окна в Qt WebEngine
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index("app-shell.js") < html.index("dokky.js")
    source = (WEB / "app-shell.js").read_text(encoding="utf-8")
    assert "DOMContentLoaded" in source


def test_right_panel_is_cards_that_keep_existing_ids():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in (
        "environment", "context-percent", "inspector-diff", "inspector-files", "task-plan",
        "turn", "change-summary", "change-files", "diff", "rollback", "file-tree", "editor-view",
    ):
        assert f'id="{name}"' in html
    for key in ("diff", "files", "plan"):
        assert f'data-inspector="{key}"' in html
    assert html.count("card-head") >= 5


def _host(geometry, available, maximized=False):
    state = {"geometry": geometry}
    host = SimpleNamespace(
        isMaximized=lambda: maximized,
        isFullScreen=lambda: False,
        isMinimized=lambda: False,
        minimumWidth=lambda: 960,
        screen=lambda: SimpleNamespace(availableGeometry=lambda: available),
        geometry=lambda: state["geometry"],
        setGeometry=lambda x, y, w, h: state.update(geometry=QRect(x, y, w, h)),
        _move_geometry=lambda target, start, duration: state.update(geometry=target, duration=duration),
    )
    return host, state


def test_window_grows_to_the_right_first_and_falls_back_to_the_left():
    available = QRect(0, 0, 1920, 1080)
    host, state = _host(QRect(100, 50, 1000, 700), available)
    assert web_host.WebHost.adjust_width(host, "right", 280, 380) == {"left": 0, "right": 280}
    assert state["geometry"] == QRect(100, 50, 1280, 700) and state["duration"] == 380
    # у правого края экрана остаток добирается слева
    host, state = _host(QRect(900, 50, 1000, 700), available)
    assert web_host.WebHost.adjust_width(host, "right", 280) == {"left": 260, "right": 20}
    assert state["geometry"].right() + 1 == 1920 and state["geometry"].width() == 1280


def test_window_width_changes_are_bounded_and_skipped_when_maximized():
    available = QRect(0, 0, 1920, 1080)
    host, state = _host(QRect(0, 0, 1920, 1080), available, maximized=True)
    assert web_host.WebHost.adjust_width(host, "right", 280) == {"left": 0, "right": 0}
    host, _ = _host(QRect(0, 0, 1000, 700), available)
    assert web_host.WebHost.adjust_width(host, "top", 100) == {"left": 0, "right": 0}
    assert web_host.WebHost.adjust_width(host, "right", 5000) == {"left": 0, "right": 0}
    # возврат не уменьшает окно ниже минимального размера
    host, state = _host(QRect(0, 0, 1000, 700), available)
    assert web_host.WebHost.adjust_width(host, "right", -500) == {"left": 0, "right": -40}
    assert state["geometry"].width() == 960
