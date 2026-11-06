"""Меню значка в трее на Windows: всплывающее меню (Qt.Popup) со своим оформлением вместо QMenu.

Ведёт себя как системное меню трея: забирает фокус (SetForegroundWindow, как перед TrackPopupMenu),
закрывается кликом мимо, Esc, переключением в другое окно и крестиком; стрелки и Enter выбирают пункт.
Всё рисуется вручную: так подпись и знак стоят ровно по высоте (по высоте заглавных букв, а не по строке
шрифта с её отступами), как в макете «Плитки». На macOS меню нативное (`macos_tray.py`).
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QWidget

from koddi.desktop import splash

WIDTH = 300
SHADOW = 16
PAD = 12
# щелчок по значку при открытом меню закрывает его: Qt прячет меню по нажатию, а значок тут же открыл бы снова
REOPEN_GUARD = 0.35
GAP = SHADOW + 8
# свёрнутый список чатов: три строки, дальше строка «Ещё N» раскрывает остальные
COLLAPSED = 3

MARK_TEXT = {
    "working": "Работает",
    "question": "Ждёт ответа",
    "done": "Готово",
}


@dataclass(frozen=True)
class Palette:
    bg: QColor
    fg: QColor
    muted: QColor
    line: QColor
    tile: QColor
    hover: QColor
    track: QColor
    accent: QColor
    on_accent: QColor
    shadow: QColor


def palette(theme: str) -> Palette:
    if theme == "light":
        return Palette(
            QColor("#ffffff"), QColor("#141414"), QColor("#6b6b6b"), QColor(0, 0, 0, 22), QColor("#f3f3f4"),
            QColor(0, 0, 0, 14), QColor(0, 0, 0, 24), QColor("#2f6fe0"), QColor("#ffffff"), QColor(20, 30, 60, 46),
        )
    return Palette(
        QColor("#1f1f1f"), QColor("#f2f2f2"), QColor("#9a9a9a"), QColor(255, 255, 255, 22), QColor("#2a2a2a"),
        QColor(255, 255, 255, 18), QColor(255, 255, 255, 30), QColor("#438dff"), QColor("#ffffff"), QColor(0, 0, 0, 120),
    )


QUESTION = QColor("#f5a524")
DONE = QColor("#438dff")

_family: str | None = None


def font_family() -> str:
    """Inter из ресурсов страницы, как в самом приложении; без него - системный Segoe UI Variable."""
    global _family
    if _family is None:
        path = Path(splash.mark_path()).with_name("InterVariable.ttf")
        families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(path))) if path.exists() else []
        _family = families[0] if families else "Segoe UI Variable"
    return _family


def chats_word(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return "чатов"
    return {1: "чат", 2: "чата", 3: "чата", 4: "чата"}.get(count % 10, "чатов")


def make_font(px: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(font_family())
    font.setPixelSize(round(px))
    font.setWeight(weight)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def baseline_for(center_y: float, font: QFont) -> float:
    """Базовая линия, при которой заглавные буквы стоят ровно по центру: строка шрифта с её отступами сдвигает текст вниз."""
    return center_y + QFontMetricsF(font).capHeight() / 2


def panel_position(area: QRect, target: QRect, width: int, height: int, *, overflow: bool) -> QPoint:
    """Сначала размещаем видимую карточку целиком рядом с целью, затем учитываем тень."""
    def clamp(value: int, low: int, high: int) -> int:
        return max(low, min(value, max(low, high)))

    center = target.center()
    x = clamp(center.x() - width // 2, area.left(), area.right() - width + 1)
    y = clamp(center.y() - height // 2, area.top(), area.bottom() - height + 1)
    left = QPoint(target.left() - GAP - width, y)
    right = QPoint(target.right() + GAP + 1, y)
    above = QPoint(x, target.top() - GAP - height)
    below = QPoint(x, target.bottom() + GAP + 1)
    vertical = [above, below] if center.y() >= area.center().y() else [below, above]
    horizontal = [left, right] if center.x() >= area.center().x() else [right, left]
    candidates = horizontal + vertical if overflow else vertical + horizontal
    for point in candidates:
        if area.contains(QRect(point.x(), point.y(), width, height)):
            return point - QPoint(SHADOW, SHADOW)
    point = candidates[0]
    return QPoint(clamp(point.x(), area.left(), area.right() - width + 1) - SHADOW,
                  clamp(point.y(), area.top(), area.bottom() - height + 1) - SHADOW)


def overflow_rect(screen, point: QPoint) -> QRect | None:
    """Границы открытого списка скрытых значков в координатах Qt, если курсор находится внутри."""
    if sys.platform != "win32" or screen is None:
        return None
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user = ctypes.windll.user32
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    for name in ("TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow"):
        window = user.FindWindowW(name, None)
        if not window or not user.IsWindowVisible(window):
            continue
        rect, info = wintypes.RECT(), MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = user.MonitorFromWindow(window, 2)
        if not monitor or not user.GetWindowRect(window, ctypes.byref(rect)) or not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            continue
        ratio, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
        bounds = QRect(origin.x() + round((rect.left - info.monitor.left) / ratio),
                       origin.y() + round((rect.top - info.monitor.top) / ratio),
                       round((rect.right - rect.left) / ratio), round((rect.bottom - rect.top) / ratio))
        if bounds.contains(point):
            return bounds
    return None


def bring_forward(widget: QWidget) -> None:
    """Меню трея должно стать окном переднего плана, иначе Windows не закроет его кликом мимо."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.SetForegroundWindow(int(widget.winId()))
    except (AttributeError, OSError):
        pass


class TrayPanel(QWidget):
    """Плитки лимитов, статус чатов, «Открыть Докки» и ссылки; размеры считает `layout()`."""

    def __init__(self, owner) -> None:
        # тень рисуется своя, поэтому системная выключена
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        super().__init__(None, flags)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Меню Докки")
        self.hot: str | None = None
        self.expanded = False
        self.targets: dict[str, QRectF] = {}
        self.angle = 0.0
        self.mark = QPixmap(str(splash.mark_path()))
        self.anchor = QRect()
        self.overflow = None
        self.closed_at = 0.0
        self._was_active = False
        self.spin = QTimer(self)
        self.spin.setInterval(33)
        self.spin.timeout.connect(self._tick)

    # данные

    @property
    def state(self):
        return self.owner.state

    def chats(self):
        return list(self.state.chats)

    def visible_chats(self):
        """Чаты, которые видны сейчас, и сколько спрятано под строкой «Ещё N»."""
        chats = self.chats()
        if self.expanded or len(chats) <= COLLAPSED + 1:
            return chats, 0
        return chats[:COLLAPSED], len(chats) - COLLAPSED

    # раскладка

    def content_height(self) -> float:
        from koddi.desktop import tray

        height = PAD + 32 + 8
        shown, hidden = self.visible_chats()
        lines = (len(shown) or 1) + (1 if hidden else 0)
        height += lines * 34 + (lines - 1) * 4 + 8
        if tray.rows(self.state):
            height += 96 + 10
        height += 38 + 8 + 30 + PAD
        return height

    def popup_at(self, anchor: QRect, *, preserve_target: bool = False) -> None:
        """Над значком в трее (или над курсором), не вылезая за рабочую область экрана."""
        self.anchor = anchor
        if not self.isVisible():
            self.expanded = False
        size_w, size_h = WIDTH + 2 * SHADOW, math.ceil(self.content_height()) + 2 * SHADOW
        self.resize(size_w, size_h)
        cursor = QCursor.pos()
        point = anchor.center() if anchor.isValid() and not anchor.isEmpty() else cursor
        screen_point = self.overflow.center() if preserve_target and self.overflow else cursor
        screen = QGuiApplication.screenAt(screen_point) or QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.overflow = self.overflow if preserve_target else overflow_rect(screen, cursor)
        target = self.overflow or (anchor if anchor.isValid() and not anchor.isEmpty() else QRect(point, point))
        self.move(panel_position(area, target, WIDTH, size_h - 2 * SHADOW, overflow=bool(self.overflow)))
        if not preserve_target:
            self.hot = None
        opening = not self.isVisible()
        self.show()
        self.raise_()
        if opening:
            self._was_active = False
            self.activateWindow()
            bring_forward(self)
            self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._sync_spin()

    def just_closed(self) -> bool:
        return time.monotonic() - self.closed_at < REOPEN_GUARD

    def hideEvent(self, event) -> None:  # noqa: N802
        self.spin.stop()
        self.closed_at = time.monotonic()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        # переключились в другое окно - меню закрывается, как системное
        if event.type() == QEvent.Type.ActivationChange and self.isVisible():
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active:
                self.hide()
        super().changeEvent(event)

    def _sync_spin(self) -> None:
        if self.isVisible() and any(chat.mark == "working" for chat in self.chats()):
            self.spin.start()
        else:
            self.spin.stop()

    def refresh(self) -> None:
        if self.isVisible():
            self.popup_at(self.anchor, preserve_target=True)

    def _tick(self) -> None:
        self.angle = (self.angle + 12) % 360
        self.update()

    # мышь и клавиатура

    def _target_at(self, point: QPointF) -> str | None:
        for name, rect in self.targets.items():
            if rect.contains(point):
                return name
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hot = self._target_at(event.position())
        if hot != self.hot:
            self.hot = hot
            self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.hot:
            self.hot = None
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self._target_at(event.position())
        if target:
            self.activate(target)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            # пункт мог исчезнуть: «Ещё N» раскрыли, чат ушёл из списка
            self.activate(self.hot if self.hot in self.targets else "open")
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.move_hot(-1 if key in (Qt.Key.Key_Up, Qt.Key.Key_Backtab) else 1)
            return
        super().keyPressEvent(event)

    def move_hot(self, step: int) -> None:
        """Стрелки ходят по пунктам сверху вниз, как в меню; крестик пропускается."""
        order = [name for name in self.targets if name != "close"]
        if not order:
            return
        index = order.index(self.hot) + step if self.hot in order else (0 if step > 0 else -1)
        self.hot = order[index % len(order)]
        self.update()

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802
        # Tab остаётся внутри меню
        return False

    def activate(self, target: str) -> None:
        if target == "more":
            self.expanded = True
            self.popup_at(self.anchor, preserve_target=True)
            return
        self.hide()
        actions = self.owner.actions
        if target == "open":
            actions.open()
        elif target == "usage":
            actions.usage()
        elif target == "quit":
            actions.quit()
        elif target.startswith("chat:"):
            chat_id = int(target.split(":", 1)[1])
            if actions.open_chat is not None:
                actions.open_chat(chat_id)
            else:
                actions.open()

    # рисование

    def paintEvent(self, _event) -> None:  # noqa: N802
        from koddi.desktop import tray

        p = palette(self.state.theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card = QRectF(SHADOW, SHADOW, WIDTH, self.height() - 2 * SHADOW)
        self._shadow(painter, card, p)
        path = QPainterPath()
        path.addRoundedRect(card, 16, 16)
        painter.fillPath(path, p.bg)
        painter.setPen(QPen(p.line, 1))
        painter.drawPath(path)
        self.targets = {}
        left, right = card.left() + PAD, card.right() - PAD
        y = card.top() + PAD

        # шапка: знак, «Докки», точка и тариф, крестик - всё по одной средней линии
        mid = y + 16
        mark = QRectF(left + 2, mid - 9, 18, 18)
        self._mark(painter, mark, p.fg)
        title_font = make_font(15, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(p.fg)
        x = mark.right() + 9
        title_base = baseline_for(mid, title_font)
        painter.drawText(QPointF(x, title_base), "Докки")
        x += QFontMetricsF(title_font).horizontalAdvance("Докки")
        if self.state.plan:
            plan_font = make_font(13, QFont.Weight.Medium)
            painter.setFont(plan_font)
            painter.setPen(p.muted)
            painter.setBrush(p.muted)
            dot = QPointF(x + 8, title_base - QFontMetricsF(plan_font).xHeight() / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(dot, 1.6, 1.6)
            painter.setPen(p.muted)
            # низ тарифа на одной линии с низом названия, а не по своей середине: иначе мелкий текст висит выше
            painter.drawText(QPointF(x + 15, title_base), self.state.plan)
        close = QRectF(right - 26, mid - 13, 26, 26)
        version_font = make_font(11.5)
        painter.setFont(version_font)
        painter.setPen(p.muted)
        version = tray.version_text().removeprefix("Версия ")
        painter.drawText(QPointF(close.left() - 6 - QFontMetricsF(version_font).horizontalAdvance(version), title_base), version)
        self.targets["close"] = close
        self._hover(painter, close, p, radius=8)
        painter.setPen(QPen(p.fg if self.hot == "close" else p.muted, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        c = close.center()
        painter.drawLine(QPointF(c.x() - 4.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() + 4.5))
        painter.drawLine(QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x() - 4.5, c.y() + 4.5))
        y += 32 + 8

        # статус чатов: крутилка - работает, жёлтая точка - ждёт ответа, синяя - готово, а чат ещё не открыт
        chats, hidden = self.visible_chats()
        row_font = make_font(13)
        if not chats:
            row = QRectF(left - 2, y, right - left + 4, 34)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p.muted)
            painter.drawEllipse(QPointF(row.left() + 14, row.center().y()), 3.5, 3.5)
            painter.setFont(row_font)
            painter.setPen(p.muted)
            painter.drawText(QPointF(row.left() + 28, baseline_for(row.center().y(), row_font)), "Сейчас задач нет")
            y += 34
        for chat in chats:
            row = QRectF(left - 2, y, right - left + 4, 34)
            key = f"chat:{chat.id}"
            self.targets[key] = row
            self._hover(painter, row, p, radius=10, always=True)
            self._chat_mark(painter, QPointF(row.left() + 14, row.center().y()), chat.mark, p)
            painter.setFont(row_font)
            label = MARK_TEXT[chat.mark] + ": "
            painter.setPen(p.muted)
            base = baseline_for(row.center().y(), row_font)
            painter.drawText(QPointF(row.left() + 28, base), label)
            shift = QFontMetricsF(row_font).horizontalAdvance(label)
            painter.setPen(p.fg)
            room = row.right() - 10 - (row.left() + 28 + shift)
            painter.drawText(QPointF(row.left() + 28 + shift, base), QFontMetricsF(row_font).elidedText(chat.title or "Чат", Qt.TextElideMode.ElideRight, room))
            y += 34 + 4
        if hidden:
            row = QRectF(left - 2, y, right - left + 4, 34)
            self.targets["more"] = row
            self._hover(painter, row, p, radius=10, always=True)
            painter.setFont(row_font)
            painter.setPen(p.fg if self.hot == "more" else p.muted)
            base = baseline_for(row.center().y(), row_font)
            painter.drawText(QPointF(row.left() + 28, base), f"Ещё {hidden} {chats_word(hidden)}")
            # шеврон вниз: список раскрывается здесь же
            c = QPointF(row.left() + 14, row.center().y())
            painter.setPen(QPen(p.muted, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(c.x() - 4, c.y() - 2), QPointF(c.x(), c.y() + 2))
            painter.drawLine(QPointF(c.x(), c.y() + 2), QPointF(c.x() + 4, c.y() - 2))
            y += 34 + 4
        if chats:
            y -= 4
        y += 8

        # плитки лимитов
        rows = tray.rows(self.state)
        if rows:
            tiles = []
            if self.state.window is not None:
                left_text = tray.until(self.state.window.resets_at, tray.time.time())
                tiles.append((tray.hours_text(self.state.window_hours).capitalize(), self.state.window.remaining,
                              f"сброс через {left_text}" if left_text else "окно открыто"))
            if self.state.total is not None:
                left_text = tray.until(self.state.total.resets_at, tray.time.time())
                tiles.append(("Подписка", self.state.total.remaining, f"ещё {left_text}" if left_text else "все модели"))
            gap = 8
            width = (right - left - gap * (len(tiles) - 1)) / len(tiles)
            for index, (caption, percent, note) in enumerate(tiles):
                self._tile(painter, QRectF(left + index * (width + gap), y, width, 96), caption, percent, note, p)
            y += 96 + 10

        # «Открыть Докки»
        button = QRectF(left, y, right - left, 38)
        self.targets["open"] = button
        shape = QPainterPath()
        shape.addRoundedRect(button, 11, 11)
        painter.fillPath(shape, p.accent.lighter(112) if self.hot == "open" else p.accent)
        open_font = make_font(13.5, QFont.Weight.DemiBold)
        painter.setFont(open_font)
        painter.setPen(p.on_accent)
        text = "Открыть Докки"
        painter.drawText(QPointF(button.center().x() - QFontMetricsF(open_font).horizontalAdvance(text) / 2,
                                 baseline_for(button.center().y(), open_font)), text)
        y += 38 + 8

        # «Использование» и «Завершить» - две серые кнопки поменьше под главной
        small_font = make_font(12.5, QFont.Weight.Medium)
        painter.setFont(small_font)
        width = (right - left - 8) / 2
        for index, (key, text) in enumerate((("usage", "Использование"), ("quit", "Завершить"))):
            rect = QRectF(left + index * (width + 8), y, width, 30)
            self.targets[key] = rect
            shape = QPainterPath()
            shape.addRoundedRect(rect, 9, 9)
            painter.fillPath(shape, p.hover if self.hot == key else p.tile)
            painter.setPen(p.fg if self.hot == key else p.muted)
            painter.drawText(QPointF(rect.center().x() - QFontMetricsF(small_font).horizontalAdvance(text) / 2,
                                     baseline_for(rect.center().y(), small_font)), text)
        painter.end()

    def _shadow(self, painter: QPainter, card: QRectF, p: Palette) -> None:
        # мягкая тень из нескольких полупрозрачных скруглений: без QGraphicsEffect окно не перерисовывается целиком
        for step in range(SHADOW, 0, -2):
            color = QColor(p.shadow)
            color.setAlphaF(p.shadow.alphaF() * (1 - step / SHADOW) ** 2 / 4)
            ring = QPainterPath()
            ring.addRoundedRect(card.adjusted(-step, -step + 4, step, step + 4), 16 + step, 16 + step)
            painter.fillPath(ring, color)

    def _hover(self, painter: QPainter, rect: QRectF, p: Palette, radius: float, always: bool = False) -> None:
        key = next((name for name, value in self.targets.items() if value == rect), None)
        if key is not None and self.hot == key:
            shape = QPainterPath()
            shape.addRoundedRect(rect, radius, radius)
            painter.fillPath(shape, p.hover)
        elif always:
            shape = QPainterPath()
            shape.addRoundedRect(rect, radius, radius)
            painter.fillPath(shape, QColor(p.hover.red(), p.hover.green(), p.hover.blue(), p.hover.alpha() // 2))

    def _mark(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        # знак белый: перекрашивается под тему через маску
        tinted = QPixmap(self.mark.size())
        tinted.fill(Qt.GlobalColor.transparent)
        mask = QPainter(tinted)
        mask.drawPixmap(0, 0, self.mark)
        mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        mask.fillRect(tinted.rect(), color)
        mask.end()
        painter.drawPixmap(rect, tinted, QRectF(tinted.rect()))

    def _chat_mark(self, painter: QPainter, center: QPointF, mark: str, p: Palette) -> None:
        painter.save()
        if mark == "working":
            painter.setPen(QPen(p.track, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 5.5, 5.5)
            painter.setPen(QPen(p.fg, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(center.x() - 5.5, center.y() - 5.5, 11, 11), int(-self.angle * 16), 100 * 16)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QUESTION if mark == "question" else DONE)
            painter.drawEllipse(center, 4, 4)
        painter.restore()

    def _tile(self, painter: QPainter, rect: QRectF, caption: str, percent: int, note: str, p: Palette) -> None:
        shape = QPainterPath()
        shape.addRoundedRect(rect, 12, 12)
        painter.fillPath(shape, p.tile)
        inner = rect.adjusted(12, 10, -12, -12)
        small = make_font(11.5)
        painter.setFont(small)
        painter.setPen(p.muted)
        painter.drawText(QPointF(inner.left(), baseline_for(inner.top() + 7, small)), caption)
        big = make_font(25, QFont.Weight.Bold)
        painter.setFont(big)
        painter.setPen(p.fg)
        number = str(percent)
        number_base = baseline_for(inner.top() + 33, big)
        painter.drawText(QPointF(inner.left(), number_base), number)
        unit = make_font(13, QFont.Weight.Medium)
        painter.setFont(unit)
        painter.setPen(p.muted)
        painter.drawText(QPointF(inner.left() + QFontMetricsF(big).horizontalAdvance(number) + 2, number_base), "%")
        painter.setFont(small)
        painter.drawText(QPointF(inner.left(), baseline_for(inner.top() + 56, small)),
                         QFontMetricsF(small).elidedText(note, Qt.TextElideMode.ElideRight, inner.width()))
        track = QRectF(inner.left(), inner.bottom() - 4, inner.width(), 4)
        bar = QPainterPath()
        bar.addRoundedRect(track, 2, 2)
        painter.fillPath(bar, p.track)
        if percent > 0:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(track.left(), track.top(), max(4.0, track.width() * percent / 100), 4), 2, 2)
            painter.fillPath(fill, p.accent)


def grab(panel: TrayPanel) -> QPixmap:
    """Снимок меню для проверок и макетов."""
    panel.resize(WIDTH + 2 * SHADOW, math.ceil(panel.content_height()) + 2 * SHADOW)
    return panel.grab()

