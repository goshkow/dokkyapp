"""Скачивает бинарники agent-desktop нужной платформы в backend/koddi/computer_use/native/agent-desktop/.

Архив берётся из релиза vercel-labs/agent-desktop, сумма сверяется с зашитой. Если файлы уже
лежат и их метка совпадает, ничего не скачивается.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import platform
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"
BASE = f"https://github.com/vercel-labs/agent-desktop/releases/download/v{VERSION}/"
DESTINATION = ROOT / "backend/koddi/computer_use/native/agent-desktop"
LICENSES = ROOT / "third_party/agent-desktop"
MARKER = ".archive-sha256"
MAX_ARCHIVE = 20_000_000
MAX_MEMBER = 40_000_000
# цель -> (архив, sha256 из checksums.txt релиза, файлы внутри)
TARGETS = {
    "macos-arm64": (
        "agent-desktop-v1.2.0-aarch64-apple-darwin.tar.gz",
        "219813428bb1ad8decae172faed05c5964a2415170df402e2786ccfa6452ba82",
        ("agent-desktop", "agent-desktop-macos-helper"),
    ),
    "macos-x64": (
        "agent-desktop-v1.2.0-x86_64-apple-darwin.tar.gz",
        "d9dc50226f28d7fc86a2f5b0945f0c206dff6c31e418907749f493b6f9e773e6",
        ("agent-desktop", "agent-desktop-macos-helper"),
    ),
    "windows-x64": (
        "agent-desktop-v1.2.0-x86_64-pc-windows-msvc.tar.gz",
        "a4199f741a441cedd349bf56c5eba01783b1261258b498efeb7ec9945220c3e8",
        ("agent-desktop.exe",),
    ),
    "windows-arm64": (
        "agent-desktop-v1.2.0-aarch64-pc-windows-msvc.tar.gz",
        "d6d8643310c2c13eace2261444d9e4c0d16ca09581f38500983104903604caa6",
        ("agent-desktop.exe",),
    ),
}
EXECUTABLES = {"agent-desktop", "agent-desktop-macos-helper", "agent-desktop.exe"}


def detect_target() -> str:
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    if sys.platform == "win32":
        return "windows-arm64" if arm else "windows-x64"
    raise SystemExit("Computer Use поддерживает только macOS и Windows")


def is_current(target: str, destination: Path) -> bool:
    _, digest, files = TARGETS[target]
    marker = destination / MARKER
    return (
        marker.is_file()
        and marker.read_text(encoding="utf-8").strip() == digest
        and all((destination / name).is_file() for name in (*files, "LICENSE", "NOTICE"))
    )


def download(name: str) -> bytes:
    request = urllib.request.Request(BASE + name, headers={"User-Agent": "dokky-build"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise SystemExit("Архив agent-desktop больше ожидаемого")
    return data


def fetch(target: str, destination: Path = DESTINATION, *, data: bytes | None = None) -> Path:
    archive, digest, files = TARGETS[target]
    if is_current(target, destination):
        return destination
    data = download(archive) if data is None else data
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f"Сумма {archive} не совпала с ожидаемой")
    staging = destination.with_name(destination.name + ".building")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = {member.name: member for member in tar.getmembers()}
            if set(members) != set(files) or any(
                not member.isreg() or member.size > MAX_MEMBER for member in members.values()
            ):
                raise SystemExit("В архиве agent-desktop неожиданный состав")
            for name in files:
                source = tar.extractfile(members[name])
                assert source is not None
                path = staging / name
                path.write_bytes(source.read())
                path.chmod(0o755 if name in EXECUTABLES else 0o644)
        for name in ("LICENSE", "NOTICE"):
            shutil.copyfile(LICENSES / name, staging / name)
        (staging / MARKER).write_text(digest + "\n", encoding="utf-8")
        shutil.rmtree(destination, ignore_errors=True)
        staging.rename(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), default=None, help="по умолчанию - платформа этой машины")
    args = parser.parse_args()
    target = args.target or detect_target()
    path = fetch(target)
    print(f"agent-desktop {VERSION} ({target}): {path}")


if __name__ == "__main__":
    main()
