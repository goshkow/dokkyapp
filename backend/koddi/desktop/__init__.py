"""Настольная оболочка Докки на Python."""

from __future__ import annotations

import os
from pathlib import Path

_SOFTWARE = {"0", "false", "no", "off", "software"}


def webengine_cache_path(storage: Path, version: str) -> Path:
    """У каждой версии свой HTTP-кэш; хранилище пользователя остаётся общим."""
    if not version or any(char not in "0123456789.-abcdefghijklmnopqrstuvwxyz" for char in version):
        raise ValueError("Некорректная версия приложения")
    return storage / "cache-versions" / version


def render_mode_file() -> Path:
    from koddi.config import data_dir

    return data_dir() / "render-mode"


def saved_render_mode() -> str:
    """Выбор пользователя из настроек: ``gpu`` (по умолчанию, аппаратное ускорение) или ``software``.

    Хранится в отдельном файле: окно читает его до запуска WebEngine, когда база настроек ещё не открыта.
    """
    try:
        value = render_mode_file().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "gpu"
    return "software" if value == "software" else "gpu"


def save_render_mode(mode: str) -> None:
    if mode not in {"gpu", "software"}:
        raise ValueError("Недопустимый режим отрисовки")
    render_mode_file().write_text(mode, encoding="utf-8")


def software_rendering() -> bool:
    """Переменная окружения сильнее настройки: ею пользуются при диагностике."""
    forced = os.getenv("KODDI_WEBENGINE_GPU", "").strip().lower()
    if forced:
        return forced in _SOFTWARE
    return saved_render_mode() == "software"


def _add_flags(*wanted: str) -> None:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    for flag in wanted:
        if flag not in flags:
            flags.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)


def _has_user_renderer_flags() -> bool:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    return any(flag.startswith("--use-angle") or flag.startswith("--use-gl") for flag in flags)


def configure_webengine_rendering() -> None:
    """Режимы отрисовки окна (Windows). Измерено на реальной сборке и реальных чатах:

    * ``gpu`` (по умолчанию): Chromium считает кадры через ANGLE на Direct3D 11 с композитингом на видеокарте,
      Qt Quick показывает готовую текстуру через OpenGL. Кадр 16,7 мс, процессор ~0,5 с на сценарий прокрутки.
    * ``software``: тот же конвейер, но на WARP (программный Direct3D 11 от Microsoft, многопоточный) и Qt Quick,
      и Chromium. Видеокарта и драйвер NVIDIA не загружаются вообще, кадр тоже 16,7 мс (процессор ~2 ядра).
      Раньше здесь был композитинг Chromium на процессоре в одном потоке: 50-60 мс на кадр и лаги в любом чате.

    Диагностика через ``KODDI_RENDERER``: ``d3d11`` (Qt Quick тоже на Direct3D 11), ``opengl`` (ANGLE-GL: на этой
    связке окно бывало пустым), ``safe`` (всё на процессоре без Direct3D и OpenGL, медленно, на случай без D3D11).
    Флаги пользователя в ``QTWEBENGINE_CHROMIUM_FLAGS`` сильнее наших.
    """
    renderer = os.getenv("KODDI_RENDERER", "").strip().lower()
    software = software_rendering()
    if renderer == "safe" or (os.name != "nt" and software):
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        _add_flags("--disable-gpu", "--disable-gpu-compositing", "--disable-features=Vulkan")
        return
    if os.name != "nt":
        return
    if software:
        os.environ.setdefault("QSG_RHI_BACKEND", "d3d11")
        os.environ.setdefault("QSG_RHI_PREFER_SOFTWARE_RENDERER", "1")
        angle = ("--use-angle=d3d11-warp",)
    elif renderer == "d3d11":
        angle = ("--use-angle=d3d11",)
    elif renderer == "opengl":
        os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
        angle = ("--use-gl=angle", "--use-angle=gl")
    else:
        os.environ.setdefault("QSG_RHI_BACKEND", "opengl")
        angle = ("--use-angle=d3d11",)
    if not _has_user_renderer_flags():
        _add_flags(*angle, "--ignore-gpu-blocklist")


configure_webengine_rendering()
