"""Инструменты агента поверх codeintel: символы, определения, ссылки, переименование, извлечение и правка символа.

Запись идёт только через `ToolRunner._commit_files` (change_set, хеши, откат), политика и подтверждения
решаются до вызова. Переименование TypeScript дополнительно сверяет ошибки типов до и после и откатывает
правку, если она добавила новые.
"""

from __future__ import annotations

import ast
import re
import textwrap
from typing import TYPE_CHECKING, Any

from koddi.codeintel import typescript as tsl
from koddi.codeintel.pyproject import SymbolError
from koddi.codeintel.python import PyFile, split_lines
from koddi.codeintel.service import CodeIntel, language

if TYPE_CHECKING:
    from koddi.tools.runner import ToolRunner

BOM = "﻿"
IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")
SPEC = re.compile(r"^[A-Za-z_$][\w$.]*$|^.+\.[A-Za-z0-9]+:{1,2}[\w$.:]+$")


def intel(runner: ToolRunner) -> CodeIntel:
    return CodeIntel(runner._require_workspace(), runner.project_id, runner.cancel_event)


def _target(args: dict[str, Any], key: str = "symbol") -> dict[str, Any]:
    symbol = str(args.get(key) or "").strip() or None
    path = str(args.get("path") or "").strip() or None
    line = args.get("line")
    if not symbol and not (path and line):
        raise SymbolError(f"pass {key} (name, Class.member, path:Class.member or path:LINE:COLUMN) or path with line")
    return {"symbol": symbol, "path": path, "line": int(line) if line else None,
            "column": int(args["column"]) if args.get("column") else None,
            "glob": str(args.get("glob") or "").strip() or None}


def _guard(call):
    try:
        return call()
    except SymbolError as exc:
        return {"ok": False, "error_code": "symbol_error", "error": str(exc), "retryable": True}


# ---------- чтение

def symbol_search(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return _guard(lambda: intel(runner).workspace_symbols(str(args["query"]), limit))


def outline(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    if language(str(args["path"])) == "text":
        # прочие языки: прежняя построчная эвристика
        return runner.code_outline(args)
    return _guard(lambda: intel(runner).document_symbols(str(args["path"])))


def references(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    def run():
        target = _target(args)
        result = intel(runner).references(**target)
        if result.get("provider") == "python_ast" and not args.get("include_candidates", True):
            result.pop("candidates", None)
            result.pop("warnings", None)
        return result

    return _guard(run)


def definition(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    def run():
        target = _target(args)
        service = intel(runner)
        if target["path"] and target["line"]:
            return service.definition(target["path"], target["line"], target["column"])
        resolved = service.resolve_target(symbol=target["symbol"], path=target["path"], glob=target["glob"])
        if resolved["language"] == "python":
            return {"ok": True, "provider": "python_ast", "accuracy": "verified",
                    "definitions": [service.python().describe(resolved["id"])]}
        return service.definition(resolved["path"], resolved["line"], resolved["column"])

    return _guard(run)


# ---------- правки

def _new_errors(before: dict, after: dict) -> list[dict]:
    def key(item):
        return (item.get("path"), item.get("code"), item.get("message"))

    seen = {key(item) for item in before.get("diagnostics", []) if item.get("category") == "error"}
    return [item for item in after.get("diagnostics", []) if item.get("category") == "error" and key(item) not in seen]


def _preview(changes: dict[str, tuple[str, str]]) -> str:
    from koddi.changes.service import unified_diff

    diff = "".join(unified_diff(path, before, after) for path, (before, after) in changes.items())
    return diff if len(diff) <= 40000 else diff[:40000] + "\n... diff truncated"


def _apply(runner: ToolRunner, service: CodeIntel, plan: dict, description: str, *, rollback_on_type_errors: bool) -> dict:
    """Записывает план; для TypeScript сверяет ошибки типов и при новых ошибках откатывает или сообщает."""
    typecheck = plan.get("typecheck_files") or []
    before = service.diagnostics(typecheck) if typecheck else None
    if before is not None and before.get("unchecked"):
        before = None
    result = runner._commit_files(plan["changes"], description)
    if not result.get("ok"):
        return result
    if before is not None:
        after = service.diagnostics(typecheck)
        introduced = _new_errors(before, after)
        if introduced and rollback_on_type_errors:
            rollback = runner.rollback_changes(result["change_set_id"])
            return {"ok": False, "error_code": "type_errors", "rolled_back": bool(rollback.get("rolled_back", rollback.get("ok"))),
                    "error": "the change introduced TypeScript errors and was rolled back", "new_errors": introduced[:30]}
        result["typecheck"] = {"provider": "typescript", "files": len(typecheck), "new_errors": introduced[:30]}
    return result


def rename(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    from koddi.codeintel.refactor import plan_rename

    def run():
        new = str(args.get("new") or "").strip()
        if not IDENTIFIER.match(new):
            raise SymbolError("new must be a valid identifier")
        old = str(args.get("old") or "").strip()
        if old and not args.get("path") and not SPEC.match(old):
            raise SymbolError("refactor.rename renames code symbols; replace arbitrary text with file.search_replace")
        target = _target({**args, "symbol": old}, "old")
        service = intel(runner)
        include = args.get("include_candidates")
        plan = plan_rename(service, new=new, include_candidates=bool(include), **target)
        summary = {key: plan[key] for key in ("provider", "accuracy", "symbol", "new", "edits", "warnings") if key in plan}
        summary["changed"] = sorted(plan["changes"])
        if plan.get("needs_review") and include is None:
            return {"ok": False, "error_code": "needs_review", **summary, "candidates": plan["candidates"],
                    "error": "some uses have an unknown receiver type; call again with include_candidates true "
                             "(rename them too) or false (leave them unchanged)", "retryable": True}
        if args.get("preview"):
            return {"ok": True, "preview": True, **summary, "candidates": plan.get("candidates", []), "diff": _preview(plan["changes"])}
        if not plan["changes"]:
            return {"ok": False, "error_code": "no_changes", "error": "nothing to rename", **summary}
        result = _apply(runner, service, plan, f"Rename {summary['symbol'].get('name')} -> {new}", rollback_on_type_errors=True)
        return {**summary, **result}

    return _guard(run)


def extract(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    from koddi.codeintel.refactor import plan_extract

    def run():
        name = str(args.get("name") or "").strip() or None
        if name is not None and not IDENTIFIER.match(name):
            raise SymbolError("name must be a valid identifier")
        service = intel(runner)
        plan = plan_extract(service, path=str(args["path"]), selection=str(args["selection"]), name=name)
        if plan["provider"] == "typescript" and name and name != "newFunction":
            plan = _rename_new_function(plan, name)
        summary = {key: plan[key] for key in ("provider", "accuracy", "function", "lines", "warnings") if key in plan}
        if args.get("preview"):
            return {"ok": True, "preview": True, **summary, "diff": _preview(plan["changes"])}
        result = _apply(runner, service, plan, f"Extract function {plan['function']}", rollback_on_type_errors=True)
        return {**summary, **result}

    return _guard(run)


def _rename_new_function(plan: dict, name: str) -> dict:
    """TypeScript всегда называет новую функцию newFunction: меняем только появившиеся в правке вхождения."""
    from koddi.codeintel import jslex

    changes = {}
    for path, (before, after) in plan["changes"].items():
        if re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", before):
            raise SymbolError(f"'{name}' is already used in {path}; choose another name")
        if re.search(r"(?<![\w$])newFunction(?![\w$])", before):
            # имя уже было в файле: отличить новые вхождения нельзя, оставляем как назвал TypeScript
            return {**plan, "warnings": [*plan["warnings"], "newFunction already exists in the file; the name was kept"]}
        masked = jslex.mask(after)
        lines = split_lines(after, keepends=True)
        for line, col in sorted(jslex.identifier_positions(masked, "newFunction"), reverse=True):
            text = lines[line - 1]
            lines[line - 1] = text[:col] + name + text[col + len("newFunction"):]
        changes[path] = (before, "".join(lines))
    warnings = [item for item in plan["warnings"] if "newFunction" not in item]
    return {**plan, "changes": changes, "function": name, "warnings": warnings}


def _python_range(text: str, line: int, name: str) -> tuple[int, int, str]:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.lineno == line and node.name == name:
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            return start, node.end_lineno or node.lineno, "class" if isinstance(node, ast.ClassDef) else "function"
    raise SymbolError("only functions, methods and classes can be replaced with code.symbol_edit")


def symbol_edit(runner: ToolRunner, args: dict[str, Any]) -> dict[str, Any]:
    def run():
        service = intel(runner)
        target = _target(args)
        resolved = service.resolve_target(**target)
        if resolved["language"] == "python":
            info = service.python().describe(resolved["id"])
            relative, line, name = info["path"], info["line"], info["name"]
        elif resolved["language"] == "typescript":
            relative, line = resolved["path"], resolved["line"]
            name = service._name_at(relative, line, resolved["column"])
        else:
            raise SymbolError("code.symbol_edit supports Python and TypeScript/JavaScript declarations")
        raw = (service.root / relative).read_bytes().decode("utf-8")
        body = raw[1:] if raw.startswith(BOM) else raw
        if resolved["language"] == "python":
            start, end, kind = _python_range(body, line, name)
        else:
            try:
                symbols = service._ts(relative).call("symbols", {"file": relative}, cancel=service.cancel)["symbols"]
            except tsl.TypeScriptUnavailable as exc:
                raise SymbolError(f"code.symbol_edit for TypeScript needs the language service: {exc}; use file.edit") from exc
            match = next((item for item in symbols if item["line"] == line and item["name"] == name), None)
            if match is None or not match.get("start_line"):
                raise SymbolError(f"declaration {name} at {relative}:{line} was not found by TypeScript")
            start, end, kind = match["start_line"], match["end_line"], match["kind"]
        lines = split_lines(body, keepends=True)
        newline = "\r\n" if lines and lines[start - 1].endswith("\r\n") else "\n"
        indent = re.match(r"[ \t]*", lines[start - 1]).group(0)
        replacement = str(args.get("new_text") or "")
        if args.get("expected_text") is not None and "".join(lines[start - 1:end]).strip() != str(args["expected_text"]).strip():
            raise SymbolError("the declaration differs from expected_text; read it again before replacing")
        if replacement.strip():
            replacement = textwrap.indent(textwrap.dedent(replacement.replace("\r\n", "\n")).strip("\n"), indent,
                                          lambda value: bool(value.strip()))
            replacement = replacement.replace("\n", newline) + newline
        else:
            replacement = ""  # пустой new_text удаляет объявление целиком
        after_body = "".join(lines[:start - 1]) + replacement + "".join(lines[end:])
        if resolved["language"] == "python":
            parsed = PyFile(relative, after_body)
            if parsed.error:
                raise SymbolError(f"the new code does not parse: {parsed.error}")
        after = (BOM if raw.startswith(BOM) else "") + after_body
        plan = {"changes": {relative: (raw, after)},
                "typecheck_files": [relative] if resolved["language"] == "typescript" else []}
        summary = {"symbol": name, "kind": kind, "path": relative, "lines": [start, end]}
        if args.get("preview"):
            return {"ok": True, "preview": True, **summary, "diff": _preview(plan["changes"])}
        # правка тела - часть работы агента: новые ошибки типов показываем, а не откатываем
        return {**summary, **_apply(runner, service, plan, f"Edit symbol {name}", rollback_on_type_errors=False)}

    return _guard(run)
