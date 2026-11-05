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
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QSystemTrayIcon,
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


CHAT_MARKS = ("question", "working", "done")
MAX_CHATS = 12


@dataclass(frozen=True)
class ChatStatus:
    """Чат режима кода, который работает, ждёт ответа или закончил, пока его не открыли."""

    id: int
    title: str
    mark: str


@dataclass(frozen=True)
class TrayState:
    plan: str | None = None
    window: Limit | None = None
    window_hours: int = 5
    total: Limit | None = None
    theme: str = "dark"
    chats: tuple[ChatStatus, ...] = ()


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
    open_chat: Callable[[int], None] | None = None


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


def _text(value, limit: int) -> str:
    return "".join(ch for ch in value if ch.isprintable()).strip()[:limit] if isinstance(value, str) else ""


def _chats(raw) -> tuple[ChatStatus, ...]:
    """Чаты от страницы: только целые id, известные отметки; сначала те, что ждут ответа, потом работающие."""
    if not isinstance(raw, list):
        return ()
    result = []
    seen = set()
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        chat_id, mark = item.get("id"), item.get("mark")
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0 or chat_id in seen:
            continue
        if mark not in CHAT_MARKS:
            continue
        seen.add(chat_id)
        result.append(ChatStatus(chat_id, _text(item.get("title"), 80), mark))
    result.sort(key=lambda chat: CHAT_MARKS.index(chat.mark))
    return tuple(result[:MAX_CHATS])


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
    plan = _text(data.get("plan"), 40)
    window = data.get("window")
    hours = _number(window.get("hours")) if isinstance(window, dict) else None
    return TrayState(
        plan=plan or None,
        window=_limit(window),
        window_hours=int(hours) if hours is not None and 1 <= hours <= 168 else 5,
        total=_limit(data.get("total")),
        theme="light" if data.get("theme") == "light" else "dark",
        chats=_chats(data.get("chats")),
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
    """Значок в области уведомлений Windows: щелчок открывает окно, правый щелчок - своё меню (`tray_panel.py`)."""

    def __init__(self, actions: Actions, parent=None) -> None:
        from koddi.desktop.tray_panel import TrayPanel

        self.actions = actions
        self.state = TrayState()
        self.panel = TrayPanel(self)
        self.icon = QSystemTrayIcon(windows_icon(), parent)
        self.icon.setToolTip(APP_NAME)
        self.icon.activated.connect(self._activated)
        self._notice_click: Callable[[], None] | None = None
        self.icon.messageClicked.connect(self._message_clicked)
        self.icon.show()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.panel.hide()
            self.actions.open()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self.show_panel()

    def show_panel(self) -> None:
        """Меню над значком; время до сброса считается на момент открытия."""
        if self.panel.isVisible():
            self.panel.hide()
            return
        self.panel.popup_at(self.icon.geometry())

    def update(self, state: TrayState) -> None:
        self.state = state
        self.panel.refresh()

    def notify(self, title: str, body: str, on_click: Callable[[], None]) -> None:
        """Всплывающее уведомление Windows от значка в трее; щелчок по нему открывает чат."""
        self._notice_click = on_click
        self.icon.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 8000)

    def _message_clicked(self) -> None:
        if self._notice_click is not None:
            self._notice_click()

    def remove(self) -> None:
        self.panel.hide()
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
