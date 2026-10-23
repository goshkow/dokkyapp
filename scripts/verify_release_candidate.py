"""Проверка состава standalone-кандидата без запуска приложения."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


ASSET_ROOTS = {
    "backend/koddi/desktop/web/": "koddi/desktop/web/",
    "backend/koddi/desktop/web-next/": "koddi/desktop/web-next/",
    "backend/koddi/storage/migrations/": "koddi/storage/migrations/",
    "prompts/": "prompts/",
    "locales/": "locales/",
}


OPTIONAL_ASSET_ROOTS = {
    "backend/koddi/licenses/": "koddi/licenses/",
    "backend/koddi/computer_use/native/": "koddi/computer_use/native/",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory: Path, source_manifest: Path) -> dict:
    directory = directory.resolve(strict=True)
    expected = json.loads(source_manifest.read_text(encoding="utf-8"))
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Пустой манифест исходников")
    assets = {}
    for source, checksum in expected.items():
        if not isinstance(source, str) or not isinstance(checksum, str) or not re.fullmatch("[a-f0-9]{64}", checksum):
            raise ValueError("Некорректная запись манифеста")
        path = PurePosixPath(source)
        if path.is_absolute() or ".." in path.parts or "\\" in source or ":" in source:
            raise ValueError("Небезопасный путь манифеста")
        for prefix, target in (ASSET_ROOTS | OPTIONAL_ASSET_ROOTS).items():
            if source.startswith(prefix):
                if prefix in OPTIONAL_ASSET_ROOTS and path.suffix == ".py":
                    break
                assets[target + source[len(prefix):]] = checksum
                break
    if not assets or not all(any(name.startswith(root) for name in assets) for root in ASSET_ROOTS.values()):
        raise ValueError("Манифест не содержит все группы ресурсов")
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Ссылка внутри поставки")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        name = path.name.lower()
        if (
            name.startswith((".env", "koddi.sqlite", "tokens."))
            or name.endswith((".db", ".sqlite", ".sqlite3", ".log", ".key"))
            or (name.endswith(".pem") and relative != "certifi/cacert.pem")
            or ".sqlite3-" in name
            or "__pycache__" in path.parts
        ):
            raise ValueError("Служебные или пользовательские данные в поставке: " + relative)
        files[relative] = {"sha256": digest(path), "bytes": path.stat().st_size}
    if "DokkyApp.exe" not in files:
        raise ValueError("Нет DokkyApp.exe")
    actual_assets = {name for name in files if any(name.startswith(root) for root in (ASSET_ROOTS | OPTIONAL_ASSET_ROOTS).values())}
    if actual_assets != set(assets):
        raise ValueError("Состав встроенных ресурсов отличается от манифеста")
    for name, checksum in assets.items():
        if files[name]["sha256"] != checksum:
            raise ValueError("Изменён встроенный ресурс: " + name)
    return {
        "ok": True,
        "exe_sha256": files["DokkyApp.exe"]["sha256"],
        "assets_verified": len(assets),
        "file_count": len(files),
        "files": files,
        "limitations": ["не проверяет Authenticode и лицензии бинарных компонентов"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка состава кандидата Докки")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--sources", type=Path, default=Path("dist_release_evidence/source-hashes.json"))
    parser.add_argument("--output", type=Path, default=Path("dist_release_evidence/package-files.json"))
    args = parser.parse_args()
    result = verify(args.directory, args.sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
