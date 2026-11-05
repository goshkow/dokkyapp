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


def prune_webengine_caches(storage: Path, keep: Path) -> None:
    """Кэши прежних версий только занимают место: после обновления их уже никто не читает."""
    import shutil

    targets = [storage / "cache"]
    versions = storage / "cache-versions"
    if versions.is_dir():
        targets += [item for item in versions.iterdir() if item != keep]
    for item in targets:
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, ignore_errors=True)


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
    # явный выбор пользователя сильнее автоматического отката после падения
    _forget(auto_mode_file())


# порядок отката: видеокарта, программный Direct3D (WARP), всё на процессоре без Direct3D
_FALLBACK = {"gpu": "software", "software": "safe"}


def auto_mode_file() -> Path:
    from koddi.config import data_dir

    return data_dir() / "render-auto"


def startup_mark_file() -> Path:
    from koddi.config import data_dir

    return data_dir() / "render-starting"


def _forget(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def auto_render_mode() -> str:
    """Режим, на который Докки откатилась сама: ``software`` или ``safe``, пусто - отката не было.

    Откат помнится только для своей версии: после обновления (часто с новым Qt) видеокарту пробуем снова.
    """
    from koddi import __version__

    try:
        value, _, version = auto_mode_file().read_text(encoding="utf-8").strip().lower().partition(" ")
    except OSError:
        return ""
    return value if value in {"software", "safe"} and version == __version__ else ""


def _remember_auto(mode: str) -> None:
    from koddi import __version__

    auto_mode_file().write_text(f"{mode} {__version__}", encoding="utf-8")


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    import ctypes

    # os.kill на Windows завершает процесс, поэтому только спрашиваем код выхода
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong(0)
        return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def _read_mark() -> tuple[str, int]:
    try:
        mode, _, pid = startup_mark_file().read_text(encoding="utf-8").strip().lower().partition(" ")
    except OSError:
        return "", 0
    return mode, int(pid) if pid.isdigit() else 0


def d3d11_hardware_ok() -> bool:
    """Видеокарта умеет Direct3D 11 (уровень 11_0). Иначе Qt падает в qFatal ещё до первого кадра.

    На ноутбуках с Intel HD 3000 и подобными (уровень 10_1) создание устройства даёт DXGI_ERROR_UNSUPPORTED,
    а перехватить это внутри процесса нельзя: Qt закрывает программу сам. Если проверку сделать не удалось,
    считаем, что всё в порядке, и полагаемся на откат после падения.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        d3d11 = ctypes.WinDLL("d3d11")
        levels = (ctypes.c_uint * 1)(0xB000)  # D3D_FEATURE_LEVEL_11_0
        got = ctypes.c_uint(0)
        # без ppDevice функция только проверяет, создастся ли устройство
        result = d3d11.D3D11CreateDevice(None, 1, None, 0, levels, 1, 7, None, ctypes.byref(got), None)
        return ctypes.c_long(result).value >= 0
    except Exception:
        return True


def _launch_mode(chosen: str) -> str:
    """Режим этого запуска с учётом прошлых падений. Отметка снимается, когда окно загрузилось (``startup_succeeded``)."""
    mode = auto_render_mode() or chosen
    crashed, pid = _read_mark()
    # отметка живого процесса - не падение: это первый экземпляр ещё грузится, а мы второй запуск
    if pid and pid != os.getpid() and _process_alive(pid):
        return mode
    # прошлый запуск в этом режиме не дошёл до окна: следующий режим и запоминаем его
    if crashed == mode and mode in _FALLBACK:
        mode = _FALLBACK[mode]
        _remember_auto(mode)
    elif mode == "gpu" and not d3d11_hardware_ok():
        mode = "software"
        _remember_auto(mode)
    try:
        startup_mark_file().write_text(f"{mode} {os.getpid()}", encoding="utf-8")
    except OSError:
        pass
    return mode


def startup_succeeded() -> None:
    """Окно загрузилось или процесс вышел сам: режим отрисовки не виноват. Чужую отметку не трогаем."""
    _mode, pid = _read_mark()
    if pid in (0, os.getpid()):
        _forget(startup_mark_file())


def software_rendering() -> bool:
    """Переменная окружения сильнее настройки: ею пользуются при диагностике."""
    forced = os.getenv("KODDI_WEBENGINE_GPU", "").strip().lower()
    if forced:
        return forced in _SOFTWARE
    return saved_render_mode() == "software" or (os.name == "nt" and auto_render_mode() == "software")


def _add_flags(*wanted: str) -> None:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    for flag in wanted:
        if flag not in flags:
            flags.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)


def _has_user_renderer_flags() -> bool:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    return any(flag.startswith("--use-angle") or flag.startswith("--use-gl") for flag in flags)


def configure_webengine_rendering(mode: str = "") -> None:
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
    if mode == "safe":
        renderer = "safe"
    elif mode == "software":
        software = True
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



_RENDER_KEYS = ("QTWEBENGINE_CHROMIUM_FLAGS", "QSG_RHI_BACKEND", "QSG_RHI_PREFER_SOFTWARE_RENDERER", "QT_QUICK_BACKEND")
# окружение до наших правок: откат при запуске окна настраивает режим заново поверх него
_ENV_BEFORE = {key: os.environ.get(key) for key in _RENDER_KEYS}


def prepare_window_rendering() -> str:
    """Только для процесса окна, до QApplication: проверка Direct3D 11 и откат после падения прошлого запуска.

    Ручная диагностика (``KODDI_RENDERER``, ``KODDI_WEBENGINE_GPU``) обходит откат.
    """
    if os.name != "nt" or os.getenv("KODDI_RENDERER") or os.getenv("KODDI_WEBENGINE_GPU"):
        return ""
    chosen = "software" if saved_render_mode() == "software" else "gpu"
    try:
        mode = _launch_mode(chosen)
    except OSError:
        return ""
    for key, value in _ENV_BEFORE.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    configure_webengine_rendering(mode)
    return mode


configure_webengine_rendering()
