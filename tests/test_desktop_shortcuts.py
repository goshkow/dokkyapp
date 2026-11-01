import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from koddi.desktop import shortcuts


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def menu_action(self, action: str) -> None:
        self.calls.append(action)

    def open_project_folder(self) -> None:
        self.calls.append("open-project")

    def toggle_fullscreen(self) -> None:
        self.calls.append("fullscreen")


def by_key(actions, key: str):
    wanted = QKeySequence(key)
    return next(item for item in actions if wanted in item.shortcuts())


def test_windows_shortcuts_run_the_same_page_commands_as_mac_menu(app):
    host = Host()
    actions = shortcuts.install(host)
    for key in ("Ctrl+N", "Ctrl+,", "Ctrl+Alt+S", "Ctrl+=", "Ctrl+0", "Alt+Left", "Ctrl+3", "Ctrl+O", "F11"):
        by_key(actions, key).trigger()
    assert host.calls == ["new-chat", "settings", "toggle-left", "text-bigger", "text-reset", "back", "scope-development", "open-project", "fullscreen"]
    # сочетания висят на окне: работают и когда фокус внутри фрейма сайта
    assert all(item in host.actions() for item in actions)


def test_no_shortcut_is_taken_twice(app):
    keys = [sequence.toString() for item in shortcuts.install(Host()) for sequence in item.shortcuts()]
    assert len(keys) == len(set(keys))
