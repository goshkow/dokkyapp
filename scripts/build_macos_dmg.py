"""Упаковывает готовый Докки.app в оформленный DMG без изменения исходной сборки."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets" / "dmg"


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} завершился с кодом {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def resolve_output(value: Path, bundle: Path) -> Path:
    output = value.absolute()
    if os.path.lexists(output):
        raise ValueError("Образ уже существует; выберите новый путь")
    # родитель может быть ссылкой или содержать ..; временный каталог тоже вне .app
    output = output.parent.resolve() / output.name
    if output.suffix != ".dmg" or output.is_relative_to(bundle.resolve()):
        raise ValueError("Укажите новый файл .dmg вне папки приложения")
    if os.path.lexists(output.with_suffix(".json")):
        raise ValueError("Манифест уже существует; выберите новый путь")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, help="Готовая папка Докки.app")
    parser.add_argument("--output", required=True, type=Path, help="Новый файл .dmg")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("dmgbuild работает только на macOS")
    if args.app.is_symlink():
        parser.error("Укажите саму папку приложения, а не ссылку")
    bundle = args.app.resolve()
    if bundle.suffix != ".app" or not (bundle / "Contents/Info.plist").is_file():
        parser.error("Не найдена готовая .app с Info.plist")
    try:
        output = resolve_output(args.output, bundle)
    except ValueError as error:
        parser.error(str(error))
    manifest_path = output.with_suffix(".json")
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    name = info.get("CFBundleExecutable", "")
    if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+", name):
        parser.error("Некорректное имя executable в Info.plist")
    executable = bundle / "Contents/MacOS" / name
    if not executable.is_file() or executable.is_symlink():
        parser.error("Не найден обычный файл executable")
    run("codesign", "--verify", "--deep", "--strict", str(bundle))
    executable_hash = digest(executable)
    architecture = run("lipo", "-archs", str(executable))
    signature = subprocess.run(
        ["codesign", "-dvv", str(bundle)], check=True, capture_output=True, text=True
    ).stderr
    layout = json.loads((ART / "layout.json").read_text())
    background = ART / "background.png"
    for path in (background, ART / "background@2x.png"):
        if not path.is_file():
            parser.error("Сначала выполните scripts/make_macos_dmg_artwork.py")

    def check_copy(mount_point: str, _options: dict) -> None:
        # dmgbuild копирует через ditto; отдельно проверяем результат до упаковки
        copied = Path(mount_point) / "Докки.app"
        run("codesign", "--verify", "--deep", "--strict", str(copied))
        if digest(copied / "Contents/MacOS" / name) != executable_hash:
            raise ValueError("Хеш executable после копирования отличается")
        if os.readlink(Path(mount_point) / "Программы") != "/Applications":
            raise ValueError("Неверная ссылка на Программы")

    settings = {
        "files": [(str(bundle), "Докки.app")],
        "symlinks": {"Программы": "/Applications"},
        "background": str(background),
        "window_rect": ((160, 160), tuple(layout["window"])),
        "default_view": "icon-view",
        "icon_size": layout["icon_size"],
        "text_size": 13,
        "icon_locations": {key: tuple(value) for key, value in layout["icon_locations"].items()},
        # FinderInfo на .app ломает строгую проверку подписи при копировании
        "show_status_bar": False,
        "show_tab_view": False,
        "show_toolbar": False,
        "show_sidebar": False,
        "show_pathbar": False,
        "show_item_info": False,
        "grid_spacing": 80,
        "format": "UDZO",
        "filesystem": "HFS+",
        "create_hook": check_copy,
    }
    icon = bundle / "Contents/Resources/DokkyApp.icns"
    if icon.is_file():
        settings["icon"] = str(icon)

    from dmgbuild import build_dmg

    def progress(event: dict) -> None:
        if event.get("type") == "operation::start":
            print("Упаковка:", event["operation"], flush=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dokky-dmg-", dir=output.parent) as tmp:
        temporary = Path(tmp) / "Докки.dmg"
        build_dmg(str(temporary), "Докки", settings=settings, callback=progress)
        run("hdiutil", "verify", str(temporary))
        manifest = {
            "file": output.name,
            "sha256": digest(temporary),
            "bytes": temporary.stat().st_size,
            "app_version": info.get("CFBundleShortVersionString"),
            "app_build": info.get("CFBundleVersion"),
            "architecture": architecture,
            "source_executable_sha256": executable_hash,
            "ad_hoc_signed": "Signature=adhoc" in signature,
            "notarization_performed": False,
            "dmgbuild_version": importlib.metadata.version("dmgbuild"),
            "background_sha256": digest(background),
        }
        # hard link публикует готовый файл атомарно и не перезаписывает существующий
        staged_manifest = Path(tmp) / "manifest.json"
        staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        os.link(staged_manifest, manifest_path)
        try:
            os.link(temporary, output)
        except OSError:
            manifest_path.unlink()
            raise
    print(f"Готово: {output}\nSHA256: {manifest['sha256']}")
    if manifest["ad_hoc_signed"]:
        print("Приложение внутри подписано ad-hoc; notarization при упаковке не выполнялась.")


if __name__ == "__main__":
    main()
