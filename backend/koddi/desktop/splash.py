"""Заставка до загрузки страницы: тот же знак и то же «дыхание», что у экрана входа, чтобы окно не меняло вид.

Тема, цвет фона и признак стекла запоминаются в startup.json при каждом запуске: окно сразу открывается
с правильными скруглением, стеклом и фоном, а не перестраивается после загрузки интерфейса.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRect, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

DEFAULT = {"theme": "dark", "glass": False, "color": [0.078, 0.078, 0.078]}
MARK = 64
# кнопки окна Windows: размеры, значки и цвета те же, что у страницы (desktop-window.js и .css), чтобы при смене ничего не прыгало
CAPTION_HEIGHT = 42
CAPTION_BUTTON = 46
CAPTION_ACTIONS = ("minimize", "maximize", "close")
CAPTION_GLYPHS = {"minimize": "\ue921", "maximize": "\ue922", "restore": "\ue923", "close": "\ue8bb"}
CAPTION_COLORS = {
    "dark": {"text": "#f5f5f5", "hover": "#343434", "active": "#333333"},
    "light": {"text": "#171717", "hover": "#e5e5e5", "active": "#d8d8d8"},
}


def _file() -> Path:
    from koddi.config import data_dir

    return data_dir() / "startup.json"


def load() -> dict:
    """Последний вид окна; при любой ошибке - тёмная тема без стекла."""
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
        color = [min(1.0, max(0.0, float(v))) for v in raw["color"]][:3]
        if len(color) == 3 and raw.get("theme") in ("dark", "light"):
            return {"theme": raw["theme"], "glass": bool(raw.get("glass")), "color": color}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return dict(DEFAULT)


def glass_turned_off() -> bool | None:
    """Выбор стекла: True - выключено, False - включено, None - выбора ещё не было. Поле glass этого не значит:
    при первом запуске там False, пока страница не включила стекло."""
    try:
        value = json.loads(_file().read_text(encoding="utf-8")).get("glass_off")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, bool) else None


def save(theme: str, glass: bool, color, *, glass_off: bool | None = None) -> None:
    """`glass_off` меняется только при переключении стекла, остальные записи его сохраняют."""
    try:
        rgb = [round(float(v), 4) for v in color][:3]
        if len(rgb) == 3 and theme in ("dark", "light"):
            off = glass_turned_off() if glass_off is None else glass_off
            look = {"theme": theme, "glass": bool(glass), "color": rgb, **({} if off is None else {"glass_off": off})}
            _file().write_text(json.dumps(look), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass


def mark_path() -> Path:
    return Path(__file__).resolve().parent / "web-next" / "assets" / "mark.png"


class Splash(QWidget):
    """Знак по центру, непрозрачность 0.5-1 за 1.4 с (auth-breathe). Со стеклом фон рисует подложка окна.

    На Windows кнопки окна рисует страница, поэтому до её загрузки их рисует заставка (передан `window_action`).
    """

    def __init__(self, parent: QWidget, look: dict, window_action=None):
        super().__init__(parent)
        self.look = look
        self.window_action = window_action
        self.hovered = ""
        self.pressed = ""
        self.caption_pressed_at = 0.0
        self.setMouseTracking(window_action is not None)
        self.pixmap: QPixmap | None = None
        self.level = 0.5
        self.message = ""
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.5)
        self.animation.setKeyValueAt(0.5, 1.0)
        self.animation.setEndValue(0.5)
        self.animation.setDuration(1400)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.animation.setLoopCount(-1)
        self.animation.valueChanged.connect(self._tick)
        self.animation.start()

    def _tick(self, value) -> None:
        self.level = float(value)
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802
        """Сообщение об ошибке запуска под знаком; дыхание прекращается."""
        self.message = text
        self.animation.stop()
        self.level = 1.0
        self.update()

    def stop(self) -> None:
        self.animation.stop()
        self.hide()

    def _mark(self) -> QPixmap:
        """Знак под настоящий масштаб экрана: на 125% и 150% готовая картинка 2x легла бы с лесенкой."""
        ratio = self.devicePixelRatioF()
        if self.pixmap is None or self.pixmap.devicePixelRatio() != ratio:
            size = round(MARK * ratio)
            mark = QPixmap(str(mark_path())).scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            if self.look["theme"] == "light":
                # знак белый: на светлом фоне переворачиваем цвет. В premultiplied края знака дали бы ореол
                image = mark.toImage().convertToFormat(QImage.Format.Format_ARGB32)
                image.invertPixels(QImage.InvertMode.InvertRgb)
                mark = QPixmap.fromImage(image)
            mark.setDevicePixelRatio(ratio)
            self.pixmap = mark
        return self.pixmap

    def _caption_action(self, x: float, y: float) -> str:
        """Кнопка окна под точкой; пусто - кнопки нет."""
        left = self.width() - CAPTION_BUTTON * len(CAPTION_ACTIONS)
        if self.window_action is None or not (0 <= y < CAPTION_HEIGHT and left <= x < self.width()):
            return ""
        return CAPTION_ACTIONS[int((x - left) // CAPTION_BUTTON)]

    def _set_hovered(self, action: str) -> None:
        if action != self.hovered:
            self.hovered = action
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._set_hovered(self._caption_action(event.position().x(), event.position().y()))

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._set_hovered("")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.window_action is None or event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        self.pressed = self._caption_action(x, y)
        self.update()
        if self.pressed or y >= CAPTION_HEIGHT:
            return
        # перемещение окна забирает себе отпускание и второй щелчок, поэтому двойной щелчок считаем сами
        now = time.monotonic()
        double = now - self.caption_pressed_at < QApplication.doubleClickInterval() / 1000
        self.caption_pressed_at = 0.0 if double else now
        if double:
            self.window_action("maximize")
        elif handle := self.window().windowHandle():
            handle.startSystemMove()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        action, self.pressed = self.pressed, ""
        self.update()
        if action and action == self._caption_action(event.position().x(), event.position().y()):
            self.window_action(action)

    def _paint_caption(self, painter: QPainter) -> None:
        colors = CAPTION_COLORS[self.look["theme"]]
        font = QFont()
        font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        font.setPixelSize(10)
        painter.setFont(font)
        left = self.width() - CAPTION_BUTTON * len(CAPTION_ACTIONS)
        for index, action in enumerate(CAPTION_ACTIONS):
            area = QRect(left + index * CAPTION_BUTTON, 0, CAPTION_BUTTON, CAPTION_HEIGHT)
            pressed, hovered = self.pressed == action, self.hovered == action
            if action == "close" and (pressed or hovered):
                painter.fillRect(area, QColor("#b02519" if pressed else "#c42b1c"))
            elif pressed or hovered:
                painter.fillRect(area, QColor(colors["active" if pressed else "hover"]))
            painter.setPen(QColor("#ffffff") if action == "close" and (pressed or hovered) else QColor(colors["text"]))
            glyph = "restore" if action == "maximize" and self.window().isMaximized() else action
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, CAPTION_GLYPHS[glyph])

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.look["glass"]:
            r, g, b = (round(v * 255) for v in self.look["color"])
            painter.fillRect(self.rect(), QColor(r, g, b))
        painter.setOpacity(self.level)
        painter.drawPixmap((self.width() - MARK) // 2, (self.height() - MARK) // 2, self._mark())
        painter.setOpacity(1.0)
        if self.message:
            painter.setPen(QColor("#aaaaaa") if self.look["theme"] == "dark" else QColor("#555555"))
            area = self.rect().adjusted(24, (self.height() + MARK) // 2 + 16, -24, 0)
            painter.drawText(area, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.message)
        if self.window_action is not None:
            self._paint_caption(painter)
