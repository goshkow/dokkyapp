"""Горячие клавиши окна на Windows: на macOS те же действия стоят в строке меню (macos_menu) со своими сочетаниями."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence

# команда страницы и её сочетания - те же, что у меню macOS, только Ctrl вместо Cmd
PAGE_SHORTCUTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("new-chat", ("Ctrl+N",)),
    ("chat-search", ("Ctrl+F",)),
    ("palette", ("Ctrl+K",)),
    ("settings", ("Ctrl+,",)),
    ("toggle-left", ("Ctrl+Alt+S",)),
    ("toggle-right", ("Ctrl+Alt+I",)),
    ("text-bigger", ("Ctrl+=", "Ctrl++")),
    ("text-smaller", ("Ctrl+-",)),
    ("text-reset", ("Ctrl+0",)),
    ("back", ("Alt+Left",)),
    ("forward", ("Alt+Right",)),
    ("scope-chat", ("Ctrl+1",)),
    ("scope-search", ("Ctrl+2",)),
    ("scope-development", ("Ctrl+3",)),
)


def install(host) -> list[QAction]:
    """Сочетания живут на окне, а не в странице: так они работают и во фрейме сайта, куда клавиши страницы не доходят."""
    actions: list[QAction] = []

    def add(keys: tuple[str, ...], handler) -> None:
        item = QAction(host)
        item.setShortcuts([QKeySequence(key) for key in keys])
        item.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        item.triggered.connect(handler)
        host.addAction(item)
        actions.append(item)

    for action, keys in PAGE_SHORTCUTS:
        add(keys, lambda *_, action=action: host.menu_action(action))
    add(("Ctrl+O",), lambda *_: host.open_project_folder())
    add(("F11",), lambda *_: host.toggle_fullscreen())
    return actions
