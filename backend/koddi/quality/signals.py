"""Детерминированные сигналы по добавленным строкам: обход типизации, «ленивые» заглушки, слабые и отключённые
тесты, сырой ввод на границе, разрастание области задачи.

Это сигналы для ревью и отчёта, а не приговор: каждый указывает на строку и объясняет, почему стоит посмотреть.
Учитываются только строки, которые добавил ход (или все строки при scope=full), - старый код не шумит.
"""

from __future__ import annotations

import ast
import bisect
import re
from pathlib import PurePosixPath
from typing import Any

from koddi.quality.contract import GateResult, Timer
from koddi.quality.functions import changed_lines, head_text, read_source, split_lines
from koddi.quality.metrics import is_test_path

PY = (".py", ".pyi")
SCRIPT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
MAX_SIGNALS = 120

# (вид, серьёзность, регулярка, языки, пояснение)
LINE_RULES: list[tuple[str, str, re.Pattern[str], tuple[str, ...], str]] = [
    ("type_suppression", "medium", re.compile(r"#\s*type:\s*ignore(?!\[[\w-]+\]\s*#)"), PY,
     "подавление проверки типов: лучше сузить тип или поправить сигнатуру; если без него никак - с кодом ошибки и причиной"),
    ("type_suppression", "medium", re.compile(r"@ts-(?:ignore|nocheck)\b"), SCRIPT,
     "@ts-ignore прячет любую ошибку в строке; @ts-expect-error с причиной хотя бы сломается, когда ошибка уйдёт"),
    ("type_suppression", "low", re.compile(r"@ts-expect-error\s*$"), SCRIPT, "@ts-expect-error без причины"),
    ("any_escape", "medium", re.compile(r"\bas\s+any\b|:\s*any\b(?!\w)|<any>"), SCRIPT,
     "any отключает проверку типов для значения и всего, что из него следует"),
    ("any_escape", "low", re.compile(r"(?:->|:)\s*(?:typing\.)?Any\b"), PY,
     "Any в сигнатуре: тип стоит выразить (TypedDict, Protocol, dataclass), иначе ошибки уходят дальше по коду"),
    ("lint_suppression", "low", re.compile(r"eslint-disable(?:-next-line|-line)?(?!\S*\s+--\s)"), SCRIPT,
     "отключение линтера без пояснения причины (`-- причина`)"),
    ("swallowed_error", "high", re.compile(r"^\s*except\s*(?:(?:BaseException|Exception)\s*)?(?:as\s+\w+\s*)?:\s*(?:pass|\.\.\.)\s*(?:#.*)?$"), PY,
     "ошибка проглатывается молча: сбой станет неправильным результатом без следа"),
    ("swallowed_error", "high", re.compile(r"catch\s*(?:\(\s*\w*\s*\))?\s*\{\s*\}"), SCRIPT,
     "пустой catch: ошибка теряется без следа"),
    ("bare_except", "medium", re.compile(r"^\s*except\s*:"), PY, "голый except ловит и KeyboardInterrupt/SystemExit"),
    ("stub", "high", re.compile(r"raise\s+NotImplementedError\b"), PY,
     "заглушка вместо реализации - допустимо только в абстрактном методе"),
    ("stub", "high", re.compile(r"throw\s+new\s+Error\(\s*['\"`](?:not\s+implemented|todo)", re.I), SCRIPT,
     "заглушка вместо реализации"),
    ("todo", "medium", re.compile(r"(?:#|//|/\*)\s*(?:TODO|FIXME|XXX|HACK)\b"), PY + SCRIPT,
     "TODO в новом коде - обязательная часть может остаться недоделанной; если это осознанный долг - вынеси в задачи"),
    ("debug_leftover", "low", re.compile(r"^\s*(?:console\.(?:log|debug)\(|debugger\s*;?\s*$)"), SCRIPT,
     "отладочный вывод в коде"),
    ("debug_leftover", "low", re.compile(r"^\s*(?:breakpoint\(\)|import\s+pdb|pdb\.set_trace\(\))"), PY, "отладочная точка остановки"),
    ("raw_boundary", "medium", re.compile(r"\bJSON\.parse\([^)]*\)\s*as\s+[A-Z]\w*"), SCRIPT,
     "непроверенное приведение внешних данных к типу: тип не проверяется во время выполнения - нужна валидация схемой"),
    ("raw_boundary", "medium", re.compile(r"await\s+request\.json\(\)|json\.loads\(\s*request\.|request\.get_json\("), PY,
     "сырое тело запроса: на границе нужна модель (pydantic/dataclass) с валидацией, а не словарь"),
    ("raw_boundary", "low", re.compile(r"def\s+\w+\([^)]*\b(?:payload|body|data)\s*:\s*dict\b"), PY,
     "словарь на входе обработчика: доменный тип с проверкой на границе защищает остальной код"),
]
SPLIT_EXCEPT = re.compile(r"^\s*except\s*(?:(?:BaseException|Exception)\s*)?(?:as\s+\w+\s*)?:\s*(?:#.*)?$")
SWALLOW_BODY = re.compile(r"^\s*(?:pass|\.\.\.)\s*(?:#.*)?$")
TEST_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    ("disabled_test", "high", re.compile(r"@pytest\.mark\.(?:skip|xfail)\b|\bpytest\.skip\(|@unittest\.skip"),
     "тест отключён: если он мешает, это повод разобраться, а не выключить"),
    ("disabled_test", "high", re.compile(r"\b(?:it|test|describe)\.skip\(|\bx(?:it|describe|test)\("), "тест отключён"),
    ("focused_test", "high", re.compile(r"\b(?:it|test|describe)\.only\("), ".only выключает все остальные тесты файла"),
    ("tautology", "high", re.compile(r"^\s*assert\s+(?:True|1|not\s+False)\s*(?:#.*)?$|expect\(\s*true\s*\)\.toBe\(\s*true\s*\)"),
     "проверка, которая проходит всегда"),
    ("broad_raises", "medium", re.compile(r"pytest\.raises\(\s*(?:Exception|BaseException)\s*\)"),
     "pytest.raises(Exception) пройдёт и на опечатке в коде - укажи конкретное исключение и match"),
]
ASSERT_CALLS = {"assert_called", "assert_called_once", "assert_called_with", "assert_called_once_with", "assert_any_call",
                "assert_has_calls", "assert_not_called", "assert_awaited", "assert_awaited_once", "assert_awaited_with",
                "assert_awaited_once_with"}


def _signal(kind: str, severity: str, path: str, line: int, text: str, advice: str) -> dict[str, Any]:
    return {"key": f"{path}|{kind}|{line}", "path": path, "line_start": line, "line_end": line, "metric": "signal",
            "kind": kind, "value": 1, "severity": severity, "evidence": {"line": text.strip()[:200]}, "advice": advice}


def _python_weak_tests(path: str, text: str, lines: set[int] | None) -> list[dict[str, Any]]:
    """Тест без единой проверки результата или только с проверками вызовов моков."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
            continue
        end = node.end_lineno or node.lineno
        if lines is not None and not any(node.lineno <= line <= end for line in lines):
            continue
        asserts = mocks = 0
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assert):
                asserts += 1
            elif isinstance(inner, ast.Call):
                name = inner.func.attr if isinstance(inner.func, ast.Attribute) else getattr(inner.func, "id", "")
                if name in ASSERT_CALLS:
                    mocks += 1
                elif name in {"raises", "warns", "approx", "fail"} or name.startswith(("assert", "check", "expect", "verify")):
                    asserts += 1
            elif isinstance(inner, ast.With):
                for item in inner.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and getattr(call.func, "attr", "") in {"raises", "warns"}:
                        asserts += 1
        if asserts == 0 and mocks == 0:
            found.append(_signal("assertion_free_test", "high", path, node.lineno, f"def {node.name}",
                                 "тест ничего не проверяет: он пройдёт при любом поведении кода"))
        elif asserts == 0:
            found.append(_signal("mock_only_test", "medium", path, node.lineno, f"def {node.name}",
                                 "тест проверяет только вызовы моков, а не результат: он повторяет реализацию"))
    return found


def _script_weak_tests(path: str, text: str, lines: set[int] | None) -> list[dict[str, Any]]:
    from koddi.codeintel.jslex import mask

    masked = mask(text)
    source_lines = split_lines(text)
    found = []
    starts = [0] + [item.end() for item in re.finditer(r"\r\n|\r|\n", masked)]
    for match in re.finditer(r"\b(?:it|test)\s*\(\s*(['\"`])", masked):
        line = bisect.bisect_right(starts, match.start())
        # тело теста: от открывающей скобки вызова до парной закрывающей
        depth = 0
        end = len(masked)
        for index in range(masked.index("(", match.start()), len(masked)):
            char = masked[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        body = masked[match.start():end]
        last = bisect.bisect_right(starts, end)
        if lines is not None and not any(line <= item <= last for item in lines):
            continue
        if not re.search(r"\b(?:expect|assert)\w*\s*[.(]|\bshould\b|toMatchSnapshot|t\.(?:is|deepEqual|true|throws)", body):
            found.append(_signal("assertion_free_test", "high", path, line, source_lines[line - 1] if line <= len(source_lines) else "",
                                 "тест ничего не проверяет: он пройдёт при любом поведении кода"))
    return found


def _connected(graph, path: str, changed: set[str]) -> bool:
    if graph is None:
        return True
    users = set(graph.imported_by.get(path, [])) | set(graph.linked_from.get(path, []))
    return bool(users) or bool(set(graph.imports.get(path, [])) & changed)


ENTRY_HINT = re.compile(r"(?:^|/)(?:main|index|app|cli|__main__|manage|server|setup|conftest)\.[\w]+$|(?:^|/)scripts?/|"
                        r"(?:^|/)migrations?/|\.(?:md|txt|json|ya?ml|toml|sql|css|scss|html|svg|png|lock)$")


def signals_gate(root, paths: list[str], *, before_texts: dict[str, str | None] | None = None, full: bool = False,
                 contract: dict[str, Any] | None = None, graph=None) -> GateResult:
    timer = Timer()
    findings: list[dict[str, Any]] = []
    new_files: list[str] = []
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in PY + SCRIPT:
            continue
        text = read_source(root, path)
        if text is None:
            continue
        before = before_texts[path] if before_texts is not None and path in before_texts else head_text(root, path)
        if before is None:
            new_files.append(path)
        added = None if full else changed_lines(before, text)
        if added is not None and not added:
            continue
        source_lines = split_lines(text)
        test = is_test_path(path)
        for number, line in enumerate(source_lines, start=1):
            if added is not None and number not in added:
                continue
            for kind, severity, pattern, suffixes, advice in LINE_RULES:
                if suffix in suffixes and pattern.search(line):
                    # абстрактный метод может честно поднимать NotImplementedError
                    if kind == "stub" and suffix in PY and any("abstractmethod" in item for item in source_lines[max(0, number - 6):number]):
                        continue
                    if kind == "debug_leftover" and test:
                        continue
                    findings.append(_signal(kind, severity, path, number, line, advice))
            # except на одной строке, pass/... на следующей - самый частый вид молча проглоченной ошибки
            if suffix in PY and SPLIT_EXCEPT.match(line) and number < len(source_lines) \
                    and SWALLOW_BODY.match(source_lines[number]):
                findings.append(_signal("swallowed_error", "high", path, number, line,
                                        "ошибка проглатывается молча: сбой станет неправильным результатом без следа"))
            if test:
                for kind, severity, pattern, advice in TEST_RULES:
                    if pattern.search(line):
                        findings.append(_signal(kind, severity, path, number, line, advice))
        if test:
            weak = _python_weak_tests(path, text, added) if suffix in PY else _script_weak_tests(path, text, added)
            findings.extend(weak)
    changed = set(paths)
    scope_items = " ".join(str(item) for key in ("scope", "affected_components")
                           for item in (contract or {}).get(key, []) or [])
    for path in new_files:
        if is_test_path(path) or ENTRY_HINT.search(path):
            continue
        if not _connected(graph, path, changed):
            findings.append(_signal("unconnected_new_file", "medium", path, 1, path,
                                    "новый файл никто не импортирует: либо он не подключён, либо это лишняя работа вне задачи"))
        elif scope_items and PurePosixPath(path).stem not in scope_items and PurePosixPath(path).parent.name not in scope_items:
            findings.append(_signal("outside_contract_scope", "low", path, 1, path,
                                    "файл не упомянут в scope/affected_components контракта - проверь, что это часть задачи"))
    findings = findings[:MAX_SIGNALS]
    counts: dict[str, int] = {}
    for item in findings:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    high = sum(1 for item in findings if item["severity"] == "high")
    status = "failed" if high else "passed"
    if not any(PurePosixPath(path).suffix.lower() in PY + SCRIPT for path in paths):
        status = "skipped"
    return GateResult("signals", status, paths, findings=findings, duration_ms=timer.ms(),
                      summary={"total": len(findings), "high": high, "by_kind": counts, "new_files": len(new_files)},
                      checker={"id": "dokky-signals", "version": 1}, confidence="heuristic",
                      notes=["сигналы - повод посмотреть строку, а не доказанный дефект"] if findings else [])
