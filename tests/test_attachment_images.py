"""Что модель получает вместо картинки: иконка, поворот, 16 бит, форматы."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from koddi.attachments import recognize
from koddi.attachments.service import ALLOWED_EXTENSIONS, unsupported_text


def converted(monkeypatch, make) -> Image.Image:
    buffer = io.BytesIO()
    make(buffer)
    monkeypatch.setattr(recognize, "_read_attachment_bytes", lambda _item, _limit: buffer.getvalue())
    url = recognize.image_url({"original_name": "image"})
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("L")


def square(color: tuple[int, int, int, int], side: int = 64) -> Image.Image:
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    image.paste(color, (side // 4, side // 4, side * 3 // 4, side * 3 // 4))
    return image


@pytest.mark.parametrize("color", [(255, 255, 255, 255), (5, 5, 5, 255)])
def test_icon_on_transparent_background_stays_visible(monkeypatch, color) -> None:
    low, high = converted(monkeypatch, lambda out: square(color).save(out, "PNG")).getextrema()
    assert high - low > 150


def test_ico_is_read_and_tiny_icon_is_enlarged(monkeypatch) -> None:
    image = converted(monkeypatch, lambda out: square((255, 255, 255, 255)).save(out, "ICO"))
    assert max(image.size) >= recognize.MIN_IMAGE_SIDE


def test_phone_photo_is_turned_upright(monkeypatch) -> None:
    def photo(out) -> None:
        image = Image.new("RGB", (400, 200))
        exif = image.getexif()
        exif[0x0112] = 6
        image.save(out, "JPEG", exif=exif)

    width, height = converted(monkeypatch, photo).size
    assert height > width


def test_sixteen_bit_image_keeps_its_contrast(monkeypatch) -> None:
    def scan(out) -> None:
        Image.frombytes("I;16", (300, 1), bytes(range(0, 200)) * 3).save(out, "PNG")

    low, high = converted(monkeypatch, scan).getextrema()
    assert high - low > 150


def test_rejected_formats_are_explained_in_russian() -> None:
    assert ".exe" in unsupported_text(".exe")
    assert "без расширения" in unsupported_text("")
    assert {".heic", ".pdf", ".ico"} <= ALLOWED_EXTENSIONS


def test_heic_from_iphone_is_read(monkeypatch, tmp_path) -> None:
    import shutil
    import subprocess

    if not shutil.which("sips"):
        pytest.skip("HEIC в тесте собирает sips на macOS")
    source, target = tmp_path / "in.png", tmp_path / "out.heic"
    Image.new("RGB", (400, 200), (200, 10, 10)).save(source)
    subprocess.run(["sips", "-s", "format", "heic", str(source), "--out", str(target)], check=True, capture_output=True)
    monkeypatch.setattr(recognize, "_read_attachment_bytes", lambda _item, _limit: target.read_bytes())
    url = recognize.image_url({"original_name": "photo.HEIC"})
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert image.size == (400, 200)
