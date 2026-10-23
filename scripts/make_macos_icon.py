"""Собирает слои значка Liquid Glass (assets/DokkyApp.icon) из assets/dokkyapp-512.png.

Мишка делится на два слоя: белое тело (получает стекло) и тёмные черты лица поверх. Фон задаёт градиент
в icon.json. Результат открывается в Icon Composer и компилируется `actool` в scripts/build_macos.sh.
Нужен только Pillow.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "dokkyapp-512.png"
ICON = ROOT / "assets" / "DokkyApp.icon"
CANVAS = 1024
BEAR_WIDTH = 640  # доля холста, оставляющая поля под скругление и стекло
FACE = (22, 22, 26)


def clamp_ramp(low: int, high: int):
    return lambda value: max(0, min(255, round((value - low) * 255 / (high - low))))


def layers() -> tuple[Image.Image, Image.Image]:
    gray = Image.open(SOURCE).convert("L").resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    # внутри рамки значка: края со светлой окантовкой скругления в разбор не попадают
    inner = Image.new("L", gray.size, 0)
    ImageDraw.Draw(inner).rectangle((120, 120, CANVAS - 120, CANVAS - 120), fill=255)
    white = ImageChops.multiply(gray.point(clamp_ramp(110, 150)), inner)
    solid = white.point(lambda v: 255 if v > 127 else 0)
    background = ImageOps.invert(solid)
    ImageDraw.floodfill(background, (0, 0), 128)
    # заливка дыр: глаза, нос и улыбка становятся частью силуэта
    filled = background.point(lambda v: 0 if v == 128 else 255)
    interior = filled.filter(ImageFilter.MinFilter(7))
    body_alpha = ImageChops.lighter(white, interior)
    face_alpha = ImageChops.multiply(interior, ImageOps.invert(white))
    box = body_alpha.getbbox()
    if box is None:
        raise RuntimeError("Мишка не найден в исходном значке")
    scale = BEAR_WIDTH / (box[2] - box[0])
    size = (round((box[2] - box[0]) * scale), round((box[3] - box[1]) * scale))
    origin = ((CANVAS - size[0]) // 2, (CANVAS - size[1]) // 2)
    result = []
    for alpha, color in ((body_alpha, (255, 255, 255)), (face_alpha, FACE)):
        cropped = alpha.crop(box).resize(size, Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", (CANVAS, CANVAS), (*color, 0))
        layer.putalpha(Image.new("L", (CANVAS, CANVAS), 0))
        layer.paste(Image.new("RGBA", size, (*color, 255)), origin, cropped)
        result.append(layer)
    return result[0], result[1]


def main() -> None:
    body, face = layers()
    (ICON / "Assets").mkdir(parents=True, exist_ok=True)
    body.save(ICON / "Assets" / "bear.png")
    face.save(ICON / "Assets" / "face.png")
    document = {
        "fill": {
            "linear-gradient": [
                "display-p3:0.20000,0.20000,0.22000,1.00000",
                "display-p3:0.03000,0.03000,0.04000,1.00000",
            ],
            "orientation": {"start": {"x": 0.5, "y": 0}, "stop": {"x": 0.5, "y": 1}},
        },
        "groups": [
            {
                "layers": [
                    {
                        "glass": False,
                        "hidden": False,
                        "image-name": "face.png",
                        "name": "face",
                        "position": {"scale": 1, "translation-in-points": [0, 0]},
                    },
                    {
                        "glass": True,
                        "hidden": False,
                        "image-name": "bear.png",
                        "name": "bear",
                        "position": {"scale": 1, "translation-in-points": [0, 0]},
                    },
                ],
                "lighting": "individual",
                "shadow": {"kind": "neutral", "opacity": 0.5},
                "translucency": {"enabled": True, "value": 0.35},
            }
        ],
        "supported-platforms": {"squares": "shared"},
    }
    (ICON / "icon.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Готово: {ICON}")


if __name__ == "__main__":
    main()
