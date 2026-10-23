"""Копирует установленный Chromium в каталог релиза и проверяет его."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _required_directories(package: Path) -> set[str]:
    try:
        data = json.loads((package / "driver" / "package" / "browsers.json").read_text(encoding="utf-8"))
        return {
            f"{item['name'].replace('-', '_')}-{item['revision']}"
            for item in data["browsers"]
            if item["name"] in {"chromium-headless-shell", "ffmpeg", "winldd"}
        }
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def main():
    parser = argparse.ArgumentParser(description="Включить браузер агента в сборку")
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    release = args.release.resolve()
    built = (release / "DokkyApp.exe").is_file() or (release / "DokkyApp").is_file()
    if not release.is_relative_to(root) or release == root or not built:
        parser.error("Укажите папку готовой программы внутри проекта")
    import playwright

    package_cache = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if sys.platform == "win32":
        default_cache = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
    elif sys.platform == "darwin":
        default_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        default_cache = Path.home() / ".cache" / "ms-playwright"
    source = (
        package_cache
        if package_cache.is_dir()
        else Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or default_cache)
    )
    # в общем кэше Playwright копятся старые версии и полный Chromium (около гигабайта): берём только то,
    # что нужно установленному Playwright, агент запускает лишь headless-оболочку
    wanted = _required_directories(Path(playwright.__file__).parent)
    include = [
        path
        for path in source.iterdir()
        if path.is_dir()
        and (path.name in wanted if wanted else path.name.startswith(
            ("chromium-", "chromium_headless_shell-", "ffmpeg-", "winldd-")))
    ]
    if not any(path.name.startswith("chromium_headless_shell-") for path in include):
        raise RuntimeError("Не найден установленный headless Chromium для текущего Playwright")
    target = release / "playwright" / "driver" / "package" / ".local-browsers"
    for path in include:
        shutil.copytree(
            path,
            target / path.name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.log", "__pycache__", "*.pyc"),
        )
        print(f"Браузер включён: {path.name}")


if __name__ == "__main__":
    main()
