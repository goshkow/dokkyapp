"""снимок реальных зависимостей сборки, SBOM и лицензии установленных пакетов."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import subprocess
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]


def inventory(output: Path, hash_lock: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]
    pending = [
        (item, "") for item in project["dependencies"] + project["optional-dependencies"]["dev"]
    ]
    distributions, dependencies, expanded = {}, {}, set()
    while pending:
        raw, extra = pending.pop()
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate({"extra": extra}):
            continue
        name = canonicalize_name(requirement.name)
        dist = metadata.distribution(name)
        if requirement.specifier and dist.version not in requirement.specifier:
            raise RuntimeError(f"Версия {name} не соответствует pyproject.toml")
        distributions[name] = dist
        for enabled in {""} | set(requirement.extras):
            if (name, enabled) in expanded:
                continue
            expanded.add((name, enabled))
            deps = []
            for item in dist.requires or []:
                dep = Requirement(item)
                if not dep.marker or dep.marker.evaluate({"extra": enabled}):
                    pending.append((item, enabled))
                    deps.append(canonicalize_name(dep.name))
            dependencies.setdefault(name, set()).update(deps)
    components = []
    notices = output / "licenses"
    notices.mkdir(exist_ok=True)
    for name, dist in sorted(distributions.items()):
        reference = f"pkg:pypi/{name}@{dist.version}"
        component = {
            "type": "library",
            "bom-ref": reference,
            "name": name,
            "version": dist.version,
            "purl": reference,
        }
        license_name = (
            dist.metadata.get("License-Expression") or dist.metadata.get("License") or "NOASSERTION"
        )
        component["licenses"] = [{"license": {"name": license_name[:4000]}}]
        components.append(component)
        target = notices / name
        for file in dist.files or []:
            if re.search(r"(?i)(?:^|/)(?:licen[cs]e|copying|notice)(?:[./_-]|$)", file.as_posix()):
                source = Path(dist.locate_file(file))
                if source.is_file() and source.stat().st_size <= 2 * 1024 * 1024:
                    target.mkdir(exist_ok=True)
                    filename = (
                        hashlib.sha256(file.as_posix().encode()).hexdigest()[:8] + "-" + file.name
                    )
                    (target / filename).write_bytes(source.read_bytes())
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": project["name"],
                "version": project["version"],
            }
        },
        "components": components,
        "dependencies": [
            {
                "ref": f"pkg:pypi/{name}@{distributions[name].version}",
                "dependsOn": [
                    f"pkg:pypi/{dep}@{distributions[dep].version}"
                    for dep in sorted(deps)
                    if dep in distributions
                ],
            }
            for name, deps in sorted(dependencies.items())
        ],
    }
    (output / "sbom.cdx.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "requirements-resolved.txt").write_text(
        "\n".join(f"{name}=={dist.version}" for name, dist in sorted(distributions.items())) + "\n",
        encoding="utf-8",
    )
    if hash_lock:

        def hashes(item):
            name, dist = item
            url = f"https://pypi.org/pypi/{name}/{dist.version}/json"
            with urllib.request.urlopen(url, timeout=30) as response:
                if response.geturl() != url:
                    raise RuntimeError("Неожиданное перенаправление PyPI")
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError("Слишком большой ответ PyPI")
            values = json.loads(raw)
            digests = sorted(
                {entry["digests"]["sha256"] for entry in values["urls"] if not entry.get("yanked")}
            )
            if not digests or any(not re.fullmatch("[a-f0-9]{64}", value) for value in digests):
                raise RuntimeError(f"Нет проверяемого артефакта для {name}")
            return f"{name}=={dist.version}" + "".join(
                " \\\n    --hash=sha256:" + value for value in digests
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            lines = list(executor.map(hashes, sorted(distributions.items())))
        (output / "requirements-windows-py311.lock").write_text(
            "# Windows x64 / Python 3.11; includes development/build dependencies\n--require-hashes\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    sources = {}
    for directory in ("backend/koddi", "prompts", "locales"):
        for path in sorted((ROOT / directory).rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".log"}
            ):
                sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    sources["pyproject.toml"] = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    (output / "source-hashes.json").write_text(
        json.dumps(sources, sort_keys=True, indent=2), encoding="utf-8"
    )
    report = {
        "commit": commit,
        "application_source_sha256": hashlib.sha256(
            json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": len(components),
        "hash_lock": hash_lock,
        "build_system": {
            Requirement(item).name: metadata.version(Requirement(item).name)
            for item in configuration["build-system"]["requires"]
        },
        "build_system_mismatch": [
            item
            for item in configuration["build-system"]["requires"]
            if metadata.version(Requirement(item).name) not in Requirement(item).specifier
        ],
        "limitations": [
            "Qt/Chromium и JS требуют отдельного учёта лицензий",
            "подпись издателя не проверялась",
            "bootstrap и компилятор требуют отдельной фиксации",
        ],
    }
    (output / "inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist_release_evidence")
    parser.add_argument("--hash-lock", action="store_true")
    args = parser.parse_args()
    print(json.dumps(inventory(args.output, args.hash_lock), ensure_ascii=True))
