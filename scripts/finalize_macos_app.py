"""Доводит собранный Nuitka DokkyApp.app до рабочего вида: Info.plist, браузер агента, подпись."""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

BUNDLE_ID = "com.goshkow.dokky"
ENTITLEMENTS = {
    # Chromium из Qt WebEngine: JIT и исполняемая память, библиотеки Qt подписаны другим ключом, микрофон для голоса
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
    "com.apple.security.device.camera": True,
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
        # Qt в сборке требует 14.4; как проверять - docs/MACOS_DISTRIBUTION.md
        "LSMinimumSystemVersion": "14.4",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        # ссылки koddi:// возвращают человека в программу после оплаты и входа на сайте
        "CFBundleURLTypes": [{
            "CFBundleURLName": BUNDLE_ID,
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


LOCAL_SIGNING = Path.home() / ".dokky-signing"


@dataclass(frozen=True)
class Signer:
    """adhoc - без сертификата; local - свой сертификат из ~/.dokky-signing; developer - Developer ID."""

    identity: str
    kind: str
    keychain: Path | None = None

    def args(self) -> list[str]:
        command = ["--sign", self.identity]
        if self.keychain:
            command += ["--keychain", str(self.keychain)]
        if self.kind == "developer":
            command += ["--options", "runtime", "--timestamp"]
        return command


def local_signer() -> Signer | None:
    """Свой сертификат: macOS привязывает права к нему и к идентификатору, а не к хэшу сборки,
    поэтому Универсальный доступ переживает обновления, и чужая программа его не унаследует."""
    keychain = LOCAL_SIGNING / "dokky-signing.keychain-db"
    password_file = LOCAL_SIGNING / "keychain-pass"
    certificate = LOCAL_SIGNING / "cert.pem"
    if not (keychain.is_file() and password_file.is_file() and certificate.is_file()):
        return None
    password = password_file.read_text(encoding="utf-8").strip()
    subprocess.run(["security", "unlock-keychain", "-p", password, str(keychain)], check=True)
    fingerprint = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-fingerprint", "-sha1"],
        check=True, capture_output=True, text=True,
    ).stdout.split("=", 1)[1].strip().replace(":", "")
    return Signer(fingerprint, "local", keychain)


def resolve_signer(identity: str) -> Signer:
    if identity == "auto":
        signer = local_signer()
        if signer:
            return signer
        print("Нет ~/.dokky-signing: подпись без сертификата, права macOS после обновления придётся выдать заново")
        identity = "-"
    if identity == "-":
        return Signer("-", "adhoc")
    return Signer(identity, "developer")


def stable_requirement(identifier: str) -> str:
    """Без сертификата macOS по умолчанию привязывает права к хэшу сборки, и после обновления
    Универсальный доступ молча перестаёт действовать. Требование по идентификатору переживает обновления,
    но его может повторить любая программа - поэтому это запасной путь, когда своего сертификата нет."""
    return f'=designated => identifier "{identifier}"'


def sign_agent_desktop(bundle: Path, signer: Signer) -> None:
    """Бинарники agent-desktop получают свой постоянный идентификатор вместо случайного из их сборки."""
    folder = bundle / "Contents" / "MacOS" / "koddi" / "computer_use" / "native" / "agent-desktop"
    if not folder.is_dir():
        raise SystemExit("В бандле нет agent-desktop: сначала python scripts/fetch_agent_desktop.py")
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.name.startswith("agent-desktop") and not path.is_symlink():
            identifier = f"{BUNDLE_ID}.{path.name}"
            command = ["codesign", "--force", *signer.args(), "--identifier", identifier]
            if signer.kind == "adhoc":
                command += ["--requirements", stable_requirement(identifier)]
            subprocess.run([*command, str(path)], check=True)


def sign(bundle: Path, signer: Signer) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        entitlements = Path(tmp) / "entitlements.plist"
        entitlements.write_bytes(plistlib.dumps(ENTITLEMENTS))
        subprocess.run(["codesign", "--force", "--deep", *signer.args(), "--entitlements", str(entitlements),
                        str(bundle)], check=True)
        # --deep переподписал вложенный код со случайными идентификаторами, а --deep с --requirements навесил бы
        # всем одно требование, поэтому agent-desktop и сам бандл подписываются ещё раз отдельно
        sign_agent_desktop(bundle, signer)
        command = ["codesign", "--force", *signer.args(), "--entitlements", str(entitlements), "--identifier", BUNDLE_ID]
        if signer.kind == "adhoc":
            command += ["--requirements", stable_requirement(BUNDLE_ID)]
        subprocess.run([*command, str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--identity", default="auto",
                        help="auto - свой сертификат из ~/.dokky-signing, иначе без сертификата; '-' - без сертификата; Developer ID")
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
    sign(bundle, resolve_signer(args.identity))
    print(f"Готово: {bundle}")


if __name__ == "__main__":
    main()
