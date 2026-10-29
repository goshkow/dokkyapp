"""Значок Докки в строке меню macOS: NSStatusItem с нативным меню (заголовок, лимиты с полосками, команды).

Qt на macOS ставит QMenu в строку меню как NSMenu и виджеты в нём не показывает, поэтому полоски
лимитов - нативные NSProgressIndicator внутри видов пунктов меню. Модель меню общая с Windows (`tray.py`).
"""

from __future__ import annotations

import objc
from PySide6.QtCore import QTimer

from koddi.desktop import splash, tray

WIDTH = 320
PAD = 14
_classes: dict = {}


def _objc_classes():
    """Объектные классы создаются при первом использовании: определить класс ObjC дважды нельзя."""
    if _classes:
        return _classes
    from AppKit import NSView
    from Foundation import NSObject

    class TrayTarget(NSObject):
        def initWithOwner_(self, owner):  # noqa: N802
            self = objc.super(TrayTarget, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        def open_(self, _sender):
            self.owner.run("open")

        def usage_(self, _sender):
            self.owner.run("usage")

        def quit_(self, _sender):
            self.owner.run("quit")

        # меню собирается перед каждым открытием: время до сброса считается на этот момент
        def menuNeedsUpdate_(self, menu):  # noqa: N802
            self.owner.render()

    class TrayRowView(NSView):
        def isFlipped(self):  # noqa: N802
            return True

    class TrayHeaderView(TrayRowView):
        # щелчок по заголовку, как и по кнопке, открывает окно
        def mouseUp_(self, _event):  # noqa: N802
            self.target.open_(self)

    _classes.update(target=TrayTarget, row=TrayRowView, header=TrayHeaderView)
    return _classes


def _label(text: str, size: float, secondary: bool = False, bold: bool = False):
    from AppKit import NSColor, NSFont, NSTextField

    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
    label.sizeToFit()
    return label


def _status_image():
    from AppKit import NSImage

    image = NSImage.alloc().initWithContentsOfFile_(str(splash.mark_path()))
    if image is None:
        return None
    image.setSize_((18, 18))
    # шаблон: система сама красит знак под светлую и тёмную строку меню
    image.setTemplate_(True)
    return image


class MacTray:
    def __init__(self, actions: tray.Actions) -> None:
        from AppKit import NSMenu, NSStatusBar

        classes = _objc_classes()
        self.actions = actions
        self.state = tray.TrayState()
        self.target = classes["target"].alloc().initWithOwner_(self)
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)  # NSVariableStatusItemLength
        button = self.item.button()
        image = _status_image()
        if image is not None:
            button.setImage_(image)
        else:
            button.setTitle_(tray.APP_NAME)
        button.setToolTip_(tray.APP_NAME)
        button.setAccessibilityLabel_(tray.APP_NAME)
        self.menu = NSMenu.alloc().initWithTitle_(tray.APP_NAME)
        self.menu.setAutoenablesItems_(False)
        self.menu.setMinimumWidth_(WIDTH)
        self.menu.setDelegate_(self.target)
        self.item.setMenu_(self.menu)
        self.render()

    def run(self, name: str) -> None:
        """Команда меню: меню закрывается, действие выполняется уже после выхода из его цикла событий."""
        self.menu.cancelTracking()
        QTimer.singleShot(0, getattr(self.actions, name))

    def update(self, state: tray.TrayState) -> None:
        self.state = state
        self.render()

    def _item_with_view(self, view):
        from AppKit import NSMenuItem

        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        item.setView_(view)
        self.menu.addItem_(item)
        return item

    def _header(self):
        from AppKit import NSButton, NSControlSizeSmall

        view = _objc_classes()["header"].alloc().initWithFrame_(((0, 0), (WIDTH, 50)))
        view.target = self.target
        view.setAutoresizingMask_(2)  # NSViewWidthSizable
        title = _label(tray.APP_NAME, 13, bold=True)
        title.setFrameOrigin_((PAD, 8))
        version = _label(tray.version_text(), 11, secondary=True)
        version.setFrameOrigin_((PAD, 27))
        button = NSButton.buttonWithTitle_target_action_(tray.OPEN_HINT, self.target, "open:")
        button.setControlSize_(NSControlSizeSmall)
        button.sizeToFit()
        width, height = button.frame().size
        button.setFrameOrigin_((WIDTH - PAD - width, (50 - height) / 2))
        button.setAutoresizingMask_(1)  # NSViewMinXMargin: кнопка остаётся у правого края
        for child in (title, version, button):
            view.addSubview_(child)
        return view

    def _limit(self, row: tray.Row):
        from AppKit import NSProgressIndicator, NSTextAlignmentRight

        view = _objc_classes()["row"].alloc().initWithFrame_(((0, 0), (WIDTH, 44)))
        view.setAutoresizingMask_(2)
        title = _label(row.title, 13)
        title.setFrameOrigin_((PAD, 4))
        detail = _label(row.detail, 12, secondary=True)
        detail.setAlignment_(NSTextAlignmentRight)
        width, height = detail.frame().size
        detail.setFrame_(((WIDTH - PAD - width, 5), (width, height)))
        detail.setAutoresizingMask_(1)
        bar = NSProgressIndicator.alloc().initWithFrame_(((PAD, 26), (WIDTH - 2 * PAD, 10)))
        bar.setStyle_(0)  # NSProgressIndicatorStyleBar
        bar.setIndeterminate_(False)
        bar.setMinValue_(0)
        bar.setMaxValue_(100)
        bar.setDoubleValue_(row.percent)
        bar.setControlSize_(1)  # NSControlSizeSmall
        bar.setAutoresizingMask_(2)
        bar.setAccessibilityLabel_(f"{row.title}: {row.detail}")
        for child in (title, detail, bar):
            view.addSubview_(child)
        return view

    def _command(self, title: str, action: str, key: str = ""):
        from AppKit import NSMenuItem

        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        item.setTarget_(self.target)
        self.menu.addItem_(item)

    def render(self) -> None:
        from AppKit import NSMenuItem

        self.menu.removeAllItems()
        self._item_with_view(self._header())
        self.menu.addItem_(NSMenuItem.separatorItem())
        limit_rows = tray.rows(self.state)
        if limit_rows:
            title = tray.section_title(self.state)
            if hasattr(NSMenuItem, "sectionHeaderWithTitle_"):
                self.menu.addItem_(NSMenuItem.sectionHeaderWithTitle_(title))
            else:
                # до macOS 14 заголовка раздела нет: неактивный пункт
                header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
                header.setEnabled_(False)
                self.menu.addItem_(header)
            for row in limit_rows:
                self._item_with_view(self._limit(row))
            self.menu.addItem_(NSMenuItem.separatorItem())
        self._command(tray.USAGE_TITLE, "usage:")
        self.menu.addItem_(NSMenuItem.separatorItem())
        self._command(tray.QUIT_TITLE, "quit:", "q")

    def remove(self) -> None:
        from AppKit import NSStatusBar

        NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
