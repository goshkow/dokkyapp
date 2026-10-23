"""Каталог установленных runtime-зависимостей и их исходных лицензионных текстов."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    components = []

    def notices(identifier, sources):
        result = []
        for label, source in sources:
            if not source.is_file() or source.is_symlink() or source.stat().st_size > 2_000_000:
                continue
            data = source.read_bytes()
            notice_id = hashlib.sha256(label.encode()).hexdigest()[:16]
            target = output / "notices" / identifier / (notice_id + ".txt")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            result.append(
                {
                    "id": notice_id,
                    "name": Path(label).name,
                    "path": target.relative_to(output).as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return result

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    pending = [(raw, "") for raw in config["project"]["dependencies"]]
    seen, dependencies = set(), set()
    while pending:
        raw, extra = pending.pop()
        req = Requirement(raw)
        if req.marker and not req.marker.evaluate({"extra": extra}):
            continue
        name = canonicalize_name(req.name)
        dist = metadata.distribution(name)
        identifier = "py-" + name
        if name not in dependencies:
            dependencies.add(name)
            license_name = (
                dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
            )
            classifiers = [
                value.removeprefix("License :: ")
                for value in dist.metadata.get_all("Classifier", [])
                if value.startswith("License :: ")
            ]
            if not license_name.strip() or len(license_name) > 160 or "\n" in license_name:
                license_name = "; ".join(classifiers) or "NOASSERTION"
            source_files = [
                (file.as_posix(), Path(dist.locate_file(file)))
                for file in dist.files or []
                if re.search(
                    r"(?i)(?:^|/)(?:licen[cs]e|copying|notice)(?:[./_-]|$)", file.as_posix()
                )
            ]
            files = notices(identifier, source_files)
            components.append(
                {
                    "id": identifier,
                    "name": dist.metadata.get("Name", name),
                    "version": dist.version,
                    "license": license_name,
                    "source": f"https://pypi.org/project/{name}/{dist.version}/",
                    "usage": "python_runtime",
                    "notices": files,
                    "status": "notice_collected" if files else "review_required",
                }
            )
        for enabled in {""} | set(req.extras):
            if (name, enabled) in seen:
                continue
            seen.add((name, enabled))
            pending.extend((item, enabled) for item in dist.requires or [])
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    for relative, value in sorted(lock["packages"].items()):
        if not relative or value.get("dev") or value.get("devOptional"):
            continue
        package = ROOT / "frontend" / relative
        info = json.loads((package / "package.json").read_text())
        name = info["name"]
        identifier = "npm-" + re.sub(r"[^a-z0-9._-]", "-", name.lower())
        source_files = [
            (file.name, file)
            for file in package.iterdir()
            if re.match(r"(?i)^(licen[cs]e|copying|notice)(?:[._-]|$)", file.name)
        ]
        files = notices(identifier, source_files)
        components.append(
            {
                "id": identifier,
                "name": name,
                "version": value["version"],
                "license": value.get("license", info.get("license", "NOASSERTION")),
                "source": "https://www.npmjs.com/package/" + name + "/v/" + value["version"],
                "usage": "frontend_runtime",
                "notices": files,
                "status": "notice_collected" if files else "review_required",
            }
        )
    for identifier, name, license_name, source, file in [
        (
            "font-inter",
            "Inter",
            "SIL OFL 1.1",
            "https://rsms.me/inter/",
            "backend/koddi/desktop/web/assets/Inter-LICENSE",
        ),
        (
            "font-unbounded",
            "Unbounded",
            "SIL OFL 1.1",
            "https://fonts.google.com/specimen/Unbounded",
            "backend/koddi/desktop/web-next/assets/fonts/OFL-Unbounded.txt",
        ),
        (
            "dokky-notices",
            "Докки: сведения о сторонних компонентах",
            "См. уведомления",
            "",
            "THIRD_PARTY.md",
        ),
    ]:
        components.append(
            {
                "id": identifier,
                "name": name,
                "version": "repository",
                "license": license_name,
                "source": source,
                "usage": "bundled_asset",
                "notices": notices(identifier, [(file, ROOT / file)]),
                "status": "notice_collected",
            }
        )
    # уведомления о прежней адаптации навыков, без назначения им новой лицензии
    for identifier, name, license_name, source, filename in [
        (
            "skill-jeffallan",
            "Источники навыков jeffallan/claude-skills",
            "MIT",
            "https://github.com/jeffallan/claude-skills",
            "jeffallan-MIT.txt",
        ),
        (
            "skill-marketing",
            "Источники навыков coreyhaines31/marketingskills",
            "MIT",
            "https://github.com/coreyhaines31/marketingskills",
            "marketingskills-MIT.txt",
        ),
        (
            "skill-canvas",
            "Источник навыка визуального дизайна anthropics/skills",
            "Apache-2.0",
            "https://github.com/anthropics/skills",
            "anthropic-canvas-Apache-2.0.txt",
        ),
    ]:
        base = ROOT / "third_party/skill-sources"
        components.append(
            {
                "id": identifier,
                "name": name,
                "version": "original_zip_version_unconfirmed",
                "license": license_name,
                "source": source,
                "usage": "existing_adapted_instructions",
                "status": "provenance_review_required",
                "notices": notices(
                    identifier, [(filename, base / filename), ("NOTICE.txt", base / "NOTICE.txt")]
                ),
            }
        )
    # Qt/Chromium содержат собственные вложенные компоненты вне Python METADATA
    components.append(
        {
            "id": "qt-chromium",
            "name": "Qt / Chromium: вложенные компоненты",
            "version": metadata.version("PySide6"),
            "license": "LGPL-3.0 и лицензии вложенных компонентов",
            "source": "https://doc.qt.io/qt-6/qtwebengine-licensing.html",
            "usage": "native_runtime",
            "notices": [],
            "status": "release_inventory_required",
        }
    )
    result = {
        "schema_version": 1,
        "platform": sys.platform,
        "components": sorted(components, key=lambda item: item["id"]),
        "limitations": [
            "Это инвентаризация, а не юридическая гарантия. Проверяйте состав конкретной сборки.",
            "Каталог Python сформирован на указанной платформе. На Windows его нужно пересобрать.",
            "Qt/Chromium, встроенные навыки и provenance перенесённых файлов требуют отдельной проверки перед релизом.",
        ],
    }
    (output / "catalog.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "backend/koddi/licenses")
    result = build(parser.parse_args().output)
    print(
        json.dumps(
            {
                "platform": result["platform"],
                "components": len(result["components"]),
                "review_required": [
                    item["id"]
                    for item in result["components"]
                    if item["status"] != "notice_collected"
                ],
            }
        )
    )
