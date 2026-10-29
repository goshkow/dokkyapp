"""Анализ влияния: что затронет изменение символа или файлов - использования, зависящие файлы, тесты, API, заметки.

Использования символа - семантические (codeintel), зависимости файлов - граф импортов карты проекта. Ответ
разделяет проверенное и приблизительное и предлагает тесты для запуска; оценка риска объясняет причины.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from koddi.policy.paths import WorkspacePaths

MAX_DEPENDENTS = 60
MAX_TESTS = 30


def changed_files(root: Path, *, limit: int = 200) -> list[str]:
    """Изменённые и новые файлы рабочей копии git (без игнорируемых и закрытых правилами проекта)."""
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    try:
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root, capture_output=True,
            timeout=15, check=False, creationflags=HIDDEN_SUBPROCESS,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    entries = proc.stdout.decode("utf-8", errors="replace").split("\0")
    access = WorkspacePaths(root)
    result: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1  # у переименования следом идёт старый путь
        if "D" in status:
            continue
        if access.accessible(root / path):
            result.append(path)
        if len(result) >= limit:
            break
    return result


def _test_command(root: Path, tests: list[str]) -> list[str]:
    commands = []
    python_tests = [item for item in tests if item.endswith(".py")]
    if python_tests and any((root / name).exists() for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "conftest.py")):
        commands.append("python -m pytest -q " + " ".join(python_tests[:15]))
    scripts = [item for item in tests if not item.endswith(".py")]
    package = root / "package.json"
    if scripts and package.is_file():
        try:
            text = package.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "vitest" in text:
            commands.append("npx vitest run " + " ".join(scripts[:15]))
        elif "jest" in text:
            commands.append("npx jest " + " ".join(scripts[:15]))
        elif "playwright" in text:
            commands.append("npx playwright test " + " ".join(scripts[:15]))
    return commands


def analyze(runner, args: dict[str, Any]) -> dict[str, Any]:
    from koddi.codeintel.pyproject import SymbolError
    from koddi.codeintel.service import CodeIntel

    root = runner._require_workspace()
    depth = max(1, min(int(args.get("depth") or 2), 4))
    warnings: list[str] = []
    seeds: dict[str, str] = {}
    symbol_info = None
    usages: list[dict] = []
    candidates: list[dict] = []
    if args.get("symbol") or args.get("line"):
        service = CodeIntel(root, runner.project_id, runner.cancel_event)
        try:
            refs = service.references(symbol=args.get("symbol"), path=args.get("path"), line=args.get("line"),
                                      column=args.get("column"))
        except SymbolError as exc:
            return {"ok": False, "error_code": "symbol_error", "error": str(exc), "retryable": True}
        symbol_info = refs.get("symbol")
        usages = refs.get("references", [])
        candidates = refs.get("candidates", [])
        if refs.get("accuracy") != "verified":
            warnings.append("usages are matched by name only (no semantic provider); treat them as approximate")
        warnings.extend(item for item in refs.get("warnings", []) if item not in warnings)
        for item in usages:
            if not item.get("external"):
                seeds.setdefault(item["path"], "uses the symbol")
        if symbol_info and symbol_info.get("path"):
            seeds[symbol_info["path"]] = "declares the symbol"
    paths = [str(item) for item in args.get("paths") or []]
    access = WorkspacePaths(root)
    for value in paths:
        try:
            target = access.resolve(value, check_ignore=True)
        except Exception as exc:  # путь вне проекта или закрыт правилами - явная ошибка вызывающему
            return {"ok": False, "error_code": "path_error", "error": f"{value}: {exc}"}
        seeds.setdefault(target.relative_to(root).as_posix(), "requested")
    if not seeds:
        for path in changed_files(root):
            seeds[path] = "changed in the working copy"
        if not seeds:
            return {"ok": True, "summary": "no symbol, paths or working-copy changes to analyze", "risk": {"level": "none"}}
    graph = None
    if runner.project_id is not None:
        from koddi.policy.account import local_owner
        from koddi.projectmap import service as map_service

        try:
            map_service.ensure(runner.project_id, root, wait=20.0, cancel=runner.cancel_event)
            graph = map_service.graph(runner.project_id, local_owner())
        except map_service.MapError as exc:
            warnings.append(f"project map unavailable: {exc}; dependents and tests are not computed")
    else:
        warnings.append("not a Dokky project: dependents and tests are not computed")
    dependents: dict[str, dict] = {}
    tests: dict[str, str] = {}
    routes: list[dict] = []
    api_callers: dict[str, list[str]] = {}
    notes: list[dict] = []
    if graph is not None:
        frontier = [path for path in seeds if path in graph.files]
        seen = set(frontier)
        for level in range(1, depth + 1):
            following = []
            for current in frontier:
                for importer in graph.imported_by.get(current, []):
                    if importer in seen:
                        continue
                    seen.add(importer)
                    if graph.is_test(importer):
                        tests.setdefault(importer, f"imports {current}")
                        continue
                    dependents[importer] = {"path": importer, "depth": level, "via": current}
                    following.append(importer)
            frontier = following
        for path in [*seeds, *list(dependents)[:20]]:
            if graph.is_test(path):
                tests.setdefault(path, "is a changed test")
                continue
            for test, why in graph.tests_for(path):
                tests.setdefault(test, f"{why} ({path})")
        if symbol_info and symbol_info.get("name"):
            name = symbol_info["name"]
            for path, record in graph.files.items():
                info = record.get("info") or {}
                if info.get("test") and name in (info.get("refs") or []):
                    tests.setdefault(path, f"calls {name}")
        for key, route in graph.routes.items():
            if route["file"] in seeds or (route["file"] in dependents and dependents[route["file"]]["depth"] == 1):
                routes.append({"api": key[4:], "file": route["file"], "line": route.get("line"), "handler": route.get("handler")})
                callers = [item for item in graph.callers.get(key, []) if item != route["file"]]
                if callers:
                    api_callers[key[4:]] = callers[:10]
        for path in seeds:
            for note_ref in graph.notes_by_node.get(f"file:{path}", []):
                note = graph.notes_by_id.get(note_ref)
                if note:
                    notes.append({"id": note["id"], "title": note["title"], "kind": note["kind"], "path": path,
                                  **({"stale": True} if note_ref in graph.stale_notes else {})})
    reasons = []
    score = 0
    if len(dependents) >= 10:
        score += 2
        reasons.append(f"{len(dependents)} files depend on it (depth {depth})")
    elif dependents:
        score += 1
        reasons.append(f"{len(dependents)} dependent file(s)")
    central = [path for path in seeds if graph is not None and graph.rank.get(path, 0) >= 0.5]
    if central:
        score += 1
        reasons.append("central module: " + ", ".join(central[:3]))
    if routes:
        score += 1
        reasons.append(f"{len(routes)} API route(s) affected")
    if api_callers:
        score += 1
        reasons.append("clients call the affected API: " + ", ".join(sorted({item for values in api_callers.values() for item in values})[:4]))
    if graph is not None and not tests:
        score += 1
        reasons.append("no tests cover the affected files")
    if candidates:
        score += 1
        reasons.append(f"{len(candidates)} use(s) with unknown receiver type need review")
    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    ordered_tests = sorted(tests.items())[:MAX_TESTS]
    return {
        "ok": True,
        **({"symbol": symbol_info} if symbol_info else {}),
        "seeds": [{"path": path, "reason": reason} for path, reason in seeds.items()][:50],
        "usages": usages[:80],
        "usage_count": len(usages),
        "candidates": candidates[:30],
        "dependents": sorted(dependents.values(), key=lambda item: (item["depth"], item["path"]))[:MAX_DEPENDENTS],
        "dependents_total": len(dependents),
        "tests": [{"path": path, "reason": reason} for path, reason in ordered_tests],
        "suggested_commands": _test_command(root, [path for path, _ in ordered_tests]),
        "api": routes[:30],
        "api_callers": api_callers,
        "notes": notes[:20],
        "risk": {"level": level, "reasons": reasons},
        **({"warnings": warnings} if warnings else {}),
    }
