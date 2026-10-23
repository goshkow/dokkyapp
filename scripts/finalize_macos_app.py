"""Доводит собранный Nuitka DokkyApp.app до рабочего вида: Info.plist, браузер агента, подпись."""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENTITLEMENTS = {
    # Chromium из Qt WebEngine: JIT и исполняемая память, библиотеки Qt подписаны другим ключом, микрофон для голоса
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
}


def install_glass_icon(bundle: Path, assets_car: Path) -> None:
    """Assets.car со значком Liquid Glass: macOS 26+ берёт его по CFBundleIconName, старые системы - .icns."""
    resources = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assets_car, resources / "Assets.car")
    path = bundle / "Contents" / "Info.plist"
    info = plistlib.loads(path.read_bytes())
    info["CFBundleIconName"] = "DokkyApp"
    path.write_bytes(plistlib.dumps(info))


def patch_info(bundle: Path) -> None:
    path = bundle / "Contents" / "Info.plist"
    info = plistlib.loads(path.read_bytes())
    info.update({
        "CFBundleDisplayName": "Докки",
        "CFBundleName": "Докки",
        "CFBundleDevelopmentRegion": "ru",
        "CFBundleLocalizations": ["ru"],
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        # ссылки koddi:// возвращают человека в программу после оплаты и входа на сайте
        "CFBundleURLTypes": [{
            "CFBundleURLName": "com.goshkow.dokky",
            "CFBundleURLSchemes": ["koddi"],
        }],
    })
    path.write_bytes(plistlib.dumps(info))
    # русская локализация: имя в Dock, Finder и меню берётся из ru.lproj
    localized = bundle / "Contents" / "Resources" / "ru.lproj"
    localized.mkdir(parents=True, exist_ok=True)
    (localized / "InfoPlist.strings").write_text(
        '"CFBundleDisplayName" = "Докки";\n"CFBundleName" = "Докки";\n', encoding="utf-8"
    )


def bundle_browser(bundle: Path) -> None:
    macos = bundle / "Contents" / "MacOS"
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("bundle_browser.py")), str(macos)],
        check=True,
    )
    # codesign не принимает папку с браузером внутри Contents/MacOS: переносим в Resources и оставляем ссылку
    browsers = macos / "playwright" / "driver" / "package" / ".local-browsers"
    target = bundle / "Contents" / "Resources" / "playwright-browsers"
    if browsers.is_dir() and not browsers.is_symlink():
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(browsers), str(target))
        browsers.symlink_to(Path("../../../../Resources/playwright-browsers"))


def sign(bundle: Path, identity: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        entitlements = Path(tmp) / "entitlements.plist"
        entitlements.write_bytes(plistlib.dumps(ENTITLEMENTS))
        command = ["codesign", "--force", "--deep", "--sign", identity,
                   "--entitlements", str(entitlements)]
        if identity != "-":
            command += ["--options", "runtime", "--timestamp"]
        subprocess.run([*command, str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--identity", default="-", help="Developer ID или '-' для подписи без сертификата")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--assets-car", type=Path, help="Assets.car, скомпилированный actool из DokkyApp.icon")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    if bundle.suffix != ".app" or not (bundle / "Contents" / "Info.plist").is_file():
        parser.error("Укажите папку .app, собранную Nuitka")
    patch_info(bundle)
    if args.assets_car:
        install_glass_icon(bundle, args.assets_car.resolve())
    if not args.no_browser:
        bundle_browser(bundle)
    sign(bundle, args.identity)
    print(f"Готово: {bundle}")


if __name__ == "__main__":
    main()
