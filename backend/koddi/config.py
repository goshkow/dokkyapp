from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "Koddi"
# раскладка Windows для автообновления: Dokky.exe и current.json над папкой версии app-X.Y.Z
LAUNCHER_NAME = "Dokky.exe"
VERSION_DIR = re.compile(r"app-\d+\.\d+\.\d+(-beta\.\d+)?")


def is_packaged_build() -> bool:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return True
    exe = Path(sys.executable).resolve()
    return exe.suffix.lower() == ".exe" and "python" not in exe.name.lower()


def app_dir() -> Path:
    if is_packaged_build():
        return Path(sys.executable).resolve().parent
    return project_root()


def launcher_path() -> Path | None:
    """Лаунчер установленной или переносной версии Windows; ярлыки, автозапуск и koddi:// ведут на него."""
    if os.name != "nt" or not is_packaged_build():
        return None
    version_dir = app_dir()
    root = version_dir.parent
    if not VERSION_DIR.fullmatch(version_dir.name):
        return None
    launcher = root / LAUNCHER_NAME
    if not launcher.is_file() or not (root / "current.json").is_file():
        return None
    return launcher


def project_root() -> Path:
    if is_packaged_build():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.getenv("KODDI_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    elif os.getenv("KODDI_PORTABLE") == "1":
        path = app_dir() / ".koddi-data"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_data_dirs() -> list[Path]:
    target = data_dir().resolve()
    candidates = [
        app_dir() / ".koddi-data",
        project_root() / ".koddi-data",
        Path.cwd() / ".koddi-data",
    ]
    result: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved == target or resolved in result or not resolved.is_dir():
            continue
        result.append(resolved)
    return result


def migrate_legacy_data() -> list[str]:
    """Переносит портативный профиль в постоянный, не затирая более свежие данные."""
    if os.getenv("KODDI_DATA_DIR") or os.getenv("KODDI_PORTABLE") == "1":
        return []
    target = data_dir().resolve()
    migrated: list[str] = []
    for source in legacy_data_dirs():
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            destination = target / item.relative_to(source)
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            migrated.append(str(destination))
    return migrated


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("KODDI_HOST", "127.0.0.1")
    port: int = int(os.getenv("KODDI_PORT", "8765"))
    default_model: str = os.getenv("KODDI_AI_MODEL", "")
    command_timeout_seconds: int = int(os.getenv("KODDI_COMMAND_TIMEOUT", "120"))
    max_file_read_bytes: int = int(os.getenv("KODDI_MAX_FILE_READ_BYTES", "262144"))
    context_budget: str = os.getenv("KODDI_CONTEXT_BUDGET", "maximum")


settings = Settings()
