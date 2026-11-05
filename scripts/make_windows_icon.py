"""Собирает значок Windows (assets/DokkyApp.ico) из слоёв значка macOS (assets/DokkyApp.icon/Assets).

Тот же мишка и тот же тёмный градиент, что у значка macOS, но плоско, без стекла: скруглённый квадрат на всё
поле значка (как у Claude и ChatGPT), мишка занимает чуть больше половины ширины. В мелких размерах мишка крупнее,
иначе на панели задач он теряется. Нужен только Pillow.

Запуск: python scripts/make_windows_icon.py [папка для превью PNG]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "assets" / "DokkyApp.icon" / "Assets"
OUTPUT = ROOT / "assets" / "DokkyApp.ico"
WORK = 1024
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
TOP, BOTTOM = (54, 54, 60), (10, 10, 12)


def square(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * radius), fill=255)
    return mask


def gradient(size: int) -> Image.Image:
    column = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return column.resize((size, size))


def bear(width_share: float) -> Image.Image:
    """Мишка (белое тело и тёмные черты лица) по центру холста WORK, шириной width_share от холста."""
    body = Image.open(LAYERS / "bear.png").convert("RGBA")
    face = Image.open(LAYERS / "face.png").convert("RGBA")
    merged = Image.alpha_composite(body, face)
    box = body.getbbox()
    if box is None:
        raise RuntimeError("Слой мишки пуст")
    cropped = merged.crop(box)
    scale = WORK * width_share / cropped.width
    cropped = cropped.resize((round(cropped.width * scale), round(cropped.height * scale)), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (WORK, WORK), (0, 0, 0, 0))
    # чуть ниже геометрического центра: уши делают мишку визуально выше
    layer.alpha_composite(cropped, ((WORK - cropped.width) // 2, (WORK - cropped.height) // 2 + round(WORK * 0.015)))
    return layer


def master(width_share: float, radius: float) -> Image.Image:
    mask = square(WORK, radius)
    tile = Image.new("RGBA", (WORK, WORK), (0, 0, 0, 0))
    tile.paste(gradient(WORK), (0, 0), mask)
    # тонкий светлый край сверху вместо стекла: квадрат не сливается с тёмной панелью задач
    edge = Image.new("L", (WORK, WORK), 0)
    ImageDraw.Draw(edge).rounded_rectangle((0, 0, WORK - 1, WORK - 1), radius=round(WORK * radius), outline=255, width=6)
    fade = Image.linear_gradient("L").resize((WORK, WORK)).point(lambda v: max(0, 255 - v * 2))
    edge = ImageChops.multiply(edge, fade)
    tile.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (WORK, WORK), 255)] * 3, edge.point(lambda v: v * 40 // 255))))
    figure = bear(width_share)
    # мягкая тень под мишкой, чтобы он не выглядел наклейкой
    shadow = figure.split()[3].filter(ImageFilter.GaussianBlur(WORK * 0.02)).point(lambda v: v * 70 // 255)
    shadow_layer = Image.new("RGBA", (WORK, WORK), (0, 0, 0, 0))
    shadow_layer.putalpha(ImageChops.offset(shadow, 0, round(WORK * 0.012)))
    tile.alpha_composite(shadow_layer)
    tile.alpha_composite(figure)
    tile.putalpha(ImageChops.multiply(tile.split()[3], mask))
    return tile


def frames() -> dict[int, Image.Image]:
    large = master(0.56, 0.225)
    # 16-32 точки: мишка крупнее и углы чуть меньше, иначе в трее и на панели задач он мелкий
    small = master(0.68, 0.2)
    result = {}
    for size in SIZES:
        source = small if size <= 32 else large
        result[size] = source.resize((size, size), Image.Resampling.LANCZOS)
    return result


def main() -> None:
    images = frames()
    biggest = images[max(SIZES)]
    biggest.save(OUTPUT, format="ICO", sizes=[(size, size) for size in SIZES],
                 append_images=[images[size] for size in SIZES if size != max(SIZES)])
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
        out.mkdir(parents=True, exist_ok=True)
        for size, image in images.items():
            image.save(out / f"icon-{size}.png")
    print(f"{OUTPUT.relative_to(ROOT)}: {', '.join(map(str, SIZES))}")


if __name__ == "__main__":
    main()
