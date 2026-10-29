"""Мутационное тестирование изменённых функций: ловят ли тесты намеренно внесённые ошибки.

Мутант - одна правка оператора или условия (`>`↔`>=`, `==`↔`!=`, `and`↔`or`, `True`↔`False`, `+`↔`-`,
отрицание условия `if`). Python-мутанты строятся по AST и проверяются компиляцией (не компилируется -
`invalid`, без прогона); TypeScript/JavaScript - по токенам вне строк и комментариев, только операторы,
окружённые пробелами (дженерики `<T>` не трогаются).

Изоляция: файлы проекта копируются в одноразовую папку профиля, зависимости (`node_modules`) подключаются
ссылкой; мутант пишется только в копию, рабочая копия пользователя не меняется ни при каком исходе.
Сначала в копии прогоняются связанные тесты без мутаций: если они не проходят, оценки нет (`baseline_failed`),
прошлый успешный результат не перетирается. Для Python проверяется, что тесты импортируют код из копии,
а не из оригинала (editable-установка) - иначе любой мутант «выжил» бы ложно.

Исходы: killed, survived, uncovered (строка не исполняется тестами по отчёту покрытия), invalid,
timeout (отдельно, не засчитывается как убитый), infrastructure_error, skipped (бюджет).
Кэш - по хешу файла, тестовых файлов, команды и версии среды: изменение кода инвалидирует результат.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from koddi.config import data_dir
from koddi.quality.contract import GateResult, Timer, cache_get, cache_put
from koddi.quality.functions import Function

MUTATION_VERSION = 2
OVERLAY_MARKER = ".dokky-overlay.json"
MAX_OVERLAY_BYTES = 400 * 1024 * 1024
MAX_OVERLAY_FILES = 60_000
LINKED_DIRS = {"node_modules"}
COPY_SKIP = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             "dist", "build", ".next", ".nuxt", "coverage", "htmlcov", ".tox", ".turbo", ".cache"}
STALE_OVERLAY_SECONDS = 2 * 3600
INVALID_OUTPUT = re.compile(r"SyntaxError|IndentationError|Transform failed|Unexpected token|error TS\d+|ParseError", re.I)
OUTCOMES = ("killed", "survived", "equivalent", "uncovered", "invalid", "timeout", "infrastructure_error", "skipped")

PY_SWAPS = {ast.Gt: ">=", ast.GtE: ">", ast.Lt: "<=", ast.LtE: "<", ast.Eq: "!=", ast.NotEq: "==",
            ast.Is: "is not", ast.IsNot: "is", ast.In: "not in", ast.NotIn: "in"}
PY_TEXT = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.Eq: "==", ast.NotEq: "!=", ast.Is: "is",
           ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in", ast.Add: "+", ast.Sub: "-", ast.And: "and", ast.Or: "or"}
PY_BINOP = {ast.Add: "-", ast.Sub: "+"}
JS_SWAPS = {" > ": " >= ", " >= ": " > ", " < ": " <= ", " <= ": " < ", " === ": " !== ", " !== ": " === ",
            " == ": " != ", " != ": " == ", " && ": " || ", " || ": " && ", " + ": " - ", " - ": " + "}
JS_OPERATOR = re.compile(r" (?:===|!==|==|!=|>=|<=|&&|\|\||>|<|\+|-) ")
JS_BOOL = re.compile(r"\b(true|false)\b")


@dataclass
class Mutant:
    id: str
    path: str
    symbol: str
    line: int
    operator: str
    original: str
    replacement: str
    start: int
    end: int

    def apply(self, text: str) -> str:
        return text[:self.start] + self.replacement + text[self.end:]


# ---------- генерация мутантов

def _offsets(text: str) -> list[int]:
    """Начала строк по `\n` - как считает строки ast (splitlines режет и по \u2028, \x0c)."""
    starts = [0]
    index = text.find("\n")
    while index != -1:
        starts.append(index + 1)
        index = text.find("\n", index + 1)
    starts.append(len(text))
    return starts


def _position(text: str, starts: list[int], line: int, byte_col: int) -> int:
    """ast даёт колонку в байтах UTF-8; переводим в смещение символа."""
    begin = starts[line - 1]
    line_text = text[begin:starts[line]] if line < len(starts) else text[begin:]
    return begin + len(line_text.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


def _operator_between(text: str, start: int, end: int, token: str) -> tuple[int, int] | None:
    """Оператор между операндами (между ними бывают только скобки, пробелы и переносы)."""
    segment = text[start:end]
    if "#" in segment:
        return None
    if token[0].isalpha():
        pattern = r"\b" + r"\s+".join(map(re.escape, token.split())) + r"\b"
    else:
        pattern = r"(?<![=!<>+\-*/%&|^@])" + re.escape(token) + r"(?![=<>*/])"
    match = re.search(pattern, segment)
    if not match:
        return None
    return start + match.start(), start + match.end()


def python_mutants(path: str, text: str, targets: list[Function]) -> list[Mutant]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    starts = _offsets(text)
    ranges = [(item.line_start, item.line_end, item.symbol) for item in targets]
    result: list[Mutant] = []

    def owner(line: int) -> str | None:
        hits = [item for item in ranges if item[0] <= line <= item[1]]
        return min(hits, key=lambda item: item[1] - item[0])[2] if hits else None

    def end_of(node: ast.AST) -> int:
        return _position(text, starts, node.end_lineno, node.end_col_offset)

    def start_of(node: ast.AST) -> int:
        return _position(text, starts, node.lineno, node.col_offset)

    def add(symbol: str, line: int, operator: str, start: int, end: int, replacement: str) -> None:
        original = text[start:end]
        if original == replacement:
            return
        result.append(Mutant(f"{path}:{line}:{start}:{operator}", path, symbol, line, operator, original, replacement, start, end))

    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        symbol = owner(line) if line else None
        if symbol is None:
            continue
        if isinstance(node, ast.Compare):
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                if type(op) in PY_SWAPS:
                    span = _operator_between(text, end_of(left), start_of(right), PY_TEXT[type(op)])
                    if span:
                        add(symbol, line, "compare", *span, PY_SWAPS[type(op)])
                left = right
        elif isinstance(node, ast.BinOp) and type(node.op) in PY_BINOP:
            span = _operator_between(text, end_of(node.left), start_of(node.right), PY_TEXT[type(node.op)])
            if span:
                add(symbol, line, "arithmetic", *span, PY_BINOP[type(node.op)])
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            token = PY_TEXT[type(node.op)]
            span = _operator_between(text, end_of(node.values[0]), start_of(node.values[1]), token)
            if span:
                add(symbol, line, "boolean", *span, "or" if token == "and" else "and")
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            add(symbol, line, "constant", start_of(node), end_of(node), "False" if node.value else "True")
        elif isinstance(node, (ast.If, ast.While)):
            test = node.test
            add(symbol, test.lineno, "negate_condition", start_of(test), end_of(test),
                f"not ({text[start_of(test):end_of(test)]})")
    unique = {item.id: item for item in result}
    return sorted(unique.values(), key=lambda item: item.start)


def script_mutants(path: str, text: str, targets: list[Function]) -> list[Mutant]:
    from koddi.codeintel.jslex import mask

    masked = mask(text)
    starts = _offsets(text)
    result: list[Mutant] = []
    for function in targets:
        begin = starts[function.line_start - 1]
        end = starts[function.line_end] if function.line_end < len(starts) else len(text)
        segment = masked[begin:end]
        for match in JS_OPERATOR.finditer(segment):
            token = match.group(0)
            absolute = begin + match.start()
            line = function.line_start + segment.count("\n", 0, match.start())
            # `a - b` в типах и выражениях вида `x - -1` пропускать не нужно: это валидные мутанты
            result.append(Mutant(f"{path}:{line}:{absolute}:op", path, function.symbol, line,
                                 "boolean" if token.strip() in {"&&", "||"} else "compare" if token.strip() not in {"+", "-"} else "arithmetic",
                                 token, JS_SWAPS[token], absolute, absolute + len(token)))
        for match in JS_BOOL.finditer(segment):
            absolute = begin + match.start()
            line = function.line_start + segment.count("\n", 0, match.start())
            result.append(Mutant(f"{path}:{line}:{absolute}:bool", path, function.symbol, line, "constant",
                                 match.group(1), "false" if match.group(1) == "true" else "true", absolute, match.end() + begin))
    unique = {item.id: item for item in result}
    return sorted(unique.values(), key=lambda item: item.start)


def mutants_for(path: str, text: str, targets: list[Function]) -> list[Mutant]:
    if path.endswith(".py"):
        return python_mutants(path, text, targets)
    return script_mutants(path, text, targets)


def select(mutants: list[Mutant], limit: int) -> list[Mutant]:
    """Равномерно по функциям и видам операторов: бюджет не уходит целиком на одну функцию."""
    groups: dict[tuple[str, str], list[Mutant]] = {}
    for item in mutants:
        groups.setdefault((item.symbol, item.operator), []).append(item)
    chosen: list[Mutant] = []
    queues = list(groups.values())
    while queues and len(chosen) < limit:
        for queue in list(queues):
            if not queue:
                queues.remove(queue)
                continue
            chosen.append(queue.pop(0))
            if len(chosen) >= limit:
                break
    return chosen


# ---------- изолированная копия

def overlays_root() -> Path:
    path = data_dir() / "quality-overlays"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remove(path: Path) -> None:
    def on_error(function, target, _info):
        try:
            os.chmod(target, 0o700)
            function(target)
        except OSError:
            pass

    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    # ссылки на node_modules снимаем до удаления папки, чтобы rmtree не пошёл по ним
    from koddi.policy.paths import _is_reparse_point

    for current, dirs, _files in os.walk(path):
        for name in list(dirs):
            target = Path(current) / name
            # в Python 3.11 нет Path.is_junction: junction из mklink /J виден только по атрибуту reparse point
            if _is_reparse_point(target):
                try:
                    os.unlink(target) if target.is_symlink() and os.name != "nt" else os.rmdir(target)
                except OSError:
                    pass
                dirs.remove(name)
    shutil.rmtree(path, onerror=on_error)


def cleanup_stale_overlays(*, max_age: float = STALE_OVERLAY_SECONDS) -> int:
    """Убирает копии, оставшиеся после аварийного выхода: только папки с нашей меткой и старше порога."""
    removed = 0
    now = time.time()
    for item in overlays_root().iterdir():
        marker = item / OVERLAY_MARKER
        try:
            if not item.is_dir() or item.is_symlink() or not marker.is_file():
                continue
            if now - marker.stat().st_mtime < max_age:
                continue
        except OSError:
            continue
        _remove(item)
        removed += 1
    return removed


def _tracked_files(root: Path) -> list[str] | None:
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    try:
        proc = subprocess.run(["git", "-c", "core.fsmonitor=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                              capture_output=True, timeout=60, check=False, creationflags=HIDDEN_SUBPROCESS)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [item for item in proc.stdout.decode("utf-8", errors="replace").split("\0") if item]


def _link_dir(source: Path, target: Path) -> bool:
    try:
        os.symlink(source, target, target_is_directory=True)
        return True
    except OSError:
        if os.name != "nt":
            return False
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    # на Windows без прав на символические ссылки работает junction
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)], capture_output=True, check=False,
                          creationflags=HIDDEN_SUBPROCESS)
    return proc.returncode == 0


def make_overlay(root: Path, *, accessible=None) -> tuple[Path, list[str]]:
    """Копия файлов проекта (по git, иначе обходом) без тяжёлых каталогов; node_modules - ссылкой."""
    notes: list[str] = []
    overlay = overlays_root() / f"mut-{uuid.uuid4().hex[:12]}"
    overlay.mkdir()
    try:
        (overlay / OVERLAY_MARKER).write_text(json.dumps({"pid": os.getpid(), "root": str(root), "created": time.time()}),
                                              encoding="utf-8")
        return overlay, _fill_overlay(root, overlay, notes, accessible)
    except BaseException:
        # недоделанная копия (нет места, нет прав, отмена) не должна оставаться в данных Докки
        _remove(overlay)
        raise


def _fill_overlay(root: Path, overlay: Path, notes: list[str], accessible) -> list[str]:
    files = _tracked_files(root)
    if files is None:
        files = []
        for current, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in COPY_SKIP and not name.startswith(".git")]
            for name in names:
                files.append((Path(current) / name).relative_to(root).as_posix())
    total = 0
    copied = 0
    for relative in files:
        parts = relative.split("/")
        if set(parts[:-1]) & COPY_SKIP:
            continue
        if accessible and not accessible(relative):
            continue  # закрытые и секретные файлы в копию не попадают
        source = root / relative
        try:
            if source.is_symlink() or not source.is_file():
                continue
            size = source.stat().st_size
        except OSError:
            continue
        total += size
        copied += 1
        if total > MAX_OVERLAY_BYTES or copied > MAX_OVERLAY_FILES:
            raise OverlayError(f"проект больше лимита изолированной копии ({MAX_OVERLAY_BYTES // 1024 // 1024} МБ, "
                               f"{MAX_OVERLAY_FILES} файлов)")
        target = overlay / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for current, dirs, _names in os.walk(root):
        rel = Path(current).relative_to(root)
        if set(rel.parts) & (COPY_SKIP - LINKED_DIRS) or len(rel.parts) > 4:
            dirs[:] = []
            continue
        for name in list(dirs):
            if name in LINKED_DIRS:
                target = overlay / rel / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if not _link_dir(Path(current) / name, target):
                    notes.append(f"не удалось подключить {rel / name} в копию")
                dirs.remove(name)
    return notes


class OverlayError(RuntimeError):
    pass


# ---------- прогон

def _module_candidates(path: str) -> list[str]:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[index:]) for index in range(len(parts)) if parts[index:]]


def _import_origin_ok(runner, python: str, overlay: Path, cwd: Path, path: str) -> tuple[bool, str]:
    script = ("import importlib.util,json,sys\nfor name in json.loads(sys.argv[1]):\n    try:\n"
              "        spec=importlib.util.find_spec(name)\n    except Exception:\n        continue\n"
              "    if spec and spec.origin:\n        print(spec.origin); break\n")
    result = runner.run_process([python, "-c", script, json.dumps(_module_candidates(path))], cwd, 60)
    origin = (result.get("stdout") or "").strip().splitlines()
    if not origin:
        return True, "модуль не найден напрямую - проверка импорта пропущена"
    try:
        inside = Path(origin[-1]).resolve().is_relative_to(overlay.resolve())
    except (OSError, ValueError):
        inside = False
    return inside, origin[-1]


def _python_for(check) -> str | None:
    first = check.argv[0] if check.argv else ""
    if first and Path(first).name.lower().startswith("python"):
        return first
    if not first:
        return None
    # в venv на Windows рядом лежит Scripts\python.exe, без расширения файла нет
    for name in ("python", "python.exe"):
        candidate = Path(first).with_name(name)
        if candidate.exists():
            return str(candidate)
    return None


def _runtime(runner, check, cwd: Path) -> str:
    from koddi.quality.checks import _shell_join
    from koddi.quality.checks import _check_shell_argv

    if not check.argv or check.argv[0] == "__shell__":
        return "shell"
    try:
        result = runner.run_process(_check_shell_argv(_shell_join([check.argv[0], "--version"])), cwd, 30)
    except OSError:
        return "unknown"
    return ((result.get("stdout") or "") + (result.get("stderr") or "")).strip()[:120]


TEST_CONFIGS = ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "package.json",
                "vitest.config.ts", "vitest.config.js", "jest.config.js", "jest.config.ts", "tsconfig.json")


def _test_files_digest(root: Path, check, path: str = "") -> str:
    """Отпечаток всего, от чего зависит исход тестов: сами тесты, соседние помощники, conftest и конфиги по пути
    до корня. Соседи и конфиги - по размеру и времени изменения, чтобы не читать лишнее."""
    digest = hashlib.sha256()
    targets = sorted(check.targets or [])
    for target in targets:
        try:
            digest.update(target.encode() + hashlib.sha256((root / target).read_bytes()).digest())
        except OSError:
            digest.update(target.encode() + b"missing")
    folders: set[Path] = set()
    for relative in [*targets, path]:
        if not relative:
            continue
        folder = (root / relative).parent
        while True:
            folders.add(folder)
            if folder == root or root not in folder.parents:
                break
            folder = folder.parent
    test_folders = {(root / target).parent for target in targets}
    for folder in sorted(folders):
        names = [folder / name for name in TEST_CONFIGS]
        if folder in test_folders:
            # помощники рядом с тестами (helpers.py, fixtures) тоже меняют исход
            try:
                names += [item for item in folder.iterdir() if item.suffix in {".py", ".ts", ".js", ".mjs", ".tsx"}]
            except OSError:
                pass
        for item in sorted(set(names)):
            try:
                stat = item.stat()
            except OSError:
                continue
            digest.update(str(item.relative_to(root)).encode() + f":{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:16]


class _Clock:
    """Каждая запись файла в копии получает новое время изменения: кэш байт-кода Python сверяет время
    с точностью до секунды и размер, и мутант той же длины мог бы выполниться старым байт-кодом."""

    def __init__(self) -> None:
        self.value = int(time.time()) + 10

    def touch(self, path: Path) -> None:
        self.value += 2
        os.utime(path, (self.value, self.value))


def _outcome(check, result: dict, overlay: Path, path: str) -> str:
    from koddi.quality.checks import classify

    if result.get("timed_out"):
        return "timeout"
    if result.get("returncode") == 0:
        return "survived"
    output = (result.get("stdout") or "") + (result.get("stderr") or "")
    if classify(check, result, overlay)["status"] in {"environment", "no_tests"}:
        return "infrastructure_error"
    if INVALID_OUTPUT.search(output) and PurePosixPath(path).name in output:
        return "invalid"
    return "killed"


def _finding(item: dict[str, Any]) -> dict[str, Any]:
    outcome = item["outcome"]
    evidence = {"operator": item["operator"], "original": item["original"].strip(), "replacement": item["replacement"].strip()}
    if item.get("output_tail"):
        evidence["output_tail"] = item["output_tail"]
    advice = {"survived": "допиши поведенческий тест, который различает эти варианты (не подгоняй тест под мутацию); если варианты "
                          "неотличимы по поведению - мутант эквивалентный, это решает человек",
              "uncovered": "строку не исполняет ни один связанный тест",
              "timeout": "мутант завис - это не считается убитым; проверь, нет ли в коде бесконечного цикла при этом условии"}
    return {"key": item["key"], "path": item["path"], "line_start": item["line"], "line_end": item["line"],
            "symbol": item["symbol"], "metric": "mutant", "kind": outcome, "value": 1,
            "severity": {"survived": "high", "uncovered": "medium"}.get(outcome, "info"),
            "evidence": evidence, "advice": advice.get(outcome, "")}


def mutation_key(item: dict[str, Any], ordinal: int) -> str:
    """Ключ мутанта без смещений: переживает правки выше по файлу (для решения «эквивалентный»)."""
    return f"{item['path']}|{item['symbol']}|{item['operator']}|{item['original'].strip()}->{item['replacement'].strip()}|{ordinal}"


def mutation_gate(runner, root: Path, changed: list[Function], *, budget: dict[str, Any], coverage_data=None,
                  accessible=None, project_id: int | None = None, owner: str | None = None) -> GateResult:
    from koddi.quality.checks import _check_shell_argv, _shell_join, classify, detect

    timer = Timer()
    checker = {"id": "dokky-mutation", "version": MUTATION_VERSION}
    max_mutants = int(budget.get("max_mutants", 0))
    if max_mutants <= 0:
        return GateResult("mutation", "skipped", notes=["мутационное тестирование не входит в бюджет этого прогона"],
                          checker=checker)
    targets_by_file: dict[str, list[Function]] = {}
    for function in changed:
        if PurePosixPath(function.path).suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            targets_by_file.setdefault(function.path, []).append(function)
    if not targets_by_file:
        return GateResult("mutation", "skipped", notes=["нет изменённых функций"], checker=checker)
    cleanup_stale_overlays()
    deadline = time.monotonic() + float(budget.get("wall_seconds", 300))
    files = list(targets_by_file.items())[: int(budget.get("max_files", 6))]
    left_out = [path for path in targets_by_file if path not in dict(files)]
    per_file_limit = max(1, max_mutants // max(1, len(files)))
    outcomes: list[dict[str, Any]] = []
    notes: list[str] = []
    files_done: list[str] = []
    no_mutants: set[str] = set()
    baseline_failures: list[dict[str, Any]] = []
    infrastructure: list[str] = []
    cancelled = False
    overlay: Path | None = None
    clock = _Clock()
    try:
        for path, functions in files:
            if runner.cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() > deadline:
                notes.append(f"{path}: не оценён - исчерпан бюджет времени")
                continue
            raw = (root / path).read_bytes()
            text = raw.decode("utf-8", errors="replace")
            mutants = select(mutants_for(path, text, functions), per_file_limit)
            if not mutants:
                notes.append(f"{path}: подходящих мутаций нет")
                no_mutants.add(path)
                continue
            checks, check_notes = detect(root, [path], scope="changed", project_id=runner.project_id)
            tests = [check for check in checks if check.kind == "test"]
            if not tests:
                notes.append(f"{path}: связанные тесты не найдены - мутации не оценены"
                             + (f" ({'; '.join(check_notes[:2])})" if check_notes else ""))
                continue
            check = tests[0]
            command = check.argv[1] if check.argv[0] == "__shell__" else _shell_join(check.argv)
            runtime = _runtime(runner, check, (root / check.cwd) if check.cwd else root)
            cache_key = "mut:" + hashlib.sha256(json.dumps(
                [MUTATION_VERSION, str(root), path, hashlib.sha256(raw).hexdigest(), command, _test_files_digest(root, check, path),
                 runtime, [item.id for item in mutants]]).encode()).hexdigest()
            cached = cache_get(cache_key)
            if cached and cached.get("complete"):
                outcomes.extend({**item, "cached": True} for item in cached["outcomes"])
                files_done.append(path)
                continue
            if overlay is None:
                try:
                    overlay, overlay_notes = make_overlay(root, accessible=accessible)
                except (OverlayError, OSError) as exc:
                    infrastructure.append(f"изолированная копия не создана: {exc}")
                    break
                notes.extend(overlay_notes)
            target = overlay / path
            if not target.is_file():
                infrastructure.append(f"{path}: файла нет в изолированной копии (закрыт настройками доступа)")
                continue
            work = (overlay / check.cwd) if check.cwd else overlay
            python = _python_for(check) if path.endswith(".py") else None
            if python:
                inside, origin = _import_origin_ok(runner, python, overlay, work, path)
                if not inside:
                    infrastructure.append(f"{path}: тесты импортируют оригинал ({origin}), а не копию - вероятно, "
                                          "пакет установлен в режиме editable; мутации в копии не оценить")
                    continue
            started = time.monotonic()
            # базовый прогон тоже в общем бюджете времени гейта
            baseline_limit = int(min(float(budget.get("baseline_timeout", 300)), max(10.0, deadline - time.monotonic())))
            baseline = runner.run_process(_check_shell_argv(command), work, baseline_limit)
            baseline_seconds = time.monotonic() - started
            verdict = classify(check, baseline, overlay)
            if verdict["status"] == "cancelled":
                cancelled = True
                break
            if verdict["status"] != "passed":
                baseline_failures.append({"path": path, "status": verdict["status"], "command": command,
                                          "output_tail": verdict["output_tail"][-1500:]})
                continue
            per_mutant = int(min(float(budget.get("mutant_timeout", 120)), max(10.0, baseline_seconds * 3 + 5)))
            file_coverage, coverage_state = coverage_data.lookup(root, path) if coverage_data else (None, "missing")
            file_outcomes: list[dict[str, Any]] = []
            for mutant in mutants:
                record = {key: value for key, value in asdict(mutant).items() if key not in {"start", "end"}}
                if runner.cancel_event.is_set():
                    cancelled = True
                    break
                if time.monotonic() > deadline:
                    file_outcomes.append({**record, "outcome": "skipped", "reason": "бюджет времени"})
                    continue
                if coverage_state == "ok" and file_coverage and mutant.line in file_coverage.statements \
                        and mutant.line not in file_coverage.executed:
                    file_outcomes.append({**record, "outcome": "uncovered"})
                    continue
                mutated = mutant.apply(text)
                if path.endswith(".py"):
                    try:
                        compile(mutated, path, "exec")
                    except SyntaxError:
                        file_outcomes.append({**record, "outcome": "invalid", "reason": "не компилируется"})
                        continue
                target.write_bytes(mutated.encode("utf-8"))
                clock.touch(target)
                try:
                    result = runner.run_process(_check_shell_argv(command), work, per_mutant)
                finally:
                    target.write_bytes(raw)
                    clock.touch(target)
                if result.get("cancelled"):
                    cancelled = True
                    break
                outcome = _outcome(check, result, overlay, path)
                output = ((result.get("stdout") or "") + (result.get("stderr") or "")).strip()
                file_outcomes.append({**record, "outcome": outcome,
                                      **({"output_tail": output[-400:]} if outcome in {"survived", "invalid"} else {})})
            outcomes.extend(file_outcomes)
            files_done.append(path)
            if cancelled:
                break
            # тайм-аут зависит от загрузки машины - такой итог не кэшируем
            if not any(item["outcome"] in {"skipped", "infrastructure_error", "timeout"} for item in file_outcomes):
                cache_put(cache_key, {"complete": True, "outcomes": file_outcomes})
    finally:
        if overlay is not None:
            _remove(overlay)
    seen: dict[str, int] = {}
    for item in outcomes:
        base = mutation_key(item, 0)[:-2]
        seen[base] = seen.get(base, 0) + 1
        item["key"] = mutation_key(item, seen[base] - 1)
    if project_id is not None and owner:
        from koddi.quality.metrics import baseline_rows

        decisions = baseline_rows(project_id, owner, "mutation", [item["key"] for item in outcomes if item["outcome"] == "survived"])
        for item in outcomes:
            if item["outcome"] == "survived" and decisions.get(item["key"], {}).get("decision") == "accepted":
                # человек подтвердил, что мутант эквивалентен коду: поведение не отличить никаким тестом
                item["outcome"] = "equivalent"
    counts = {name: sum(1 for item in outcomes if item["outcome"] == name) for name in OUTCOMES}
    evaluated = counts["killed"] + counts["survived"]
    score = round(counts["killed"] / evaluated, 4) if evaluated else None
    findings = [_finding(item) for item in outcomes if item["outcome"] in {"survived", "uncovered", "invalid", "timeout"}]
    summary = {**counts, "score": score, "evaluated": evaluated, "files": files_done, "max_mutants": max_mutants,
               "baseline_failed": baseline_failures, "infrastructure": infrastructure}
    error = None
    if cancelled:
        status = "cancelled"
    elif counts["survived"] or counts["uncovered"]:
        status = "failed"
    elif evaluated and set(files_done) | no_mutants >= set(targets_by_file) and not counts["skipped"] and not baseline_failures \
            and not infrastructure:
        status = "passed"
    elif evaluated:
        # часть файлов или мутантов не оценена: «всё убито» по куску - не доказательство силы тестов
        status = "unavailable"
        error = "partial"
        notes.append(f"оценена только часть: {len(set(files_done))} из {len(targets_by_file)} файлов")
    else:
        status = "unavailable"
        if baseline_failures:
            error = "baseline_failed"
            notes.append("тесты не проходят без мутаций - оценки силы тестов нет: сначала почини базовый прогон")
        elif infrastructure:
            error = "infrastructure"
        elif counts["skipped"] or any("бюджет времени" in note for note in notes):
            error = "budget"
    if left_out:
        notes.append(f"не оценены по лимиту файлов бюджета: {', '.join(left_out[:5])}")
    if baseline_failures and error != "baseline_failed":
        notes.append(f"{len(baseline_failures)} файлов не оценены: базовый прогон тестов не прошёл")
    notes.extend(infrastructure)
    if counts["timeout"]:
        notes.append(f"{counts['timeout']} мутантов упёрлись в тайм-аут - они не считаются убитыми")
    if counts["skipped"]:
        notes.append(f"{counts['skipped']} мутантов не запускались - исчерпан бюджет времени")
    return GateResult("mutation", status, list(targets_by_file), findings=findings, summary=summary, notes=notes,
                      duration_ms=timer.ms(), checker=checker, error=error,
                      confidence="exact" if all(path.endswith(".py") for path in targets_by_file) else "heuristic")
