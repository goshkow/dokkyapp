"""Рисует макет DMG в SVG и PNG 1x/2x; использует установленный в проекте PySide6."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QRawFont,
)
from PySide6.QtSvg import QSvgGenerator


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets" / "dmg"
WIDTH, HEIGHT = 800, 560
INK = QColor("#202327")
MUTED = QColor("#6c7381")
BLUE = QColor("#438dff")


def font(family: str, size: float, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    result = QFont(family)
    result.setPixelSize(round(size))
    result.setWeight(weight)
    result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return result


def text(
    painter: QPainter,
    family: str,
    value: str,
    size: float,
    x: float,
    baseline: float,
    *,
    color: QColor = INK,
    centered: bool = False,
    weight: QFont.Weight = QFont.Weight.Medium,
) -> None:
    face = font(family, size, weight)
    metrics = QFontMetricsF(face)
    if centered:
        x -= metrics.horizontalAdvance(value) / 2
    if x < 20 or x + metrics.horizontalAdvance(value) > WIDTH - 20:
        raise ValueError(f"Текст выходит за безопасное поле: {value}")
    path = QPainterPath()
    # контуры сохраняют шрифт и кернинг без установленного Unbounded у клиента
    path.addText(QPointF(x, baseline), face, value)
    path.setFillRule(Qt.FillRule.WindingFill)
    painter.fillPath(path, color)


def paint(painter: QPainter, family: str) -> None:
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
    )
    base = QLinearGradient(0, 0, WIDTH, HEIGHT)
    base.setColorAt(0, QColor("#fbfbf8"))
    base.setColorAt(0.56, QColor("#f6f8fc"))
    base.setColorAt(1, QColor("#eaf1ff"))
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), base)

    glow = QRadialGradient(QPointF(765, 535), 460)
    glow.setColorAt(0, QColor(164, 191, 250, 105))
    glow.setColorAt(0.68, QColor(200, 213, 249, 35))
    glow.setColorAt(1, QColor(225, 233, 252, 0))
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), glow)

    # линии продолжаются за краем, чтобы фон оставался спокойным и не спорил со значками
    painter.save()
    painter.setClipRect(QRectF(0, 0, WIDTH, HEIGHT))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for radius, alpha in ((190, 48), (236, 35), (282, 26), (328, 19)):
        painter.setPen(QPen(QColor(108, 150, 225, alpha), 0.8))
        painter.drawEllipse(QPointF(818, 591), radius, radius)
    painter.restore()

    logo = QImage(str(ROOT / "assets" / "dokkyapp-512.png"))
    if logo.isNull():
        raise ValueError("Не найден существующий значок Докки")
    painter.drawImage(QRectF(48, 37, 34, 34), logo)
    text(painter, family, "Докки", 23, 93, 63, weight=QFont.Weight.DemiBold)
    text(painter, family, "ДЛЯ macOS", 10, 643, 58, color=MUTED)

    text(
        painter, family, "Перетащите. И готово.", 33, 400, 152,
        centered=True, weight=QFont.Weight.DemiBold,
    )
    text(
        painter, family, "Ваш Докки - в папке «Программы»", 12, 400, 186,
        centered=True, color=MUTED,
    )

    for x in (220, 580):
        halo = QRadialGradient(QPointF(x, 315), 99)
        halo.setColorAt(0, QColor(255, 255, 255, 240))
        halo.setColorAt(0.80, QColor(255, 255, 255, 170))
        halo.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(x, 315), 99, 99)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(178, 191, 212, 60), 0.9))
        painter.drawEllipse(QPointF(x, 315), 90, 90)

    # один непрерывный жест направляет к настоящей папке Finder
    arrow = QPainterPath(QPointF(350, 330))
    arrow.cubicTo(QPointF(375, 305), QPointF(409, 306), QPointF(443, 327))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(BLUE, 4.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPath(arrow)
    head = QPainterPath(QPointF(430, 307))
    head.cubicTo(QPointF(436, 314), QPointF(441, 323), QPointF(444, 329))
    head.cubicTo(QPointF(435, 331), QPointF(425, 334), QPointF(415, 335))
    painter.drawPath(head)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(67, 141, 255, 85))
    painter.drawEllipse(QPointF(334, 342), 2.1, 2.1)
    painter.setBrush(QColor(67, 141, 255, 42))
    painter.drawEllipse(QPointF(323, 350), 1.5, 1.5)

    text(painter, family, "01", 9, 220, 232, centered=True, color=MUTED)
    text(painter, family, "02", 9, 580, 232, centered=True, color=MUTED)
    painter.setPen(QPen(QColor(186, 197, 217, 100), 0.8))
    painter.drawLine(QPointF(72, 440), QPointF(728, 440))
    text(
        painter, family, "После копирования откройте Докки из «Программ».", 11, 400, 480,
        centered=True, color=MUTED,
    )


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication([])
    font_id = QFontDatabase.addApplicationFontFromData(
        QByteArray((ART / "fonts" / "Unbounded.ttf").read_bytes())
    )
    families = QFontDatabase.applicationFontFamilies(font_id)
    if font_id < 0 or not families:
        raise RuntimeError("Не удалось загрузить Unbounded")
    family = families[0]
    raw_font = QRawFont.fromFont(font(family, 16))
    for char in "ДоккиПеретащитеговоВашпкПргмыЗмоьлжнДЛЯmacOS012«».-":
        if not all(raw_font.glyphIndexesForString(char)):
            raise ValueError(f"В Unbounded нет символа {char!r}")

    svg = QSvgGenerator()
    svg.setFileName(str(ART / "background.svg"))
    svg.setSize(QSize(WIDTH, HEIGHT))
    svg.setViewBox(QRectF(0, 0, WIDTH, HEIGHT))
    svg.setTitle("Докки - установка на macOS")
    svg.setDescription("Фон нарисован вручную; Unbounded в контурах, существующий знак Докки.")
    painter = QPainter(svg)
    paint(painter, family)
    painter.end()
    for scale in (1, 2):
        image = QImage(WIDTH * scale, HEIGHT * scale, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDotsPerMeterX(round(72 * scale / 0.0254))
        image.setDotsPerMeterY(round(72 * scale / 0.0254))
        painter = QPainter(image)
        painter.scale(scale, scale)
        paint(painter, family)
        painter.end()
        name = "background.png" if scale == 1 else "background@2x.png"
        if not image.save(str(ART / name)):
            raise RuntimeError(f"Не удалось сохранить {name}")
    layout = {
        "window": [WIDTH, HEIGHT],
        "icon_size": 112,
        "icon_locations": {"Докки.app": [220, 315], "Программы": [580, 315]},
        "font": family,
    }
    (ART / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n")
    print(f"Готово: {ART}, шрифт {family}, PNG 1x/2x и SVG")
    app.quit()


if __name__ == "__main__":
    main()
