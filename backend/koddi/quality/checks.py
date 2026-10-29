"""Проверки проекта после правок агента: какие команды есть, их запуск и разбор отказов.

Команды не придумываются: только инструменты, которые реально стоят в проекте (виртуальное окружение,
node_modules/.bin), и скрипты из package.json; команды пользователя из настроек проекта идут первыми.
Ничего не устанавливается. Отсутствующий инструмент, тайм-аут и «тестов не найдено» - это не успех,
они отделяются от настоящего падения, чтобы отчёт не выдавал непроверенное за проверенное.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from koddi.policy.paths import WorkspacePaths

KINDS = ("lint", "typecheck", "test", "build")
PY_CONFIGS = ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "setup.py")
SCRIPT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
TYPECHECK_SCRIPTS = ("typecheck", "type-check", "check-types", "types", "tsc")
LINT_SCRIPTS = ("lint", "eslint")
MAX_TARGETS = 40
MAX_DIAGNOSTICS = 30
DEFAULT_TIMEOUT = 600
OUTPUT_LIMIT = 6000
TEST_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec|specs|e2e)/|(^|/)test_[^/]+\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$")


@dataclass
class Check:
    id: str
    kind: str
    tool: str
    cwd: str
    argv: list[str]
    source: str = "detected"
    targets: list[str] = field(default_factory=list)

    def command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.argv)


# ---------- обнаружение

def _read_json(path: Path) -> dict:
    try:
        if path.is_file() and path.stat().st_size <= 512 * 1024:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _read_text(path: Path) -> str:
    try:
        if path.is_file() and path.stat().st_size <= 512 * 1024:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _nearest(root: Path, relative: str, markers: tuple[str, ...]) -> Path | None:
    """Ближайшая папка с одним из файлов-признаков между файлом и корнем проекта."""
    current = (root / relative).parent
    while True:
        if "node_modules" in current.parts:
            current = current.parent
            continue
        if any((current / marker).is_file() for marker in markers):
            return current
        if current == root or root not in current.parents:
            return None
        current = current.parent


def _workspace_packages(root: Path, markers: tuple[str, ...]) -> list[Path]:
    """Корень и вложенные пакеты монорепозитория до двух уровней (frontend/, packages/x/) для scope=full."""
    access = WorkspacePaths(root)
    found = [root] if any((root / marker).is_file() for marker in markers) else []
    for pattern in ("*", "*/*"):
        for directory in sorted(root.glob(pattern)):
            if (directory.is_dir() and not directory.name.startswith(".") and "node_modules" not in directory.parts
                    and any((directory / marker).is_file() for marker in markers) and access.accessible(directory)):
                found.append(directory)
    return found[:12]


def _rel(root: Path, path: Path) -> str:
    return "" if path == root else path.relative_to(root).as_posix()


def _venv(root: Path, package: Path) -> Path | None:
    for base in dict.fromkeys((package, root)):
        for name in (".venv", "venv", "env"):
            candidate = base / name
            if (candidate / "pyvenv.cfg").is_file():
                return candidate
    return None


def _venv_python(venv: Path) -> Path | None:
    path = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return path if path.exists() else None


def _venv_has(venv: Path, module: str) -> bool:
    patterns = ("Lib/site-packages",) if os.name == "nt" else ("lib/python*/site-packages",)
    for pattern in patterns:
        for site in venv.glob(pattern):
            if (site / module).exists() or (site / f"{module}.py").exists():
                return True
    return False


def _python_tool(root: Path, package: Path, module: str) -> list[str] | None:
    """Команда запуска модуля: из окружения проекта, иначе установленная в системе программа."""
    venv = _venv(root, package)
    if venv is not None:
        python = _venv_python(venv)
        if python is not None and _venv_has(venv, module):
            return [str(python), "-m", module]
        return None
    found = shutil.which(module)
    return [found] if found else None


def _node_bin(root: Path, package: Path, name: str) -> str | None:
    suffix = ".cmd" if os.name == "nt" else ""
    for base in dict.fromkeys((package, root)):
        candidate = base / "node_modules" / ".bin" / f"{name}{suffix}"
        if candidate.exists():
            return str(candidate)
    return None


def _package_manager(root: Path, package: Path) -> str:
    for base in dict.fromkeys((package, root)):
        for lock, manager in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
                              ("package-lock.json", "npm")):
            if (base / lock).is_file():
                return manager
    return "npm"


def _tsconfigs(package: Path) -> list[str]:
    """Конфигурации TypeScript для проверки: «решение» из references проверяется по каждой ссылке."""
    config = package / "tsconfig.json"
    if not config.is_file():
        return []
    text = re.sub(r"(?m)^\s*//.*$|/\*.*?\*/", "", _read_text(config), flags=re.S)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        data = json.loads(text)
    except ValueError:
        return ["tsconfig.json"]
    references = data.get("references") if isinstance(data, dict) else None
    if isinstance(references, list) and references and data.get("files") == []:
        result = []
        for item in references:
            target = package / str((item or {}).get("path") or "")
            target = target / "tsconfig.json" if target.is_dir() else target
            if target.is_file() and package in target.resolve().parents:
                result.append(target.relative_to(package).as_posix())
        return result or ["tsconfig.json"]
    return ["tsconfig.json"]


def _user_checks(project_id: int | None) -> list[Check]:
    """Команды проверки, которые пользователь задал для проекта в Докки (не из файлов репозитория)."""
    if project_id is None:
        return []
    from koddi.storage.db import get_setting

    items = (get_setting("qa_commands", {}) or {}).get(str(project_id)) or []
    result = []
    for index, item in enumerate(items[:20]):
        if not isinstance(item, dict) or item.get("kind") not in KINDS or not str(item.get("command") or "").strip():
            continue
        result.append(Check(id=f"user:{item['kind']}:{index}", kind=item["kind"], tool="user", cwd=str(item.get("cwd") or ""),
                            argv=["__shell__", str(item["command"]).strip()], source="user"))
    return result


def set_user_checks(project_id: int, items: list[dict]) -> list[dict]:
    from koddi.storage.db import get_setting, set_setting

    cleaned = []
    for item in items[:20]:
        kind = str(item.get("kind") or "")
        command = str(item.get("command") or "").strip()
        cwd = str(item.get("cwd") or "").strip().replace("\\", "/").strip("/")
        if kind not in KINDS or not command or len(command) > 2000:
            raise ValueError("Нужны вид проверки (lint, typecheck, test, build) и команда до 2000 символов")
        if cwd and (".." in PurePosixPath(cwd).parts or PurePosixPath(cwd).is_absolute()):
            raise ValueError("Папка запуска должна быть внутри проекта")
        cleaned.append({"kind": kind, "command": command, "cwd": cwd})
    current = dict(get_setting("qa_commands", {}) or {})
    if cleaned:
        current[str(project_id)] = cleaned
    else:
        current.pop(str(project_id), None)
    set_setting("qa_commands", current)
    return cleaned


def user_checks(project_id: int) -> list[dict]:
    from koddi.storage.db import get_setting

    return list((get_setting("qa_commands", {}) or {}).get(str(project_id)) or [])


def _related_tests(project_id: int | None, root: Path, sources: list[str]) -> list[str]:
    if project_id is None or not sources:
        return []
    from koddi.policy.account import local_owner
    from koddi.projectmap import service

    try:
        service.ensure(project_id, root, wait=10.0)
        graph = service.graph(project_id, local_owner())
    except Exception:  # noqa: BLE001 - без карты проекта связанные тесты просто не найдутся
        return []
    found: list[str] = []
    for path in sources:
        for test, _why in graph.tests_for(path):
            if test not in found:
                found.append(test)
    return found


def _python_checks(root: Path, files: list[str], scope: str, project_id: int | None, notes: list[str]) -> list[Check]:
    packages: dict[Path, list[str]] = {}
    if scope == "full":
        for package in _workspace_packages(root, PY_CONFIGS):
            packages[package] = []
    for path in files:
        if path.endswith((".py", ".pyi")):
            package = _nearest(root, path, PY_CONFIGS) or root
            packages.setdefault(package, []).append(path)
    checks = []
    for package, paths in packages.items():
        where = _rel(root, package)
        local = [str(PurePosixPath(path).relative_to(where)) if where else path for path in paths]
        pyproject = _read_text(package / "pyproject.toml")
        if "[tool.ruff" in pyproject or (package / "ruff.toml").is_file() or (package / ".ruff.toml").is_file():
            ruff = _python_tool(root, package, "ruff")
            if ruff is None:
                notes.append(f"ruff настроен в {where or 'корне'}, но не установлен в окружении проекта")
            elif scope == "full" or local:
                checks.append(Check(f"python:lint:ruff@{where}", "lint", "ruff", where,
                                    [*ruff, "check", "--force-exclude", *(local if scope != "full" else ["."])], targets=paths))
        if "[tool.mypy" in pyproject or (package / "mypy.ini").is_file() or (package / ".mypy.ini").is_file():
            mypy = _python_tool(root, package, "mypy")
            if mypy is None:
                notes.append(f"mypy настроен в {where or 'корне'}, но не установлен в окружении проекта")
            elif scope == "full" or local:
                checks.append(Check(f"python:typecheck:mypy@{where}", "typecheck", "mypy", where,
                                    [*mypy, *(local if scope != "full" else ["."])], targets=paths))
        has_pytest = ("[tool.pytest" in pyproject or (package / "pytest.ini").is_file() or (package / "conftest.py").is_file()
                      or "[tool:pytest]" in _read_text(package / "setup.cfg") or (package / "tests").is_dir())
        if not has_pytest:
            continue
        pytest = _python_tool(root, package, "pytest")
        if pytest is None:
            notes.append(f"pytest не установлен в окружении проекта ({where or 'корень'})")
            continue
        if scope == "full":
            checks.append(Check(f"python:test:pytest@{where}", "test", "pytest", where, [*pytest, "-q"]))
            continue
        sources = [path for path in paths if not TEST_PATH.search(path)]
        targets = [path for path in paths if TEST_PATH.search(path) and PurePosixPath(path).name.startswith("test_")]
        targets += [test for test in _related_tests(project_id, root, sources) if test.endswith(".py") and test not in targets]
        targets = [test for test in targets if not where or test.startswith(where + "/")][:MAX_TARGETS]
        if targets:
            local_tests = [str(PurePosixPath(test).relative_to(where)) if where else test for test in targets]
            checks.append(Check(f"python:test:pytest@{where}", "test", "pytest", where, [*pytest, "-q", *local_tests], targets=targets))
        elif sources:
            notes.append("тестов, связанных с изменёнными Python-файлами, не найдено: " + ", ".join(sources[:5])
                         + "; полный набор - qa.run со scope=full")
    return checks


def _script_checks(root: Path, files: list[str], scope: str, project_id: int | None, notes: list[str]) -> list[Check]:
    packages: dict[Path, list[str]] = {}
    if scope == "full":
        for package in _workspace_packages(root, ("package.json",)):
            packages[package] = []
    for path in files:
        if path.endswith(SCRIPT_SUFFIXES):
            package = _nearest(root, path, ("package.json",))
            if package is not None:
                packages.setdefault(package, []).append(path)
    checks = []
    for package, paths in packages.items():
        where = _rel(root, package)
        local = [str(PurePosixPath(path).relative_to(where)) if where else path for path in paths]
        manifest = _read_json(package / "package.json")
        scripts = manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
        manager = _package_manager(root, package)
        typed = any(path.endswith((".ts", ".tsx", ".mts", ".cts", ".vue")) for path in paths) or scope == "full"
        script = next((name for name in TYPECHECK_SCRIPTS if name in scripts), None)
        if typed and script:
            checks.append(Check(f"node:typecheck:{script}@{where}", "typecheck", f"{manager} run {script}", where,
                                [manager, "run", script], source="project script", targets=paths))
        elif typed and (tsc := _node_bin(root, package, "tsc")):
            for config in _tsconfigs(package):
                checks.append(Check(f"node:typecheck:tsc:{config}@{where}", "typecheck", "tsc", where,
                                    [tsc, "--noEmit", "-p", config], targets=paths))
        elif typed and (package / "tsconfig.json").is_file():
            notes.append(f"TypeScript в {where or 'корне'} не установлен (node_modules/.bin/tsc): проверка типов не запускалась")
        eslint = _node_bin(root, package, "eslint")
        has_config = any(package.glob("eslint.config.*")) or any(package.glob(".eslintrc*")) or "eslintConfig" in manifest
        if scope == "full" and (lint := next((name for name in LINT_SCRIPTS if name in scripts), None)):
            checks.append(Check(f"node:lint:{lint}@{where}", "lint", f"{manager} run {lint}", where, [manager, "run", lint],
                                source="project script"))
        elif eslint and has_config and local:
            checks.append(Check(f"node:lint:eslint@{where}", "lint", "eslint", where, [eslint, "--no-warn-ignored", *local], targets=paths))
        vitest, jest = _node_bin(root, package, "vitest"), _node_bin(root, package, "jest")
        test_script = str(scripts.get("test") or "")
        if scope == "full":
            if test_script and "no test specified" not in test_script:
                checks.append(Check(f"node:test:test@{where}", "test", f"{manager} test", where, [manager, "test"], source="project script"))
            if "build" in scripts:
                checks.append(Check(f"node:build:build@{where}", "build", f"{manager} run build", where, [manager, "run", "build"],
                                    source="project script"))
        elif local and vitest:
            checks.append(Check(f"node:test:vitest@{where}", "test", "vitest", where, [vitest, "related", "--run", *local], targets=paths))
        elif local and jest:
            checks.append(Check(f"node:test:jest@{where}", "test", "jest", where,
                                [jest, "--findRelatedTests", *local, "--passWithNoTests"], targets=paths))
        elif local and test_script and "no test specified" not in test_script:
            notes.append(f"в {where or 'корне'} тесты запускаются только целиком ({manager} test): qa.run со scope=full")
    return checks


def detect(root: Path, files: list[str], *, scope: str = "changed", project_id: int | None = None) -> tuple[list[Check], list[str]]:
    """Проверки для изменённых файлов (scope=changed) или всего проекта (full) и пояснения, чего нет."""
    access = WorkspacePaths(root)
    visible = [path for path in dict.fromkeys(files) if access.accessible(root / path)]
    notes: list[str] = []
    checks = [check for check in _user_checks(project_id) if scope == "full" or check.kind != "build"]
    user_kinds = {check.kind for check in checks}
    detected = _python_checks(root, visible, scope, project_id, notes) + _script_checks(root, visible, scope, project_id, notes)
    # команда пользователя заменяет найденную того же вида
    checks += [check for check in detected if check.kind not in user_kinds]
    if not checks and not notes:
        notes.append("для изменённых файлов нет настроенных проверок (lint, типы, тесты)")
    order = {kind: index for index, kind in enumerate(KINDS)}
    checks.sort(key=lambda check: (order[check.kind], check.id))
    return checks, notes


# ---------- разбор результата

DIAGNOSTIC_PATTERNS = (
    re.compile(r"^(?P<path>[^\s:()][^:()]*?\.[A-Za-z]{1,5})\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<message>.+)$"),  # tsc
    re.compile(r"^(?P<path>[^\s:][^:]*?\.[A-Za-z]{1,5}):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<message>.+)$"),  # ruff, mypy, gcc
    re.compile(r"^(?:FAILED|ERROR)\s+(?P<path>[^\s:]+\.py)(?:::(?P<test>\S+))?(?:\s+-\s+(?P<message>.+))?$"),  # pytest
    re.compile(r"^\s*(?:FAIL|×|✕)\s+(?P<path>\S+\.[cm]?[jt]sx?)(?:\s+>\s+(?P<test>.+))?$"),  # vitest, jest
)
MISSING = re.compile(r"command not found|is not recognized as|CommandNotFoundException|No module named (pytest|ruff|mypy)|"
                     r"Cannot find module '(typescript|vitest|jest|eslint)'|ENOENT.*spawn|not found: (npm|pnpm|yarn|bun|node)", re.I)
NO_TESTS = re.compile(r"no tests ran|No test files found|No tests found|collected 0 items", re.I)
COUNTS = (
    re.compile(r"(?P<n>\d+) (?P<what>passed|failed|errors?|skipped)\b"),  # pytest
    re.compile(r"Tests?:?\s+(?:(?P<failed>\d+) failed\s*\|?\s*,?\s*)?(?P<passed>\d+) passed"),  # vitest, jest
)


def _diagnostics(root: Path, cwd: str, text: str) -> list[dict]:
    found = []
    base = root / cwd if cwd else root
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 1000:
            continue
        for pattern in DIAGNOSTIC_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            path = match.group("path").replace("\\", "/")
            try:
                candidate = (base / path).resolve()
                relative = candidate.relative_to(root).as_posix() if candidate.is_relative_to(root) else path
            except (OSError, ValueError):
                relative = path
            item = {"path": relative, "message": (match.groupdict().get("message") or line)[:300]}
            if match.groupdict().get("line"):
                item["line"] = int(match.group("line"))
            if match.groupdict().get("test"):
                item["test"] = match.group("test")[:200]
            found.append(item)
            break
        if len(found) >= MAX_DIAGNOSTICS:
            break
    return found


def _counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in COUNTS[0].finditer(text[-4000:]):
        what = "errors" if match.group("what").startswith("error") else match.group("what")
        counts[what] = int(match.group("n"))
    if not counts:
        match = COUNTS[1].search(text[-4000:])
        if match:
            counts["passed"] = int(match.group("passed"))
            if match.group("failed"):
                counts["failed"] = int(match.group("failed"))
    return counts


def classify(check: Check, result: dict, root: Path) -> dict[str, Any]:
    """Итог одной проверки: passed, failed, no_tests, environment, timeout, cancelled."""
    output = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    code = result.get("returncode")
    if result.get("cancelled"):
        status = "cancelled"
    elif result.get("timed_out"):
        status = "timeout"
    # «нет инструмента» - только если сам запуск не состоялся: когда тесты уже посчитаны, такая строка
    # в выводе - провал кода (тест вызвал отсутствующую команду), а не окружение
    elif result.get("spawn_error") or code in (126, 127) or (code not in (0, None) and MISSING.search(output)
                                                            and not any(pattern.search(output) for pattern in COUNTS)):
        status = "environment"
    elif (check.tool == "pytest" and code == 5) or (code != 0 and check.kind == "test" and NO_TESTS.search(output)):
        status = "no_tests"
    elif code == 0:
        status = "passed"
    else:
        status = "failed"
    diagnostics = _diagnostics(root, check.cwd, output) if status == "failed" else []
    tail = output.strip()[-OUTPUT_LIMIT:]
    signature = hashlib.sha1(json.dumps([check.id, [(item["path"], item.get("line"), item.get("test"), item["message"][:80])
                                                    for item in diagnostics] or tail[-400:]]).encode()).hexdigest()[:16]
    return {"id": check.id, "kind": check.kind, "tool": check.tool, "cwd": check.cwd, "command": check.command()
            if check.argv[0] != "__shell__" else check.argv[1], "source": check.source, "status": status, "exit_code": code,
            "counts": _counts(output), "diagnostics": diagnostics, "output_tail": tail if status != "passed" else tail[-600:],
            "signature": signature if status == "failed" else None}


def _check_shell_argv(command: str) -> list[str]:
    from koddi.tools.runner import shell_argv

    if os.name == "nt":
        # powershell сам завершает процесс с кодом 0 после неуспешной внешней команды
        command = (
            "$global:LASTEXITCODE = $null; " + command + "\n"
            "$dokkyCheckOk = $?; if (-not $dokkyCheckOk) { "
            "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
            "exit 1 }; exit 0"
        )
    return shell_argv(command)


def run(runner, checks: list[Check], *, timeout: int = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Запуск по очереди: быстрые (lint, типы) первыми; отмена хода останавливает текущую команду и остальные."""

    root = runner._require_workspace()
    access = WorkspacePaths(root)
    deadline = time.monotonic() + max(30, timeout)
    results = []
    for check in checks:
        cwd = (root / check.cwd).resolve() if check.cwd else root
        if runner.cancel_event.is_set():
            results.append({**classify(check, {"cancelled": True}, root), "duration_seconds": 0})
            continue
        if not cwd.is_dir() or not (cwd == root or access.accessible(cwd)):
            results.append({**classify(check, {"spawn_error": True, "stderr": "папка запуска недоступна"}, root), "duration_seconds": 0})
            continue
        left = int(deadline - time.monotonic())
        if left <= 0:
            results.append({**classify(check, {"timed_out": True}, root), "duration_seconds": 0, "skipped": "общий лимит времени"})
            continue
        # через оболочку входа: у приложения из Finder нет PATH пользователя (node, pnpm из nvm и т.п.)
        command = check.argv[1] if check.argv[0] == "__shell__" else _shell_join(check.argv)
        started = time.monotonic()
        try:
            outcome = runner.run_process(_check_shell_argv(command), cwd, min(left, timeout))
        except OSError as exc:
            outcome = {"spawn_error": True, "stderr": str(exc)}
        results.append({**classify(check, outcome, root), "duration_seconds": round(time.monotonic() - started, 1)})
    return results


def _shell_join(argv: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    return shlex.join(argv)


def summarize(results: list[dict]) -> str:
    """Строка для человека: ✓ typecheck · ✕ tests (2 failed) · ? lint (нет ruff)."""
    marks = {"passed": "✓", "failed": "✕", "no_tests": "–", "environment": "?", "timeout": "⏱", "cancelled": "■"}
    parts = []
    for item in results:
        counts = item.get("counts") or {}
        extra = ", ".join(f"{value} {key}" for key, value in counts.items() if value)
        label = f"{marks.get(item['status'], '?')} {item['kind']} {item['tool']}" + (f" ({extra})" if extra else "")
        parts.append(label)
    return " · ".join(parts)


def overall(results: list[dict]) -> str:
    statuses = {item["status"] for item in results}
    if not results:
        return "no_checks"
    if "cancelled" in statuses:
        return "cancelled"
    if "failed" in statuses:
        return "failed"
    # тайм-аут и отсутствующий инструмент не чинятся правкой кода: это непроверенное, а не провал
    if "passed" in statuses:
        return "passed" if statuses <= {"passed", "no_tests"} else "partial"
    return "unverified"


def as_dict(check: Check) -> dict:
    return asdict(check)
