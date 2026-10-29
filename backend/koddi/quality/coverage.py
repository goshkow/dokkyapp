"""Покрытие строк из отчётов проекта: coverage.py (JSON, Cobertura XML) и LCOV (Vitest, Jest, c8).

Отчёт засчитывается для файла, только если он новее самого файла: устаревшее покрытие - это `stale`, а не
данные. Отсутствующий отчёт - `missing`; покрытие никогда не подставляется нулём или единицей молча.
Сбор нового отчёта запускает только тестовые инструменты, которые уже есть в проекте (ничего не ставится).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPORTS = ("coverage.json", "coverage.xml", "coverage/coverage-final.json", "coverage/lcov.info", "lcov.info",
           "coverage/cobertura-coverage.xml", "htmlcov/coverage.json")
MAX_REPORT_BYTES = 80_000_000


@dataclass
class FileCoverage:
    statements: set[int]
    executed: set[int]
    source: str
    generated_at: float


@dataclass
class CoverageData:
    files: dict[str, FileCoverage] = field(default_factory=dict)
    reports: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def lookup(self, root: Path, path: str) -> tuple[FileCoverage | None, str]:
        """(покрытие файла, статус): ok / stale / missing."""
        item = self.files.get(path)
        if item is None:
            return None, "missing"
        try:
            if (root / path).stat().st_mtime > item.generated_at + 1:
                return item, "stale"
        except OSError:
            return None, "missing"
        return item, "ok"


def _relative(root: Path, raw: str, base: Path) -> str | None:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _coverage_py_json(root: Path, report: Path, data: dict, when: float, into: CoverageData, base: Path | None = None) -> None:
    base = base or report.parent
    for raw, item in (data.get("files") or {}).items():
        relative = _relative(root, raw, base) or _relative(root, raw, root)
        if not relative or not isinstance(item, dict):
            continue
        executed = {int(line) for line in item.get("executed_lines") or []}
        missing = {int(line) for line in item.get("missing_lines") or []}
        into.files[relative] = FileCoverage(executed | missing, executed, report.name, when)


def _istanbul_json(root: Path, report: Path, data: dict, when: float, into: CoverageData) -> None:
    for raw, item in data.items():
        if not isinstance(item, dict) or "statementMap" not in item:
            continue
        relative = _relative(root, item.get("path") or raw, report.parent)
        if not relative:
            continue
        statements, executed = set(), set()
        for key, location in (item.get("statementMap") or {}).items():
            line = int((location.get("start") or {}).get("line") or 0)
            if line:
                statements.add(line)
                if int((item.get("s") or {}).get(key) or 0) > 0:
                    executed.add(line)
        into.files[relative] = FileCoverage(statements, executed, report.name, when)


def _cobertura(root: Path, report: Path, when: float, into: CoverageData) -> None:
    tree = ET.parse(report)
    sources = [Path(node.text.strip()) for node in tree.iter("source") if node.text and node.text.strip()] or [root]
    for node in tree.iter("class"):
        filename = node.get("filename") or ""
        relative = next((value for base in sources if (value := _relative(root, filename, base))), None)
        if not relative:
            continue
        statements, executed = set(), set()
        for line in node.iter("line"):
            number = int(line.get("number") or 0)
            statements.add(number)
            if int(line.get("hits") or 0) > 0:
                executed.add(number)
        current = into.files.get(relative)
        if current and current.source == report.name:
            current.statements |= statements
            current.executed |= executed
        else:
            into.files[relative] = FileCoverage(statements, executed, report.name, when)


def _lcov(root: Path, report: Path, when: float, into: CoverageData, base: Path | None = None) -> None:
    relative = None
    statements: set[int] = set()
    executed: set[int] = set()
    with report.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("SF:"):
                relative = _relative(root, line[3:], base or report.parent) or _relative(root, line[3:], root)
                statements, executed = set(), set()
            elif line.startswith("DA:") and relative:
                number, _, hits = line[3:].partition(",")
                try:
                    statements.add(int(number))
                    if int(hits.split(",")[0]) > 0:
                        executed.add(int(number))
                except ValueError:
                    continue
            elif line == "end_of_record" and relative:
                into.files[relative] = FileCoverage(statements, executed, report.name, when)
                relative = None


def load(root: Path, extra: list[Path] | None = None) -> CoverageData:
    data = CoverageData()
    candidates = [root / name for name in REPORTS]
    for package in ("frontend", "web", "app", "client"):
        candidates += [root / package / name for name in REPORTS]
    candidates += extra or []
    for report in candidates:
        try:
            if not report.is_file() or report.stat().st_size > MAX_REPORT_BYTES:
                continue
            when = report.stat().st_mtime
            if report.suffix == ".info":
                _lcov(root, report, when, data)
            elif report.suffix == ".xml":
                _cobertura(root, report, when, data)
            else:
                payload = json.loads(report.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and "files" in payload and "meta" in payload:
                    _coverage_py_json(root, report, payload, when, data)
                elif isinstance(payload, dict):
                    _istanbul_json(root, report, payload, when, data)
            data.reports.append(report.relative_to(root).as_posix() if report.is_relative_to(root) else report.name)
        except (OSError, ValueError, ET.ParseError) as exc:
            data.notes.append(f"отчёт покрытия {report.name} не прочитан: {type(exc).__name__}")
    return data


def collect(runner, root: Path, files: list[str], *, timeout: int) -> tuple[CoverageData | None, list[str]]:
    """Свежий отчёт покрытия связанных тестов, если инструмент уже установлен в проекте.

    Python: `coverage` из окружения проекта поверх pytest. TypeScript: vitest с установленным провайдером
    покрытия. Отчёт пишется во временную папку, рабочая копия не меняется.
    """
    from koddi.quality.checks import _nearest, _node_bin, _python_tool, _related_tests, _venv, _venv_has, _venv_python

    notes: list[str] = []
    extra: list[tuple[Path, Path]] = []
    scratch = Path(tempfile.mkdtemp(prefix="dokky-cov-"))
    try:
        python_files = [path for path in files if path.endswith(".py")]
        if python_files:
            package = _nearest(root, python_files[0], ("pyproject.toml", "setup.cfg", "pytest.ini", "setup.py")) or root
            venv = _venv(root, package)
            python = _venv_python(venv) if venv else None
            pytest = _python_tool(root, package, "pytest")
            if python and venv and _venv_has(venv, "coverage") and pytest:
                tests = _related_tests(runner.project_id, root, python_files)
                data_file = scratch / ".coverage"
                env_target = scratch / "coverage.json"
                argv = [str(python), "-m", "coverage", "run", f"--data-file={data_file}", "-m", "pytest", "-q", "-x",
                        *tests[:40]]
                result = runner.run_process(argv, package, timeout)
                if result.get("cancelled"):
                    return None, ["сбор покрытия отменён"]
                if data_file.exists():
                    runner.run_process([str(python), "-m", "coverage", "json", f"--data-file={data_file}", "-o",
                                        str(env_target)], package, 120)
                if env_target.is_file():
                    extra.append((env_target, package))
                else:
                    notes.append("coverage.py не создал отчёт: " + (result.get("stderr") or result.get("stdout") or "")[-300:])
            else:
                notes.append("для Python нет coverage в окружении проекта - покрытие не собрано")
        script_files = [path for path in files if Path(path).suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}]
        if script_files:
            package = _nearest(root, script_files[0], ("package.json",)) or root
            vitest = _node_bin(root, package, "vitest")
            provider = any((package / "node_modules" / "@vitest" / name).is_dir() for name in ("coverage-v8", "coverage-istanbul"))
            if vitest and provider:
                relatives = [os.path.relpath(root / path, package) for path in script_files[:20]]
                result = runner.run_process([vitest, "related", "--run", "--coverage.enabled", "--coverage.reporter=lcov",
                                             f"--coverage.reportsDirectory={scratch / 'js'}", *relatives], package, timeout)
                if result.get("cancelled"):
                    return None, ["сбор покрытия отменён"]
                report = scratch / "js" / "lcov.info"
                if report.is_file():
                    extra.append((report, package))
                else:
                    notes.append("vitest не создал lcov-отчёт")
            else:
                notes.append("для TypeScript нет vitest с провайдером покрытия - покрытие не собрано")
        if not extra:
            return None, notes
        data = CoverageData()
        for report, base in extra:
            when = report.stat().st_mtime
            # пути в отчётах относительны папке, где запускались тесты
            if report.suffix == ".info":
                _lcov(root, report, when, data, base)
            else:
                _coverage_py_json(root, report, json.loads(report.read_text(encoding="utf-8")), when, data, base)
        data.reports = ["собрано сейчас"]
        return data, notes
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
