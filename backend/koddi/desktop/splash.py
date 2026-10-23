"""Заставка до загрузки страницы: тот же знак и то же «дыхание», что у экрана входа, чтобы окно не меняло вид.

Тема, цвет фона и признак стекла запоминаются в startup.json при каждом запуске: окно сразу открывается
с правильными скруглением, стеклом и фоном, а не перестраивается после загрузки интерфейса.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

DEFAULT = {"theme": "dark", "glass": False, "color": [0.078, 0.078, 0.078]}
MARK = 64


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


def save(theme: str, glass: bool, color) -> None:
    try:
        rgb = [round(float(v), 4) for v in color][:3]
        if len(rgb) == 3 and theme in ("dark", "light"):
            _file().write_text(json.dumps({"theme": theme, "glass": bool(glass), "color": rgb}), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass


def mark_path() -> Path:
    return Path(__file__).resolve().parent / "web-next" / "assets" / "mark.png"


class Splash(QWidget):
    """Знак по центру, непрозрачность 0.5-1 за 1.4 с (auth-breathe). Со стеклом фон рисует подложка окна."""

    def __init__(self, parent: QWidget, look: dict):
        super().__init__(parent)
        self.look = look
        self.pixmap = QPixmap(str(mark_path())).scaledToWidth(MARK * 2, Qt.TransformationMode.SmoothTransformation)
        if look["theme"] == "light":
            # знак белый: на светлом фоне переворачиваем цвет, прозрачность остаётся
            image = self.pixmap.toImage()
            image.invertPixels(QImage.InvertMode.InvertRgb)
            self.pixmap = QPixmap.fromImage(image)
        self.pixmap.setDevicePixelRatio(2.0)
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

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        if not self.look["glass"]:
            r, g, b = (round(v * 255) for v in self.look["color"])
            painter.fillRect(self.rect(), QColor(r, g, b))
        painter.setOpacity(self.level)
        painter.drawPixmap((self.width() - MARK) // 2, (self.height() - MARK) // 2, self.pixmap)
        if self.message:
            painter.setPen(QColor("#aaaaaa") if self.look["theme"] == "dark" else QColor("#555555"))
            area = self.rect().adjusted(24, (self.height() + MARK) // 2 + 16, -24, 0)
            painter.drawText(area, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.message)
