"""Запуск Докки при входе: значение в HKCU\\...\\Run на Windows, LaunchAgent на macOS; без прав администратора."""

from __future__ import annotations

import os
import plistlib
import sys
from collections.abc import Callable
from pathlib import Path

from koddi.config import launcher_path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "DokkyApp"
LAUNCH_LABEL = "com.goshkow.dokky"


def _executable() -> Path | None:
    """Путь к собранной программе; из исходников автозапуск не включается."""
    launcher = launcher_path()
    if launcher is not None:
        return launcher
    candidate = Path(sys.argv[0] if sys.argv and sys.argv[0] else sys.executable)
    if candidate.suffix.lower() != ".exe" or candidate.stem.lower().startswith(("python", "pythonw")):
        return None
    return candidate.resolve() if candidate.exists() else None


def _mac_bundle() -> Path | None:
    """Папка Докки.app, из которой запущена программа; из исходников её нет."""
    for start in (sys.executable, sys.argv[0] if sys.argv else ""):
        if not start:
            continue
        for parent in Path(start).resolve().parents:
            if parent.suffix == ".app" and parent.is_dir():
                return parent
    return None


def _launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"


def available() -> bool:
    if sys.platform == "darwin":
        return _mac_bundle() is not None
    return os.name == "nt" and _executable() is not None


def enabled() -> bool:
    if sys.platform == "darwin":
        bundle = _mac_bundle()
        try:
            agent = plistlib.loads(_launch_agent().read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            return False
        return bool(bundle) and str(bundle) in agent.get("ProgramArguments", [])
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE)
    except OSError:
        return False
    target = _executable()
    return bool(target) and str(target).lower() in str(value).lower()


def repoint(bundle: Path, stale: Callable[[Path], bool]) -> bool:
    """Автозапуск, который ведёт на удалённую или старую копию Докки, переводится на эту копию."""
    path = _launch_agent()
    try:
        agent = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    arguments = agent.get("ProgramArguments") or []
    target = Path(str(arguments[-1])) if arguments else None
    if target is None or target == bundle or (target.exists() and not stale(target)):
        return False
    agent["ProgramArguments"] = ["/usr/bin/open", "-a", str(bundle)]
    path.write_bytes(plistlib.dumps(agent))
    return True


def set_enabled(value: bool) -> bool:
    if sys.platform == "darwin":
        path = _launch_agent()
        if not value:
            path.unlink(missing_ok=True)
            return False
        bundle = _mac_bundle()
        if bundle is None:
            raise ValueError("Автозапуск доступен только у установленной программы")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({
            "Label": LAUNCH_LABEL,
            "ProgramArguments": ["/usr/bin/open", "-a", str(bundle)],
            "RunAtLoad": True,
        }))
        return True
    if os.name != "nt":
        return False
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if not value:
            try:
                winreg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass
            return False
        target = _executable()
        if target is None:
            raise ValueError("Автозапуск доступен только у установленной программы")
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, f'"{target}"')
        return True
