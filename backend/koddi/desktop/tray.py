"""Значок в трее: строка меню macOS и область уведомлений Windows, меню как у значка Claude.

Тариф и лимиты присылает страница (`state/tray.ts`) - те же данные, что у плашки внизу левого меню; хост сам
`/subscription` не читает. Здесь общая модель меню и реализация на Qt (Windows); на macOS меню нативное
(`macos_tray.py`), потому что виджеты в меню строки меню Qt не показывает.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QMenu, QProgressBar, QPushButton, QSystemTrayIcon, QWidget, QWidgetAction,
)

from koddi.desktop import splash

APP_NAME = "Докки"
MAX_PAYLOAD = 4096
OPEN_HINT = "Нажмите, чтобы открыть"
USAGE_TITLE = "Настройки использования…"
QUIT_TITLE = f"Завершить {APP_NAME}"


@dataclass(frozen=True)
class Limit:
    remaining: int
    resets_at: float | None


@dataclass(frozen=True)
class TrayState:
    plan: str | None = None
    window: Limit | None = None
    window_hours: int = 5
    total: Limit | None = None


@dataclass(frozen=True)
class Row:
    title: str
    percent: int
    detail: str


@dataclass(frozen=True)
class Actions:
    open: Callable[[], None]
    usage: Callable[[], None]
    quit: Callable[[], None]


def _number(value) -> float | None:
    # bool - тоже int, но от страницы это всегда ошибка
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _limit(raw) -> Limit | None:
    if not isinstance(raw, dict):
        return None
    remaining = _number(raw.get("remaining"))
    if remaining is None:
        return None
    at = _number(raw.get("resets_at"))
    return Limit(max(0, min(100, round(remaining))), at if at is not None and at > 0 else None)


def parse(payload: str) -> TrayState | None:
    """Данные от страницы; None - сообщение битое, прежнее меню остаётся."""
    if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    plan = data.get("plan")
    plan = "".join(ch for ch in plan if ch.isprintable()).strip()[:40] if isinstance(plan, str) else ""
    window = data.get("window")
    hours = _number(window.get("hours")) if isinstance(window, dict) else None
    return TrayState(
        plan=plan or None,
        window=_limit(window),
        window_hours=int(hours) if hours is not None and 1 <= hours <= 168 else 5,
        total=_limit(data.get("total")),
    )


def hours_text(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        word = "часов"
    else:
        word = {1: "час", 2: "часа", 3: "часа", 4: "часа"}.get(count % 10, "часов")
    return f"{count} {word}"


def until(at: float | None, now: float) -> str:
    """Сколько осталось до момента: «35 мин», «3 ч», «5 д.»; прошедший или неизвестный момент - пусто."""
    if at is None or at <= now:
        return ""
    left = at - now
    if left < 3600:
        return f"{max(1, math.ceil(left / 60))} мин"
    if left < 86400:
        return f"{max(1, round(left / 3600))} ч"
    return f"{max(1, round(left / 86400))} д."


def rows(state: TrayState, now: float | None = None) -> list[Row]:
    """Строки лимитов; лимит без данных не показывается."""
    now = time.time() if now is None else now
    result = []
    if state.window is not None:
        left = until(state.window.resets_at, now)
        result.append(Row(
            f"Лимит на {hours_text(state.window_hours)}",
            state.window.remaining,
            f"осталось {state.window.remaining} %" + (f" · сброс через {left}" if left else ""),
        ))
    if state.total is not None:
        # у общего остатка сервер даёт только срок подписки, отдельного времени сброса нет
        left = until(state.total.resets_at, now)
        result.append(Row(
            "Подписка · все модели",
            state.total.remaining,
            f"осталось {state.total.remaining} %" + (f" · ещё {left}" if left else ""),
        ))
    return result


def section_title(state: TrayState) -> str:
    return "Лимиты использования плана" + (f" · {state.plan}" if state.plan else "")


def version_text() -> str:
    from koddi import __version__

    return f"Версия {__version__}"


def windows_icon() -> QIcon:
    """Знак значка программы: белый мишка на тёмном скруглённом квадрате, виден на светлой и тёмной панели задач."""
    mark = QPixmap(str(splash.mark_path()))
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        painter.fillPath(path, QColor("#141414"))
        inset = round(size * 0.14)
        painter.drawPixmap(inset, inset, size - 2 * inset, size - 2 * inset, mark)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class QtTray:
    """Значок в области уведомлений Windows: щелчок открывает окно, правый щелчок - меню с лимитами."""

    def __init__(self, actions: Actions, parent=None) -> None:
        self.actions = actions
        self.state = TrayState()
        self.menu = QMenu(parent)
        self.menu.aboutToShow.connect(self.render)
        self.icon = QSystemTrayIcon(windows_icon(), parent)
        self.icon.setToolTip(APP_NAME)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.render()
        self.icon.show()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.actions.open()

    def update(self, state: TrayState) -> None:
        self.state = state
        self.render()

    def _widget_action(self, widget: QWidget) -> QWidgetAction:
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(widget)
        self.menu.addAction(action)
        return action

    def _run(self, handler: Callable[[], None]) -> Callable[..., None]:
        def run(*_):
            self.menu.hide()
            handler()
        return run

    def _header(self) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        layout.setContentsMargins(12, 8, 12, 8)
        title = QLabel(APP_NAME)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        version = QLabel(version_text())
        version.setEnabled(False)
        button = QPushButton(OPEN_HINT)
        button.clicked.connect(self._run(self.actions.open))
        layout.addWidget(title, 0, 0)
        layout.addWidget(version, 1, 0)
        layout.addWidget(button, 0, 1, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.setColumnStretch(0, 1)
        return widget

    def _limit(self, row: Row) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        layout.setContentsMargins(12, 4, 12, 6)
        layout.setVerticalSpacing(4)
        detail = QLabel(row.detail)
        detail.setEnabled(False)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(row.percent)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setMinimumWidth(260)
        bar.setAccessibleName(f"{row.title}: {row.detail}")
        layout.addWidget(QLabel(row.title), 0, 0)
        layout.addWidget(detail, 0, 1, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(bar, 1, 0, 1, 2)
        return widget

    def render(self) -> None:
        """Меню собирается заново при открытии: время до сброса считается на этот момент."""
        self.menu.clear()
        self._widget_action(self._header())
        self.menu.addSeparator()
        limit_rows = rows(self.state)
        if limit_rows:
            self.menu.addSection(section_title(self.state))
            for row in limit_rows:
                self._widget_action(self._limit(row))
            self.menu.addSeparator()
        usage = QAction(USAGE_TITLE, self.menu)
        usage.triggered.connect(self._run(self.actions.usage))
        self.menu.addAction(usage)
        self.menu.addSeparator()
        leave = QAction(QUIT_TITLE, self.menu)
        leave.triggered.connect(self._run(self.actions.quit))
        self.menu.addAction(leave)

    def remove(self) -> None:
        self.icon.hide()


def install(actions: Actions, parent=None):
    """Значок трея для текущей системы; None - трея нет (например, панель без области уведомлений)."""
    import sys

    if sys.platform == "darwin":
        from koddi.desktop import macos_tray

        return macos_tray.MacTray(actions)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    return QtTray(actions, parent)
