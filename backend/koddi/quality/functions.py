"""Функции исходников для метрик: границы, цикломатическая сложность, структурный отпечаток, изменённые строки.

Python разбирается модулем ast (точно). JavaScript/TypeScript - лексически поверх `codeintel.jslex.mask`
(комментарии и строки вычищены, номера строк сохранены): границы и сложность там эвристические, это
отражается в `confidence`. Вложенная функция считается отдельно и не входит в сложность внешней.
"""

from __future__ import annotations

import ast
import bisect
import difflib
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}
MAX_FILE_BYTES = 600_000
# сгенерированное, сторонние и служебные каталоги в метрики не попадают
SKIP_PARTS = {"node_modules", ".venv", "venv", "env", ".git", "dist", "build", "out", "coverage", "__pycache__",
              ".next", ".nuxt", "vendor", "third_party", "site-packages", ".mypy_cache", ".pytest_cache", ".tox"}
GENERATED = re.compile(r"(?:^|/)[^/]*(?:\.min\.[cm]?js|\.generated\.[^/]+|_pb2\.py|\.d\.ts)$")


@dataclass
class Function:
    path: str
    symbol: str
    language: str
    line_start: int
    line_end: int
    complexity: int
    shape: list[str] = field(default_factory=list, repr=False)
    body_hash: str = ""
    shape_hash: str = ""
    confidence: str = "exact"
    body_lines: list[int] = field(default_factory=list, repr=False)

    @property
    def key(self) -> str:
        return f"{self.path}::{self.symbol}"

    @property
    def lines(self) -> int:
        return self.line_end - self.line_start + 1


def language_of(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in PY_SUFFIXES:
        return "python"
    if suffix in JS_SUFFIXES:
        return "typescript" if suffix in {".ts", ".tsx", ".mts", ".cts"} else "javascript"
    return None


def eligible(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    return language_of(normalized) is not None and not (set(parts[:-1]) & SKIP_PARTS) and not GENERATED.search(normalized)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


# ---------- Python

DECISIONS = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _py_complexity(node: ast.AST) -> int:
    total = 1

    def visit(current: ast.AST, top: bool) -> None:
        nonlocal total
        if not top and isinstance(current, SCOPES):
            return  # вложенная функция или класс считаются отдельно
        if isinstance(current, DECISIONS):
            total += 1
        elif isinstance(current, ast.BoolOp):
            total += len(current.values) - 1
        elif isinstance(current, ast.comprehension):
            total += 1 + len(current.ifs)
        elif isinstance(current, ast.match_case):
            wildcard = isinstance(current.pattern, ast.MatchAs) and current.pattern.pattern is None
            total += 0 if wildcard and current.guard is None else 1
        for child in ast.iter_child_nodes(current):
            visit(child, False)

    visit(node, True)
    return total


def _py_shape(node: ast.AST) -> list[str]:
    """Последовательность типов узлов без имён и значений: переименование переменных отпечаток не меняет."""
    shape: list[str] = []
    body = list(getattr(node, "body", []))
    skip = body[0] if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
        and isinstance(body[0].value.value, str) else None

    def visit(current: ast.AST, top: bool) -> None:
        if current is skip:
            return
        if not top and isinstance(current, SCOPES):
            shape.append("NestedScope")
            return
        if isinstance(current, (ast.Load, ast.Store, ast.Del)):
            return
        label = type(current).__name__
        if isinstance(current, ast.Constant):
            label = "Const:" + type(current.value).__name__
        shape.append(label)
        for child in ast.iter_child_nodes(current):
            if not (top and isinstance(child, ast.arguments)):
                visit(child, False)

    visit(node, True)
    return shape


def _py_body_lines(node: ast.AST) -> list[int]:
    """Строки операторов своего тела: строка def вложенной функции - своя, её тело - уже нет."""
    lines: set[int] = set()
    pending = list(getattr(node, "body", []))
    while pending:
        child = pending.pop()
        if isinstance(child, ast.stmt):
            lines.add(child.lineno)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(child))
    return sorted(lines)


def split_lines(text: str) -> list[str]:
    """Строки так же, как их считают ast и отчёты покрытия: только \n, \r\n и \r (splitlines делит ещё по \f, U+2028)."""
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def python_functions(path: str, text: str) -> list[Function]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    source_lines = split_lines(text)
    found: list[Function] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                start = min([child.lineno, *(item.lineno for item in child.decorator_list)])
                end = child.end_lineno or child.lineno
                shape = _py_shape(child)
                found.append(Function(
                    path=path, symbol=name, language="python", line_start=start, line_end=end,
                    complexity=_py_complexity(child), shape=shape,
                    body_hash=_hash("\n".join(source_lines[start - 1:end])), shape_hash=_hash(" ".join(shape)),
                    body_lines=_py_body_lines(child)))
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


# ---------- JavaScript / TypeScript

KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "else", "do", "try", "with", "new", "typeof",
            "await", "yield", "case", "throw", "delete", "void", "in", "of", "instanceof", "constructor_never"}
JS_FUNCTION = re.compile(r"\bfunction\b\s*\*?\s*([A-Za-z_$][\w$]*)?\s*(?:<[^>{}()]*>)?\s*\(")
JS_ARROW = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=;]{1,200})?=\s*(?:async\s+)?"
    r"(?:<[^>{}()]*>\s*)?(\([^()]*(?:\([^()]*\)[^()]*)*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=;{]{1,200})?=>")
JS_PROPERTY_ARROW = re.compile(
    r"(?m)^\s*(?:readonly\s+|static\s+|private\s+|public\s+|protected\s+)*([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s+)?"
    r"(\([^()]*(?:\([^()]*\)[^()]*)*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=;{]{1,200})?=>")
JS_METHOD = re.compile(
    r"(?m)^\s*(?:(?:public|private|protected|static|async|override|readonly|abstract|get|set)\s+)*\*?\s*"
    r"([A-Za-z_$][\w$]*)\s*(?:<[^>{}()]*>)?\s*\(")
JS_DECISION = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?\?(?!=)")
JS_TOKEN = re.compile(r"[A-Za-z_$][\w$]*|\d[\w.]*|\"|'|`|===|!==|==|!=|<=|>=|=>|&&|\|\||\?\?|\?\.|\+\+|--|[-+*/%<>!=&|^~?:;,.(){}\[\]]")
JS_KEEP = {"if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "return", "throw", "try",
           "catch", "finally", "new", "typeof", "instanceof", "in", "of", "await", "async", "yield", "function", "class",
           "const", "let", "var", "true", "false", "null", "undefined", "this"}


def _match_brace(masked: str, start: int) -> int:
    depth = 0
    for index in range(start, len(masked)):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _skip_parens(masked: str, start: int) -> int:
    depth = 0
    for index in range(start, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _body_after(masked: str, position: int, *, arrow: bool) -> tuple[int, int] | None:
    """Начало и конец тела: блок в фигурных скобках или (для стрелки) выражение до конца оператора."""
    index = position
    length = len(masked)
    while index < length and masked[index] in " \t\r\n":
        index += 1
    if index < length and masked[index] == "{":
        end = _match_brace(masked, index)
        return (index, end) if end > 0 else None
    if not arrow:
        # тип возврата после `)`: идём до первой `{` на том же уровне, не дальше `;`
        depth = 0
        while index < length:
            char = masked[index]
            if char in "(<[":
                depth += 1
            elif char in ")>]":
                depth -= 1
            elif char == ";" and depth <= 0:
                return None
            elif char == "{" and depth <= 0:
                end = _match_brace(masked, index)
                return (index, end) if end > 0 else None
            index += 1
        return None
    depth = 0
    start = index
    while index < length:
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return start, index - 1
            depth -= 1
        elif char in ";," and depth == 0:
            return start, index - 1
        elif char == "\n" and depth == 0 and index > start and masked[start:index].strip():
            following = masked[index:index + 200].lstrip()
            if not following[:1] or following[:1] not in ".?:+-*/&|":
                return start, index - 1
        index += 1
    return start, length - 1


def _js_candidates(masked: str) -> list[tuple[str, int, int, int]]:
    """(имя, начало объявления, начало тела, конец тела) - смещения в тексте."""
    found: dict[int, tuple[str, int, int, int]] = {}
    for match in JS_FUNCTION.finditer(masked):
        close = _skip_parens(masked, match.end() - 1)
        body = _body_after(masked, close, arrow=False) if close > 0 else None
        if body:
            found[body[0]] = (match.group(1) or "<anonymous>", match.start(), *body)
    for pattern in (JS_ARROW, JS_PROPERTY_ARROW):
        for match in pattern.finditer(masked):
            body = _body_after(masked, match.end(), arrow=True)
            if body and body[0] not in found:
                found[body[0]] = (match.group(1), match.start(1), *body)
    for match in JS_METHOD.finditer(masked):
        name = match.group(1)
        if name in KEYWORDS:
            continue
        close = _skip_parens(masked, match.end() - 1)
        if close < 0:
            continue
        body = _body_after(masked, close, arrow=False)
        # вызов `foo(...) {` бывает только у объявления метода; `foo(...);` - вызов, пропускаем
        if body and body[0] not in found and masked[close:body[0]].strip()[:1] in {"", ":"}:
            found[body[0]] = (name, match.start(1), *body)
    return sorted(found.values(), key=lambda item: item[2])


def _js_complexity(segment: str) -> int:
    total = 1 + len(JS_DECISION.findall(segment))
    # тернарный `?`: не `?.`, не `??`, не необязательный параметр `x?:` или `x?)`
    for match in re.finditer(r"\?", segment):
        before = segment[match.start() - 1] if match.start() else ""
        after = segment[match.end():match.end() + 1]
        if before == "?" or after in {"?", ".", ":", ")", ",", "="}:
            continue
        total += 1
    return total


def _js_shape(segment: str) -> list[str]:
    shape = []
    for token in JS_TOKEN.findall(segment):
        if token[0].isalpha() or token[0] in "_$":
            shape.append(token if token in JS_KEEP else "ID")
        elif token[0].isdigit():
            shape.append("NUM")
        elif token in {'"', "'", "`"}:
            shape.append("STR")
        else:
            shape.append(token)
    return shape


def script_functions(path: str, text: str) -> list[Function]:
    from koddi.codeintel.jslex import mask

    try:
        masked = mask(text)
    except Exception:  # noqa: BLE001 - нестандартный синтаксис: функции файла просто не учитываются
        return []
    starts = [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", masked)]
    line_of = lambda offset: bisect.bisect_right(starts, offset)  # noqa: E731
    candidates = _js_candidates(masked)
    language = language_of(path) or "javascript"
    source_lines = split_lines(text)
    result: list[Function] = []
    for name, declared, body_start, body_end in candidates:
        nested = [(start, end) for _, _, start, end in candidates if start > body_start and end <= body_end]
        pieces = []
        cursor = body_start
        for start, end in sorted(nested):
            if start >= cursor:
                pieces.append(masked[cursor:start])
                cursor = end + 1
        pieces.append(masked[cursor:body_end + 1])
        own = " ".join(pieces)
        line_start, line_end = line_of(declared), line_of(body_end)
        shape = _js_shape(own)
        body = [line for line in range(line_of(body_start), line_end + 1)
                if line - 1 < len(source_lines) and masked_line(masked, starts, line).strip() not in {"", "{", "}"}]
        result.append(Function(
            path=path, symbol=name, language=language, line_start=line_start, line_end=line_end,
            complexity=_js_complexity(own), shape=shape,
            body_hash=_hash("\n".join(source_lines[line_start - 1:line_end])), shape_hash=_hash(" ".join(shape)),
            confidence="heuristic", body_lines=body))
    return result


def masked_line(masked: str, starts: list[int], line: int) -> str:
    begin = starts[line - 1] if line - 1 < len(starts) else len(masked)
    end = starts[line] - 1 if line < len(starts) else len(masked)
    return masked[begin:end]


def functions_of(path: str, text: str) -> list[Function]:
    language = language_of(path)
    if language == "python":
        result = python_functions(path, text)
    elif language in {"typescript", "javascript"}:
        result = script_functions(path, text)
    else:
        return []
    # одинаковые имена (перегрузки, getter/setter свойства, условные определения) различаем номером строки
    seen: dict[str, int] = {}
    for item in result:
        seen[item.symbol] = seen.get(item.symbol, 0) + 1
    for item in result:
        if seen[item.symbol] > 1:
            item.symbol = f"{item.symbol}@{item.line_start}"
    return result


def read_source(root: Path, path: str) -> str | None:
    target = root / path
    try:
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            return None
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------- изменённые строки

def changed_lines(before: str | None, after: str) -> set[int]:
    """Номера строк нового текста (с 1), которые добавлены или заменены относительно старого."""
    new_lines = split_lines(after)
    if before is None:
        return set(range(1, len(new_lines) + 1))
    matcher = difflib.SequenceMatcher(a=split_lines(before), b=new_lines, autojunk=False)
    changed: set[int] = set()
    for tag, _a1, _a2, b1, b2 in matcher.get_opcodes():
        if tag in {"replace", "insert"}:
            changed.update(range(b1 + 1, b2 + 1))
        elif tag == "delete":
            # удаление меняет поведение функции, где оно было: метим строки по обе стороны -
            # удалённое в конце функции иначе приписалось бы следующей
            changed.update(line for line in (b1, b1 + 1) if 1 <= line <= len(new_lines))
    return changed


def head_text(root: Path, path: str) -> str | None:
    """Текст файла в HEAD или None (новый файл / не git)."""
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    try:
        proc = subprocess.run(["git", "show", f"HEAD:./{path}"], cwd=root, capture_output=True, timeout=15, check=False,
                              creationflags=HIDDEN_SUBPROCESS)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def touched(functions: list[Function], lines: set[int]) -> list[Function]:
    """Функции, чей собственный код менялся: строка вложенной функции относится к ней, а не к внешней."""
    hit: dict[str, Function] = {}
    for line in lines:
        owners = [item for item in functions if item.line_start <= line <= item.line_end]
        if owners:
            inner = min(owners, key=lambda item: item.lines)
            hit[inner.key] = inner
    return [item for item in functions if item.key in hit]
