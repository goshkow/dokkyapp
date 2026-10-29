"""Подготовка правок рефакторинга: переименование и извлечение функции.

Функции возвращают план: исходный и новый текст каждого файла, число правок, предупреждения и кандидатов.
Запись делает ToolRunner через обычный change_set (атомарно, с проверкой хешей и откатом), здесь файлы
не пишутся. Любая неуверенность - отказ с объяснением, а не текстовая замена.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from koddi.codeintel import typescript as tsl
from koddi.codeintel.pyproject import PythonProject, SymbolError, apply_edits, occurrence_json
from koddi.codeintel.python import COMPREHENSIONS, PyFile, is_identifier, split_lines
from koddi.codeintel.service import CodeIntel, language

BOM = "﻿"


def _raw(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        # правка с заменой символов испортила бы файл - отказываемся
        raise SymbolError(f"{relative} is not valid UTF-8; nothing was changed") from exc
    except OSError as exc:
        raise SymbolError(f"cannot read {relative}: {exc.strerror or exc}") from exc


def _with_bom(raw: str, transform) -> str:
    """Разбор Python идёт без BOM: правки применяются к телу, BOM возвращается на место."""
    if raw.startswith(BOM):
        return BOM + transform(raw[1:])
    return transform(raw)


# ---------- переименование

def plan_rename(intel: CodeIntel, *, new: str, symbol: str | None = None, path: str | None = None,
                line: int | None = None, column: int | None = None, glob: str | None = None,
                include_candidates: bool = False) -> dict:
    target = intel.resolve_target(symbol=symbol, path=path, line=line, column=column, glob=glob)
    if target["language"] == "python":
        return _rename_python(intel, target["id"], new, include_candidates)
    if target["language"] == "typescript":
        return _rename_typescript(intel, target, new)
    raise SymbolError("semantic rename is supported for Python and TypeScript/JavaScript; edit other files with file.edit")


def _rename_python(intel: CodeIntel, symbol_id: str, new: str, include_candidates: bool) -> dict:
    project = intel.python()
    plan = project.rename_edits(symbol_id, new)
    target = project.index.binding(symbol_id)
    member = target is not None and target.kind in {"method", "attribute"}
    candidates = [item for item in plan["candidates"] if item.accuracy == "approximate"] if member else []
    edits = {path: list(items) for path, items in plan["edits"].items()}
    if candidates and include_candidates:
        for item in candidates:
            edits.setdefault(item.path, []).append((item.line, item.col, len(plan["old"])))
    changes: dict[str, tuple[str, str]] = {}
    overrides: dict[str, PyFile] = {}
    for relative, items in edits.items():
        raw = _raw(intel.root, relative)
        after = _with_bom(raw, lambda body, items=items: apply_edits(body, items, new))
        parsed = PyFile(relative, after[1:] if after.startswith(BOM) else after)
        if parsed.error:
            raise SymbolError(f"rename would break syntax in {relative}: {parsed.error}")
        changes[relative] = (raw, after)
        overrides[relative] = parsed
    # проверка: после правок новое имя связано ровно с теми же вхождениями, что и старое
    check = PythonProject(intel.root, intel.project_id, cancel=intel.cancel, overrides=overrides, paths=project.paths)
    renamed_total = 0
    for old_id in plan["symbols"]:
        new_id = old_id[: old_id.rfind(".") + 1] + new if "." in old_id.split("::", 1)[1] else old_id.split("::", 1)[0] + "::" + new
        if check.index.binding(new_id) is None:
            raise SymbolError("rename verification failed: the renamed declaration is not found after the edit")
        renamed_total += len([item for item in check.references(new_id, include_candidates=False)["verified"] if item.name == new])
    expected = plan["count"] + (len(candidates) if include_candidates else 0)
    if renamed_total < plan["count"]:
        raise SymbolError(f"rename verification failed: {plan['count']} references before, {renamed_total} after; nothing was changed")
    warnings = list(plan["warnings"])
    if renamed_total != expected and include_candidates:
        warnings.append("some candidate uses still do not resolve to the renamed symbol after the edit; review them")
    return {
        "provider": "python_ast",
        "accuracy": "verified",
        "symbol": project.describe(symbol_id),
        "new": new,
        "changes": changes,
        "edits": sum(len(items) for items in edits.values()),
        "warnings": warnings,
        "candidates": [] if include_candidates else [occurrence_json(item, project.files[item.path]) for item in candidates[:50]],
        "needs_review": bool(candidates) and not include_candidates,
    }


def _rename_typescript(intel: CodeIntel, target: dict, new: str) -> dict:
    if not re.match(r"^[A-Za-z_$][\w$]*$", new):
        raise SymbolError(f"'{new}' is not a valid identifier")
    relative = target["path"]
    try:
        result = intel._ts(relative).call("rename", intel._ts_position(relative, target["line"], target["column"]), cancel=intel.cancel)
    except tsl.TypeScriptUnavailable as exc:
        raise SymbolError(f"semantic rename for TypeScript/JavaScript is unavailable: {exc}. Nothing was changed; "
                          "use code.references for approximate matches and edit them with file.edit") from exc
    if not result.get("can_rename"):
        raise SymbolError(result.get("error") or "TypeScript cannot rename this symbol")
    by_file: dict[str, list[dict]] = {}
    for item in result["locations"]:
        absolute = Path(item["file"]).resolve()
        if not absolute.is_relative_to(intel.root) or "node_modules" in absolute.parts:
            raise SymbolError(f"the symbol is also declared outside the project ({item['file']}); rename it there first")
        by_file.setdefault(absolute.relative_to(intel.root).as_posix(), []).append(item)
    old = result.get("name") or ""
    changes = {}
    count = 0
    for file, items in by_file.items():
        raw = _raw(intel.root, file)
        lines, breaks = tsl.split_keep(raw)
        for item in sorted(items, key=lambda value: (value["line"], value["character"]), reverse=True):
            if item["line"] != item["end_line"] or not 0 < item["line"] <= len(lines):
                raise SymbolError("unexpected rename location; nothing was changed")
            text = lines[item["line"] - 1]
            start = tsl.from_utf16(text, item["character"])
            end = tsl.from_utf16(text, item["end_character"])
            # файл мог измениться после ответа языкового сервиса: правим только там, где стоит старое имя
            if old and text[start:end] != old:
                raise SymbolError(f"{file}:{item['line']} no longer contains '{old}'; the file changed, nothing was changed")
            lines[item["line"] - 1] = text[:start] + item.get("prefix", "") + new + item.get("suffix", "") + text[end:]
            count += 1
        changes[file] = (raw, tsl.join_keep(lines, breaks))
    return {
        "provider": "typescript",
        "accuracy": "verified",
        "symbol": {"name": old, "path": relative, "line": target["line"], "kind": result.get("kind")},
        "new": new,
        "changes": changes,
        "edits": count,
        "warnings": [],
        "candidates": [],
        "needs_review": False,
        "typecheck_files": sorted(changes),
    }


# ---------- извлечение функции

SELECTION = re.compile(r"^\s*(?:L)?(\d+)\s*(?:-|\.\.|:)\s*(?:L)?(\d+)\s*$")


def parse_selection(selection: str, text: str) -> tuple[int, int]:
    """Строки выделения: `START-END` (с 1, включительно) или точный фрагмент кода, встречающийся один раз."""
    match = SELECTION.match(selection or "")
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        if start < 1 or end < start:
            raise SymbolError("selection lines must be START-END with START <= END")
        return start, end
    fragment = (selection or "").strip("\n")
    if not fragment.strip():
        raise SymbolError("selection is empty")
    count = text.count(fragment)
    if count != 1:
        raise SymbolError("selection text must occur exactly once; pass START-END lines instead" if count else "selection text was not found")
    offset = text.index(fragment)
    start = text.count("\n", 0, offset) + 1
    return start, start + fragment.count("\n")


def plan_extract(intel: CodeIntel, *, path: str, selection: str, name: str | None = None) -> dict:
    relative = intel.relative(path)
    lang = language(relative)
    raw = _raw(intel.root, relative)
    body = raw[1:] if raw.startswith(BOM) else raw
    start, end = parse_selection(selection, body)
    if lang == "python":
        new_name = name or "extracted"
        after = extract_python(body, start, end, new_name)
        return {"provider": "python_ast", "accuracy": "verified", "changes": {relative: (raw, (BOM if raw.startswith(BOM) else "") + after)},
                "function": new_name, "lines": [start, end], "warnings": []}
    if lang == "typescript":
        lines, _ = tsl.split_keep(body)
        if end > len(lines):
            raise SymbolError("selection is outside the file")
        first = lines[start - 1]
        last = lines[end - 1]
        args = {
            "file": relative, "line": start, "character": tsl.to_utf16(first, len(first) - len(first.lstrip())),
            "end_line": end, "end_character": tsl.to_utf16(last, len(last)),
        }
        try:
            result = intel._ts(relative).call("extract", args, cancel=intel.cancel)
        except tsl.TypeScriptUnavailable as exc:
            raise SymbolError(f"extract for TypeScript/JavaScript needs the TypeScript language service: {exc}") from exc
        if not result.get("ok"):
            raise SymbolError(result.get("error") or "TypeScript cannot extract this selection")
        changes = {}
        for item in result["edits"]:
            absolute = Path(item["file"]).resolve()
            if not absolute.is_relative_to(intel.root):
                raise SymbolError("extract edits a file outside the project")
            file = absolute.relative_to(intel.root).as_posix()
            original = _raw(intel.root, file)
            changes[file] = (original, tsl.apply_utf16_changes(original, item["changes"]))
        warnings = []
        if name and name.strip():
            warnings.append("TypeScript names the new function itself (newFunction); rename it with refactor.rename")
        return {"provider": "typescript", "accuracy": "verified", "changes": changes, "function": "newFunction",
                "lines": [start, end], "warnings": warnings, "typecheck_files": sorted(changes), "description": result.get("description")}
    raise SymbolError("extract is supported for Python and TypeScript/JavaScript")


def _statements_in(body: list[ast.stmt], start: int, end: int) -> tuple[list[ast.stmt], list[ast.stmt]] | None:
    """Последовательность операторов одного блока, точно покрывающая строки выделения."""
    for index, statement in enumerate(body):
        first_line = min([statement.lineno, *[d.lineno for d in getattr(statement, "decorator_list", [])]])
        if first_line > end or statement.end_lineno < start:
            continue
        if first_line >= start and statement.end_lineno <= end:
            chosen = []
            for item in body[index:]:
                item_start = min([item.lineno, *[d.lineno for d in getattr(item, "decorator_list", [])]])
                if item_start >= start and item.end_lineno <= end:
                    chosen.append(item)
                else:
                    break
            covered_end = chosen[-1].end_lineno
            if first_line == start and covered_end == end:
                return chosen, body
            return None
        # выделение внутри составного оператора: ищем во вложенных блоках
        for field in ("body", "orelse", "finalbody", "handlers"):
            inner = getattr(statement, field, None)
            if isinstance(inner, list) and inner:
                if field == "handlers":
                    for handler in inner:
                        found = _statements_in(handler.body, start, end)
                        if found:
                            return found
                    continue
                found = _statements_in(inner, start, end)
                if found:
                    return found
        if isinstance(statement, ast.Match):
            for case in statement.cases:
                found = _statements_in(case.body, start, end)
                if found:
                    return found
        return None
    return None


def _string_continuations(text: str) -> set[int]:
    """Строки файла, которые продолжают многострочный строковый литерал: их отступ трогать нельзя."""
    result: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.STRING and token.end[0] > token.start[0]:
                result.update(range(token.start[0] + 1, token.end[0] + 1))
            elif token.type == getattr(tokenize, "FSTRING_MIDDLE", -1) and token.end[0] > token.start[0]:
                result.update(range(token.start[0] + 1, token.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return result


def extract_python(text: str, start: int, end: int, name: str) -> str:
    if not is_identifier(name):
        raise SymbolError(f"'{name}' is not a valid Python identifier")
    parsed = PyFile("<extract>", text)
    if parsed.error:
        raise SymbolError(f"the file has a syntax error: {parsed.error}")
    # самая внутренняя функция, содержащая выделение; иначе модуль
    enclosing = parsed.module
    for scope in parsed.scopes:
        node = scope.node
        if scope.kind == "function" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body[0].lineno <= start and node.end_lineno >= end:
            if enclosing is parsed.module or node.lineno >= enclosing.node.lineno:
                enclosing = scope
        if scope.kind == "class" and node.body[0].lineno <= start and node.end_lineno >= end and enclosing is parsed.module:
            enclosing = scope
    if enclosing.kind == "class":
        raise SymbolError("statements directly in a class body cannot be extracted; select lines inside a method")
    container = enclosing.node.body if enclosing.kind == "function" else parsed.tree.body
    found = _statements_in(container, start, end)
    if found is None:
        raise SymbolError("the selection must cover whole statements of one block (no partial statements)")
    statements, _block = found
    selected_nodes = [node for statement in statements for node in ast.walk(statement)]
    if enclosing.kind == "function" and enclosing.nonlocals:
        raise SymbolError("functions with nonlocal declarations are not extracted automatically")
    is_async = False
    for node in selected_nodes:
        if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) and _owner_function(node, statements) is None:
            raise SymbolError("the selection contains return/yield of the enclosing function; it cannot be extracted as is")
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)) and _owner_function(node, statements) is None:
            is_async = True
        if isinstance(node, (ast.Break, ast.Continue)) and not _inside_loop(node, statements):
            raise SymbolError("the selection contains break/continue of a loop outside it")
        if isinstance(node, ast.Nonlocal):
            raise SymbolError("nonlocal declarations cannot be extracted")
    ancestors = set()
    current = enclosing.parent
    while current is not None and current.kind != "module":
        if current.kind == "function":
            ancestors.add(current.qual)
        current = current.parent
    selected = range(start, end + 1)
    inside, outside = [], []
    for occurrence in parsed.occurrences:
        if occurrence.binding is None or occurrence.role not in {"read", "write", "definition", "import"}:
            continue
        binding = parsed.bindings.get(occurrence.binding)
        if binding is None or binding.owner:
            continue
        if enclosing.kind == "function":
            relevant = binding.scope == enclosing.qual or binding.scope in ancestors
        else:
            relevant = binding.scope == ""
        if relevant:
            (inside if occurrence.line in selected else outside).append((occurrence, binding))
    first_event: dict[str, str] = {}
    written: set[str] = set()
    read: set[str] = set()
    free: set[str] = set()
    relevant_names = set()
    for occurrence, binding in inside:
        role = "write" if occurrence.role in {"write", "definition", "import"} else "read"
        (written if role == "write" else read).add(binding.name)
        relevant_names.add(binding.name)
        if enclosing.kind == "function" and binding.scope != enclosing.qual:
            free.add(binding.name)
    for event_name, role in _events(statements):
        if event_name in relevant_names:
            first_event.setdefault(event_name, role)
    used_outside = {binding.name for occurrence, binding in outside if occurrence.role == "read"}
    bound_before = {binding.name for occurrence, binding in outside
                    if occurrence.line < start and occurrence.role in {"write", "definition", "import"}}
    globals_declared = enclosing.globals
    inputs_set: set[str] = set()
    for name_ in read | written:
        if name_ in globals_declared:
            continue
        if enclosing.kind == "module":
            # имя модуля, которое выделение и читает, и присваивает, в функции стало бы локальным до присваивания
            if name_ in written and first_event.get(name_) == "read":
                inputs_set.add(name_)
        elif name_ in free:
            if name_ in read:
                inputs_set.add(name_)
        elif name_ in read and (first_event.get(name_) == "read" or name_ in bound_before):
            inputs_set.add(name_)
        elif name_ in written and name_ in bound_before:
            inputs_set.add(name_)
    # параметры в порядке объявления имён: аргументы функции - как в её сигнатуре, затем локальные
    declared = {}
    for occurrence, binding in [*outside, *inside]:
        declared.setdefault(binding.name, (binding.line, binding.col))
    inputs = sorted(inputs_set, key=lambda item: (declared.get(item, (0, 0)), item))
    outputs = sorted((name_ for name_ in written if name_ in used_outside and name_ not in globals_declared and name_ not in free),
                     key=lambda item: (declared.get(item, (0, 0)), item))
    global_writes = sorted(name_ for name_ in written if name_ in globals_declared)
    method = (enclosing.kind == "function" and enclosing.cls is not None and enclosing.self_name in inputs)
    if method:
        inputs = [item for item in inputs if item != enclosing.self_name]
    module_names = parsed.module.names
    if method:
        class_scope = parsed.scope_for(enclosing.cls)
        if name in (class_scope.names if class_scope else {}):
            raise SymbolError(f"class already has a member '{name}'")
    elif name in module_names:
        raise SymbolError(f"'{name}' is already defined in the module")
    file_lines = split_lines(text, keepends=True)
    continuation = _string_continuations(text)
    first_statement = statements[0]
    first_line = min([first_statement.lineno, *[d.lineno for d in getattr(first_statement, "decorator_list", [])]])
    base_indent = re.match(r"[ \t]*", file_lines[first_line - 1]).group(0)
    newline = "\r\n" if file_lines and file_lines[0].endswith("\r\n") else "\n"
    if method:
        method_node = enclosing.node
        def_indent = re.match(r"[ \t]*", file_lines[method_node.lineno - 1]).group(0)
    elif enclosing.kind == "function":
        top = _top_level_statement(parsed.tree, enclosing.node)
        def_indent = ""
    else:
        top = first_statement
        def_indent = ""
    unit = _indent_unit(file_lines) or "    "
    body_indent = def_indent + unit
    body_lines = []
    for number in range(start, end + 1):
        line = file_lines[number - 1]
        if number in continuation or not line.strip():
            body_lines.append(line if line.strip() else newline)
            continue
        if not line.startswith(base_indent) and line.lstrip().startswith("#"):
            body_lines.append(body_indent + line.lstrip())
            continue
        if not line.startswith(base_indent):
            raise SymbolError("inconsistent indentation inside the selection")
        body_lines.append(body_indent + line[len(base_indent):])
    if global_writes:
        body_lines.insert(0, f"{body_indent}global {', '.join(global_writes)}{newline}")
    if outputs:
        body_lines.append(f"{body_indent}return {', '.join(outputs)}{newline}")
    params = ([enclosing.self_name] if method else []) + inputs
    header = f"{def_indent}{'async ' if is_async else ''}def {name}({', '.join(params)}):{newline}"
    function_text = header + "".join(body_lines)
    call = f"{'self.' if method else ''}{name}({', '.join(inputs)})"
    if is_async:
        call = "await " + call
    if outputs:
        call = f"{', '.join(outputs)} = {call}"
    replacement = f"{base_indent}{call}{newline}"
    new_lines = file_lines[: first_line - 1] + [replacement] + file_lines[end:]
    if method:
        insert_at = method_node.end_lineno - (end - first_line)  # строка после метода в новом тексте
        block = [newline, function_text]
        new_lines = new_lines[:insert_at] + block + new_lines[insert_at:]
    else:
        top_line = min([top.lineno, *[d.lineno for d in getattr(top, "decorator_list", [])]])
        insert_at = top_line - 1
        block = [function_text, newline, newline]
        if insert_at > 0 and new_lines[insert_at - 1].strip():
            block = [newline, newline, *block]
        new_lines = new_lines[:insert_at] + block + new_lines[insert_at:]
    result = "".join(new_lines)
    check = PyFile("<extract>", result)
    if check.error:
        raise SymbolError(f"extraction would produce invalid code: {check.error}")
    return result


def _events(statements: list[ast.stmt]) -> list[tuple[str, str]]:
    """Чтения и записи имён в порядке вычисления: значение присваивания раньше цели, `+=` читает до записи."""
    events: list[tuple[str, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Assign):
            visit(node.value)
            for target in node.targets:
                visit(target)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                events.append((node.target.id, "read"))
            visit(node.value)
            visit(node.target)
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                visit(node.value)
            visit(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            visit(node.iter)
            visit(node.target)
            for child in [*node.body, *node.orelse]:
                visit(child)
        elif isinstance(node, ast.NamedExpr):
            visit(node.value)
            visit(node.target)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for child in [*node.decorator_list, *getattr(node, "bases", [])]:
                visit(child)
            # тело вложенной функции читает имена при вызове, но для выделения это чтение
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    events.append((child.id, "read"))
            events.append((node.name, "write"))
        elif isinstance(node, ast.Name):
            events.append((node.id, "read" if isinstance(node.ctx, ast.Load) else "write"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                events.append(((alias.asname or alias.name.split(".")[0]), "write"))
        else:
            for child in ast.iter_child_nodes(node):
                visit(child)

    for statement in statements:
        visit(statement)
    return events


def _arguments(node: ast.AST) -> list[ast.arg]:
    from koddi.codeintel.python import all_args

    return all_args(node)


def _free(scope, name: str) -> bool:
    current = scope.parent
    while current is not None and current.kind != "module":
        if current.kind == "function" and name in current.names:
            return True
        current = current.parent
    return False


def _owner_function(node: ast.AST, statements: list[ast.stmt]) -> ast.AST | None:
    """Вложенная функция или лямбда внутри выделения, которой принадлежит узел (return внутри неё допустим)."""
    for statement in statements:
        for candidate in ast.walk(statement):
            if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, *COMPREHENSIONS)) and candidate is not node:
                if any(child is node for child in ast.walk(candidate)):
                    return candidate
    return None


def _inside_loop(node: ast.AST, statements: list[ast.stmt]) -> bool:
    for statement in statements:
        for candidate in ast.walk(statement):
            if isinstance(candidate, (ast.For, ast.AsyncFor, ast.While)) and any(child is node for child in ast.walk(candidate) if child is not candidate):
                return True
    return False


def _top_level_statement(tree: ast.Module, node: ast.AST) -> ast.stmt:
    for statement in tree.body:
        if any(child is node for child in ast.walk(statement)):
            return statement
    raise SymbolError("enclosing definition not found")


def _indent_unit(lines: list[str]) -> str:
    for line in lines:
        match = re.match(r"^([ \t]+)\S", line)
        if match:
            return match.group(1) if match.group(1).startswith("\t") else " " * min(len(match.group(1)), 4)
    return ""
