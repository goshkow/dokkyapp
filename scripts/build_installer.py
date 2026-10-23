"""Установщик Windows (один exe) и переносная версия (zip) в раскладке автообновления.

python scripts/build_installer.py --dist dist_nuitka/web_host.dist --out dist_release

Раскладка под автообновление одинакова для установки и переносной версии:
Dokky.exe (лаунчер, installer/launcher), current.json, app-<версия>/ (вся сборка), updates/.

Для подписи в CI сборка идёт в два шага: `tree` раскладывает дерево и собирает лаунчер и установщик,
затем exe подписываются, затем `package` упаковывает уже подписанные файлы. Без подкоманды - оба шага.
Установщик - WPF на встроенном в Windows .NET Framework 4.8 (installer/windows), архив дописан в конец exe,
формат хвоста описан в installer/windows/Payload.cs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAGIC = b"DOKKYSF1"
APP_EXE = "DokkyApp.exe"
LAUNCHER = "Dokky.exe"
UNINSTALL = "Uninstall.exe"
VERSION = re.compile(r"\d+\.\d+\.\d+(-beta\.\d+)?")
# zip без отметок времени сборки: одинаковая программа даёт одинаковый архив
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
PORTABLE_NOTE = (
    "Этот файл включает переносной режим Докки: Dokky.exe хранит чаты и настройки рядом с собой,\n"
    "в папке .koddi-data. Удалите файл, чтобы программа хранила данные в профиле пользователя.\n"
)


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def inside_project(path: Path) -> Path:
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if path == ROOT or not path.is_relative_to(ROOT):
        raise SystemExit(f"Папка {path} должна быть внутри проекта")
    return path


def files_of(base: Path) -> list[Path]:
    return sorted((path for path in base.rglob("*") if path.is_file()), key=lambda p: p.relative_to(base).as_posix())


def write_zip(target: Path, sources: list[tuple[str, Path]], extra: dict[str, str] | None = None,
              dirs: tuple[str, ...] = ()) -> tuple[int, int]:
    """sources: (имя в архиве, файл или папка); папка кладётся целиком под своим именем."""
    size = count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in dirs:
            archive.writestr(zipfile.ZipInfo(name.rstrip("/") + "/", FIXED_TIME), b"")
        for arcname, source in sources:
            items = [(arcname, source)] if source.is_file() else [
                (f"{arcname}/{path.relative_to(source).as_posix()}", path) for path in files_of(source)
            ]
            for name, path in items:
                info = zipfile.ZipInfo(name, FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with path.open("rb") as source_file, archive.open(info, "w", force_zip64=True) as sink:
                    shutil.copyfileobj(source_file, sink, 1 << 20)
                size += path.stat().st_size
                count += 1
        for name, text in (extra or {}).items():
            archive.writestr(zipfile.ZipInfo(name, FIXED_TIME), text.encode("utf-8"), zipfile.ZIP_DEFLATED)
    return size, count


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dotnet_build(project: str, version: str, out: Path, exe: str) -> Path:
    shutil.rmtree(out, ignore_errors=True)
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise SystemExit("Нужен .NET SDK (dotnet) для сборки установщика и лаунчера")
    subprocess.run(
        [dotnet, "build", str(ROOT / project), "-c", "Release", "-nologo", f"-p:Version={version.split('-')[0]}",
         f"-p:InformationalVersion={version}", "-o", str(out)],
        check=True,
    )
    return out / exe


def current_json(version: str) -> str:
    return json.dumps({"v": 1, "version": version, "dir": f"app-{version}", "previous": None}) + "\n"


def build_tree(dist: Path, out: Path, version: str) -> Path:
    """out/tree: Dokky.exe, current.json, app-<версия>/, updates/; out/.stub/DokkySetup.exe - установщик без архива."""
    if not (dist / APP_EXE).is_file():
        raise SystemExit(f"В {dist} нет {APP_EXE}")
    tree = out / "tree"
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    launcher = dotnet_build("installer/launcher/DokkyLauncher.csproj", version, out / ".launcher", LAUNCHER)
    shutil.copy2(launcher, tree / LAUNCHER)
    shutil.rmtree(out / ".launcher", ignore_errors=True)
    print(f"Копирую сборку в app-{version}…", flush=True)
    shutil.copytree(dist, tree / f"app-{version}")
    (tree / "current.json").write_text(current_json(version), encoding="utf-8")
    (tree / "updates").mkdir()
    dotnet_build("installer/windows/DokkySetup.csproj", version, out / ".stub", "DokkySetup.exe")
    return tree


def build_setup(stub: Path, payload: Path, manifest: dict, target: Path) -> None:
    digest = bytes.fromhex(sha256(payload))
    data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    with target.open("wb") as output:
        with stub.open("rb") as source:
            shutil.copyfileobj(source, output)
        with payload.open("rb") as source:
            shutil.copyfileobj(source, output, 1 << 20)
        output.write(data)
        output.write(struct.pack("<iq", len(data), payload.stat().st_size))
        output.write(digest)
        output.write(MAGIC)


def package(out: Path, version: str, portable: bool) -> list[Path]:
    tree = out / "tree"
    app = tree / f"app-{version}"
    stub = out / ".stub" / "DokkySetup.exe"
    for path in (tree / LAUNCHER, app / APP_EXE, stub):
        if not path.is_file():
            raise SystemExit(f"Нет {path}: сначала шаг tree")

    payload = out / ".payload.zip"
    print("Упаковываю программу для установщика…", flush=True)
    # current.json и updates/ установщик пишет сам; программа удаления - этот же установщик без архива
    size, count = write_zip(payload, [(LAUNCHER, tree / LAUNCHER), (UNINSTALL, stub), (app.name, app)])
    setup = out / f"Dokky-{version}-Windows-x64-Setup.exe"
    build_setup(stub, payload, {"name": "Докки", "version": version, "exe": LAUNCHER, "size": size, "files": count}, setup)
    payload.unlink()
    produced = [setup]

    if portable:
        print("Собираю переносную версию…", flush=True)
        archive = out / f"Dokky-{version}-Windows-x64-portable.zip"
        write_zip(
            archive,
            [("Dokky/" + LAUNCHER, tree / LAUNCHER), (f"Dokky/{app.name}", app)],
            {"Dokky/current.json": current_json(version), "Dokky/portable.dokky": PORTABLE_NOTE},
            dirs=("Dokky/updates",),
        )
        produced.append(archive)

    write_sums(out, version)
    return produced


def write_sums(out: Path, version: str) -> None:
    """Отдельным шагом: после подписи установщика его хеш меняется."""
    names = (f"Dokky-{version}-Windows-x64-Setup.exe", f"Dokky-{version}-Windows-x64-portable.zip")
    produced = [out / name for name in names if (out / name).is_file()]
    if not produced:
        raise SystemExit(f"В {out} нет файлов версии {version}")
    sums = out / "SHA256SUMS-windows.txt"
    sums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in produced), encoding="utf-8")
    for path in produced:
        print(f"  {path.name}: {path.stat().st_size / 1024 / 1024:.0f} МБ", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Установщик и переносная версия Докки для Windows")
    parser.add_argument("step", nargs="?", choices=("tree", "package", "sums"), help="только один шаг; без него - tree и package")
    parser.add_argument("--dist", type=Path, help="папка готовой программы с DokkyApp.exe (для шага tree)")
    parser.add_argument("--out", type=Path, default=Path("dist_release"))
    parser.add_argument("--version", default=None)
    parser.add_argument("--no-portable", action="store_true")
    args = parser.parse_args()

    out = inside_project(args.out)
    out.mkdir(parents=True, exist_ok=True)
    version = args.version or project_version()
    if not VERSION.fullmatch(version):
        parser.error(f"Версия {version!r} не в виде X.Y.Z или X.Y.Z-beta.N")
    if args.step in (None, "tree"):
        if args.dist is None:
            parser.error("Для шага tree нужен --dist")
        build_tree(args.dist.resolve(), out, version)
    if args.step in (None, "package"):
        package(out, version, not args.no_portable)
    if args.step == "sums":
        write_sums(out, version)


if __name__ == "__main__":
    sys.exit(main())
