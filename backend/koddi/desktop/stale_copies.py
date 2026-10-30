"""Старые копии Докки на macOS: снять с регистрации в LaunchServices, чтобы запуск и ссылки koddi:// шли в эту копию.

Все копии (папки сборки, забытые образы, прошлые версии) делят один bundle id и схему koddi:, и система
отдаёт ссылку входа или запуск любой из них. Каждая собранная версия при старте убирает из регистрации
копии, которые старше её, и копии той же версии вне «Программ». Файлы копий не трогаются.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path

from koddi import __version__
from koddi.config import is_packaged_build
from koddi.updates.service import is_newer

LOGGER = logging.getLogger("koddi.desktop")
BUNDLE_ID = "com.goshkow.dokky"
LSREGISTER = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
TIMEOUT = 30


def _path(value: str) -> Path:
    # NSURL отдаёт имена в NFD, sys.executable - как записано на диске
    return Path(unicodedata.normalize("NFC", str(Path(value).resolve())))


def own_bundle() -> Path | None:
    for parent in _path(sys.executable).parents:
        if parent.suffix == ".app":
            return parent
    return None


def bundle_version(bundle: Path) -> str | None:
    try:
        info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    version = info.get("CFBundleShortVersionString")
    return version if isinstance(version, str) else None


def _installed(bundle: Path) -> bool:
    return bundle.parent in (Path("/Applications"), Path.home() / "Applications")


def _temporary(bundle: Path) -> bool:
    """Образ диска или карантинная копия Gatekeeper: эта копия не останется на компьютере."""
    text = str(bundle)
    return text.startswith("/Volumes/") or "/AppTranslocation/" in text


def is_stale(copy: Path, version: str | None, own: Path, own_version: str) -> bool:
    """Копия мешает этой: старше её, без читаемой версии или та же версия не в «Программах»."""
    if copy == own:
        return False
    if version is None or is_newer(own_version, version):
        return True
    return version == own_version and not _installed(copy)


def registered_copies() -> list[Path]:
    from AppKit import NSWorkspace  # type: ignore[import-not-found]

    urls = NSWorkspace.sharedWorkspace().URLsForApplicationsWithBundleIdentifier_(BUNDLE_ID) or []
    return [_path(str(url.path())) for url in urls if url.path()]


def _lsregister(*args: str) -> bool:
    try:
        return subprocess.run([str(LSREGISTER), *args], capture_output=True, timeout=TIMEOUT, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def clean_up() -> list[Path]:
    """Снять с регистрации мешающие копии и зарегистрировать эту. Возвращает снятые копии."""
    own = own_bundle()
    # копия из образа или карантина сама временная: чистить будет установленная
    if own is None or _temporary(own) or not LSREGISTER.exists():
        return []
    removed = []
    for copy in registered_copies():
        if is_stale(copy, bundle_version(copy), own, __version__) and _lsregister("-u", str(copy)):
            removed.append(copy)
    if removed:
        _lsregister("-f", str(own))
        LOGGER.info("сняты с регистрации старые копии Докки: %s", ", ".join(map(str, removed)))
    from koddi.desktop import autostart

    autostart.repoint(own, lambda bundle: is_stale(bundle, bundle_version(bundle), own, __version__))
    return removed


def clean_up_later() -> None:
    """В фоне: lsregister может работать несколько секунд, окно ждать не должно."""
    if sys.platform != "darwin" or not is_packaged_build():
        return

    def run() -> None:
        try:
            clean_up()
        except Exception:  # noqa: BLE001 - очистка необязательна, старт важнее
            LOGGER.warning("не удалось проверить старые копии Докки", exc_info=True)

    threading.Thread(target=run, name="stale-copies", daemon=True).start()
