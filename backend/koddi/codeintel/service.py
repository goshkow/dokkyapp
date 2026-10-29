"""Единый вход к символам кода: Python через собственный семантический разбор, TypeScript/JavaScript через
языковой сервис проекта, остальное - честный текстовый режим.

Каждый ответ содержит `provider` (python_ast, typescript, text) и `accuracy` (verified, approximate) и
предупреждения, если точность ниже обещанной. Ничего не правит: правки собирает `refactor`.
"""

from __future__ import annotations

import fnmatch
import re
import threading
from pathlib import Path

from koddi.codeintel import jslex
from koddi.codeintel import typescript as tsl
from koddi.codeintel.pyproject import PythonProject, SymbolError, occurrence_json
from koddi.policy.paths import WorkspacePaths

MAX_LISTED = 300
POSITION = re.compile(r"^(?P<path>.+?\.[A-Za-z0-9]+):(?P<line>\d+)(?::(?P<col>\d+))?$")
QUALIFIED = re.compile(r"^(?P<path>.+?\.[A-Za-z0-9]+):{1,2}(?P<qual>[A-Za-z_$][\w$.]*)$")


def language(path: str) -> str:
    lower = path.lower()
    if lower.endswith((".py", ".pyi")):
        return "python"
    if tsl.is_script(lower):
        return "typescript"
    return "text"


def parse_spec(spec: str) -> dict:
    """`path:LINE[:COL]`, `path:Qual.name`, `Qual.name` или `name` в аргументы поиска символа."""
    value = str(spec or "").strip().replace("\\", "/")
    if not value:
        raise SymbolError("symbol is required")
    match = POSITION.match(value)
    if match:
        return {"path": match["path"].removeprefix("./"),
                "line": int(match["line"]), "column": int(match["col"]) if match["col"] else None}
    match = QUALIFIED.match(value)
    if match:
        return {"path": match["path"], "qual": match["qual"]}
    if not re.match(r"^[A-Za-z_$][\w$.]*$", value):
        raise SymbolError("symbol must be a name, Class.member, path:Class.member or path:LINE:COLUMN")
    return {"qual": value}


class CodeIntel:
    def __init__(self, root: Path, project_id: int | None = None, cancel: threading.Event | None = None):
        self.root = Path(root).resolve()
        self.project_id = project_id
        self.cancel = cancel
        self.access = WorkspacePaths(self.root)
        self._python: PythonProject | None = None

    # ---------- общие части

    def python(self) -> PythonProject:
        if self._python is None:
            self._python = PythonProject(self.root, self.project_id, cancel=self.cancel)
        return self._python

    def relative(self, value: str) -> str:
        path = self.access.resolve(value, check_ignore=True)
        if not path.is_file():
            raise SymbolError(f"{value} is not a file of the project")
        return path.relative_to(self.root).as_posix()

    def script_files(self) -> list[str]:
        if self.project_id is not None:
            from koddi.storage.db import db

            with db() as conn:
                rows = conn.execute(
                    "SELECT path FROM project_map_files WHERE project_id=? AND lang IN ('typescript','javascript')",
                    (self.project_id,),
                ).fetchall()
            if rows:
                return sorted(row["path"] for row in rows if tsl.is_script(row["path"]))
        from koddi.projectmap.service import walk

        return [relative for relative, _ in walk(self.root) if tsl.is_script(relative)]

    def _ts(self, relative: str) -> tsl.Worker:
        return tsl.worker(self.root, relative, self.project_id)

    def _ts_position(self, relative: str, line: int, column: int) -> dict:
        text = tsl.read_text(self.root / relative)
        return {"file": relative, "line": line, "character": tsl.to_utf16(tsl.line_of(text, line), column - 1)}

    def _ts_location(self, item: dict, texts: dict[str, str]) -> dict:
        absolute = Path(item["file"])
        try:
            relative = absolute.resolve().relative_to(self.root).as_posix()
        except ValueError:
            relative = str(absolute)
        result = {"path": relative, "line": item["line"], "text": item.get("text", "")}
        if not item.get("external") and (self.root / relative).is_file():
            if relative not in texts:
                texts[relative] = tsl.read_text(self.root / relative)
            result["column"] = tsl.from_utf16(tsl.line_of(texts[relative], item["line"]), item["character"]) + 1
        else:
            result["column"] = item["character"] + 1
            result["external"] = True
        return result

    def _first_identifier_column(self, relative: str, line: int) -> int:
        text = tsl.read_text(self.root / relative) if language(relative) != "python" else self.python().file(relative).text
        current = tsl.line_of(text, line)
        found = re.search(r"[A-Za-z_$][\w$]*", current.lstrip())
        if not found:
            raise SymbolError(f"no symbol on {relative}:{line}")
        return len(current) - len(current.lstrip()) + found.start() + 1

    # ---------- определение и подсказка

    def definition(self, path: str, line: int, column: int | None = None) -> dict:
        relative = self.relative(path)
        column = column or self._first_identifier_column(relative, line)
        lang = language(relative)
        if lang == "python":
            project = self.python()
            occurrence, final = project.at(relative, line, column)
            if occurrence is None:
                raise SymbolError(f"no symbol at {relative}:{line}:{column}")
            if final is None:
                hint = "a builtin or a name from an external library" if occurrence.accuracy in {"builtin", "external"} else (
                    "an attribute whose receiver type is unknown" if occurrence.accuracy == "approximate" else "an unresolved name")
                return {"ok": True, "provider": "python_ast", "accuracy": "approximate", "name": occurrence.name,
                        "definitions": [], "warnings": [f"{occurrence.name} is {hint}; no project definition"]}
            binding = project.index.binding(final)
            if binding is not None and binding.kind == "import":
                return {"ok": True, "provider": "python_ast", "accuracy": "verified", "name": occurrence.name,
                        "definitions": [], "external_import": f"{binding.module or ''}{'.' + binding.member if binding.member else ''}",
                        "warnings": ["defined outside the project"]}
            return {"ok": True, "provider": "python_ast", "accuracy": "verified", "name": occurrence.name,
                    "definitions": [project.describe(final)]}
        if lang == "typescript":
            try:
                worker = self._ts(relative)
                result = worker.call("definition", self._ts_position(relative, line, column), cancel=self.cancel)
            except tsl.TypeScriptUnavailable as exc:
                return self._text_definition(relative, line, column, str(exc))
            texts: dict[str, str] = {}
            definitions = []
            for item in result["definitions"]:
                location = self._ts_location(item, texts)
                location.update({key: item.get(key) for key in ("kind", "name", "container") if item.get(key)})
                definitions.append(location)
            output = {"ok": True, "provider": "typescript", "accuracy": "verified", "definitions": definitions}
            if result.get("hover"):
                output["hover"] = {key: value[:1500] for key, value in result["hover"].items() if isinstance(value, str) and value}
            return output
        return self._text_definition(relative, line, column, "no semantic provider for this language")

    def _text_definition(self, relative: str, line: int, column: int, reason: str) -> dict:
        text = tsl.read_text(self.root / relative)
        current = tsl.line_of(text, line)
        found = None
        for match in re.finditer(r"[A-Za-z_$][\w$]*", current):
            if match.start() <= column - 1 < match.end():
                found = match.group(0)
        if not found:
            raise SymbolError(f"no symbol at {relative}:{line}:{column}")
        candidates = self._declarations_named(found)
        return {"ok": True, "provider": "text", "accuracy": "approximate", "name": found, "definitions": candidates[:20],
                "warnings": [reason, "declarations matched by name only; verify before relying on them"]}

    def _declarations_named(self, name: str) -> list[dict]:
        result = []
        for relative in self.script_files():
            try:
                text = tsl.read_text(self.root / relative)
            except OSError:
                continue
            if name not in text:
                continue
            for found, kind, line, col in jslex.declarations(jslex.mask(text)):
                if found == name:
                    result.append({"path": relative, "line": line, "column": col + 1, "kind": kind, "name": name})
        return result

    # ---------- символы

    def document_symbols(self, path: str) -> dict:
        relative = self.relative(path)
        lang = language(relative)
        if lang == "python":
            return {"ok": True, "provider": "python_ast", "accuracy": "verified", "path": relative,
                    "symbols": self.python().document_symbols(relative)[:MAX_LISTED]}
        if lang == "typescript":
            try:
                result = self._ts(relative).call("symbols", {"file": relative}, cancel=self.cancel)
                texts: dict[str, str] = {}
                symbols = []
                for item in result["symbols"][:MAX_LISTED]:
                    location = self._ts_location(item, texts)
                    location.update({"name": item["name"], "kind": item["kind"], "container": item.get("container"),
                                     "start_line": item.get("start_line"), "end_line": item.get("end_line")})
                    location.pop("text", None)
                    symbols.append(location)
                return {"ok": True, "provider": "typescript", "accuracy": "verified", "path": relative, "symbols": symbols}
            except tsl.TypeScriptUnavailable as exc:
                warning = str(exc)
        else:
            warning = "no semantic provider for this language"
        text = tsl.read_text(self.root / relative)
        symbols = [{"name": name, "kind": kind, "line": line, "column": col + 1}
                   for name, kind, line, col in jslex.declarations(jslex.mask(text))] if lang == "typescript" else []
        return {"ok": True, "provider": "text", "accuracy": "approximate", "path": relative, "symbols": symbols[:MAX_LISTED],
                "warnings": [warning]}

    def workspace_symbols(self, query: str, limit: int = 50) -> dict:
        query = str(query or "").strip()
        if not query or len(query) > 200:
            raise SymbolError("query must be 1-200 characters")
        lowered = query.casefold()
        results: list[dict] = []
        warnings: list[str] = []
        providers = set()
        project = self.python()
        for relative in self._python_candidates(query):
            if self.cancel is not None and self.cancel.is_set():
                raise SymbolError("operation cancelled")
            parsed = project.files[relative]
            for binding in parsed.bindings.values():
                if binding.kind in {"import", "parameter"} or (binding.kind in {"variable", "attribute"} and binding.scope and not binding.owner):
                    continue
                if lowered in binding.name.casefold():
                    results.append({"name": binding.name, "kind": binding.kind, "path": relative, "line": binding.line,
                                    "column": binding.col + 1, "container": binding.scope or None,
                                    "exact": binding.name == query})
                    providers.add("python_ast")
        scripts = self.script_files()
        if scripts:
            seen_configs: set[str] = set()
            for relative in scripts:
                config = self._config_dir(relative)
                if config in seen_configs:
                    continue
                seen_configs.add(config)
                try:
                    found = self._ts(relative).call("workspace_symbols", {"query": query, "file": relative}, cancel=self.cancel)
                except tsl.TypeScriptUnavailable as exc:
                    warnings.append(str(exc))
                    results.extend(self._text_symbols(lowered, scripts))
                    providers.add("text")
                    break
                texts: dict[str, str] = {}
                for item in found["symbols"]:
                    location = self._ts_location(item, texts)
                    location.update({"name": item["name"], "kind": item["kind"], "container": item.get("container"),
                                     "exact": item["name"] == query})
                    location.pop("text", None)
                    results.append(location)
                providers.add("typescript")
                if len(seen_configs) >= 6:
                    break
        unique = {(item["path"], item["line"], item["name"]): item for item in results}
        ordered = sorted(unique.values(), key=lambda item: (not item.get("exact"), len(item["name"]), item["path"], item["line"]))
        accuracy = "approximate" if "text" in providers or not providers else "verified"
        return {"ok": True, "provider": ",".join(sorted(providers)) or "none", "accuracy": accuracy,
                "symbols": ordered[:limit], "truncated": len(ordered) > limit, **({"warnings": sorted(set(warnings))} if warnings else {})}

    def _python_candidates(self, query: str) -> list[str]:
        pattern = query.casefold().encode("utf-8")
        found = []
        for relative in self.python().paths:
            try:
                data = (self.root / relative).read_bytes()
            except OSError:
                continue
            if len(data) <= 512 * 1024 and pattern in data.lower():
                found.append(relative)
        return found

    def _text_symbols(self, lowered: str, scripts: list[str]) -> list[dict]:
        result = []
        for relative in scripts:
            try:
                text = tsl.read_text(self.root / relative)
            except OSError:
                continue
            if lowered not in text.casefold():
                continue
            for name, kind, line, col in jslex.declarations(jslex.mask(text)):
                if lowered in name.casefold():
                    result.append({"name": name, "kind": kind, "path": relative, "line": line, "column": col + 1,
                                   "accuracy": "approximate"})
        return result

    def _config_dir(self, relative: str) -> str:
        directory = (self.root / relative).parent
        while True:
            if (directory / "tsconfig.json").is_file() or (directory / "jsconfig.json").is_file():
                return str(directory)
            if directory == self.root or self.root not in directory.parents:
                return str(self.root)
            directory = directory.parent

    # ---------- ссылки

    def resolve_target(self, *, symbol: str | None = None, path: str | None = None, line: int | None = None,
                       column: int | None = None, glob: str | None = None) -> dict:
        """Точка объявления символа: язык, файл и позиция либо объявление Python; неоднозначность - ошибка с кандидатами."""
        spec = parse_spec(symbol) if symbol and not path else {}
        path = path or spec.get("path")
        line = line or spec.get("line")
        column = column or spec.get("column")
        if path and line:
            relative = self.relative(path)
            column = column or self._first_identifier_column(relative, line)
            lang = language(relative)
            if lang == "python":
                occurrence, final = self.python().at(relative, line, column)
                if final is None or final.endswith("::"):
                    raise SymbolError(f"no project symbol at {relative}:{line}:{column}")
                if self.python().index.binding(final) is None or self.python().index.binding(final).kind == "import":
                    raise SymbolError(f"{occurrence.name} at {relative}:{line} is defined outside the project")
                return {"language": "python", "id": final}
            return {"language": lang, "path": relative, "line": line, "column": column}
        qual = spec.get("qual") or (symbol or "")
        scope_paths = None
        if path:
            relative = self.relative(path)
            if language(relative) == "python":
                ids = self.python().by_name(f"{relative}:{qual}")
                return self._single(ids, qual)
            return self._script_by_name(qual, [relative])
        if glob:
            scope_paths = [item for item in self.python().paths if _glob_match(item, glob)]
        ids = self.python().by_name(qual, scope_paths)
        scripts = self.script_files()
        script_matches = self._script_matches(qual, [item for item in scripts if not glob or _glob_match(item, glob)]) if scripts else []
        if ids and not script_matches:
            return self._single(ids, qual)
        if script_matches and not ids:
            if len(script_matches) == 1:
                return script_matches[0]
            raise SymbolError(_ambiguous(qual, [f"{item['path']}:{item['line']}:{item['column']}" for item in script_matches]))
        if not ids and not script_matches:
            raise SymbolError(f"no declaration named '{qual}' found in the project")
        listed = [self.python().describe(item) for item in ids]
        raise SymbolError(_ambiguous(qual, [f"{item['path']}:{item['line']}:{item['column']}" for item in listed]
                                     + [f"{item['path']}:{item['line']}:{item['column']}" for item in script_matches]))

    def _single(self, ids: list[str], qual: str) -> dict:
        if not ids:
            raise SymbolError(f"no declaration named '{qual}' found")
        if len(ids) > 1:
            listed = [self.python().describe(item) for item in ids]
            raise SymbolError(_ambiguous(qual, [f"{item['path']}:{item['line']}:{item['column']} "
                                                f"({item['kind']}{' in ' + item['container'] if item.get('container') else ''})"
                                                for item in listed]))
        return {"language": "python", "id": ids[0]}

    def _script_matches(self, qual: str, scripts: list[str]) -> list[dict]:
        name = qual.split(".")[-1]
        container = qual.split(".")[-2] if "." in qual else None
        found = []
        for relative in scripts:
            try:
                text = tsl.read_text(self.root / relative)
            except OSError:
                continue
            if name not in text:
                continue
            masked = jslex.mask(text)
            for declared, _kind, line, col in jslex.declarations(masked):
                if declared == name and container is None:
                    found.append({"language": "typescript", "path": relative, "line": line, "column": col + 1})
            if container is not None:
                # метод класса: Class.method -> объявление внутри тела класса
                head = re.search(r"\bclass\s+" + re.escape(container) + r"\b[^{]*\{", masked)
                if head:
                    body_start = head.end()
                    depth, index = 1, body_start
                    while index < len(masked) and depth:
                        depth += {"{": 1, "}": -1}.get(masked[index], 0)
                        index += 1
                    for match in re.finditer(r"(?m)^\s*(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*(" + re.escape(name) + r")\s*[(<:=?!]", masked[body_start:index]):
                        offset = body_start + match.start(1)
                        line = masked.count("\n", 0, offset) + 1
                        col = offset - (masked.rfind("\n", 0, offset) + 1)
                        found.append({"language": "typescript", "path": relative, "line": line, "column": col + 1})
        return found

    def _script_by_name(self, qual: str, scripts: list[str]) -> dict:
        found = self._script_matches(qual, scripts)
        if len(found) == 1:
            return found[0]
        if not found:
            raise SymbolError(f"no declaration named '{qual}' found in {', '.join(scripts)}")
        raise SymbolError(_ambiguous(qual, [f"{item['path']}:{item['line']}:{item['column']}" for item in found]))

    def references(self, **target) -> dict:
        try:
            resolved = self.resolve_target(**target)
        except SymbolError as exc:
            spec = target.get("symbol") or ""
            if "ambiguous" in str(exc) or not spec or not re.match(r"^[A-Za-z_$][\w$]*$", spec):
                raise
            return self._text_references(spec, str(exc), glob=target.get("glob"))
        if resolved["language"] == "python":
            project = self.python()
            refs = project.references(resolved["id"])
            info = project.describe(resolved["id"])
            verified = refs["verified"]
            candidates = [item for item in refs["candidates"] if item.accuracy == "approximate"] if info["kind"] in {"method", "attribute"} else []
            return {
                "ok": True, "provider": "python_ast", "accuracy": "verified", "symbol": info,
                "references": [occurrence_json(item, project.files[item.path]) for item in verified[:MAX_LISTED]],
                "count": len(verified),
                "candidates": [occurrence_json(item, project.files[item.path]) for item in candidates[:50]],
                "truncated": len(verified) > MAX_LISTED,
                **({"warnings": [f"{len(candidates)} attribute uses with unknown receiver type are listed as candidates, not references"]} if candidates else {}),
            }
        if resolved["language"] == "typescript":
            relative = resolved["path"]
            try:
                result = self._ts(relative).call("references", self._ts_position(relative, resolved["line"], resolved["column"]), cancel=self.cancel)
            except tsl.TypeScriptUnavailable as exc:
                name = self._name_at(relative, resolved["line"], resolved["column"])
                return self._text_references(name, str(exc))
            texts: dict[str, str] = {}
            references = []
            for item in result["references"]:
                location = self._ts_location(item, texts)
                location["role"] = "definition" if item.get("definition") else ("write" if item.get("write") else "read")
                location["accuracy"] = "verified"
                references.append(location)
            return {"ok": True, "provider": "typescript", "accuracy": "verified",
                    "symbol": {"name": self._name_at(relative, resolved["line"], resolved["column"]), "path": relative, "line": resolved["line"]},
                    "references": references[:MAX_LISTED], "count": len(references),
                    "truncated": result.get("truncated") or len(references) > MAX_LISTED}
        name = self._name_at(resolved["path"], resolved["line"], resolved["column"])
        return self._text_references(name, "no semantic provider for this language")

    def _name_at(self, relative: str, line: int, column: int) -> str:
        current = tsl.line_of(tsl.read_text(self.root / relative), line)
        for match in re.finditer(r"[A-Za-z_$][\w$]*", current):
            if match.start() <= column - 1 < match.end():
                return match.group(0)
        raise SymbolError(f"no symbol at {relative}:{line}:{column}")

    def _text_references(self, name: str, reason: str, glob: str | None = None) -> dict:
        """Совпадения имени в коде без комментариев и строк: только приблизительно."""
        references = []
        files = 0
        for relative in [*self.python().paths, *self.script_files()]:
            if glob and not _glob_match(relative, glob):
                continue
            if self.cancel is not None and self.cancel.is_set():
                raise SymbolError("operation cancelled")
            try:
                text = tsl.read_text(self.root / relative)
            except OSError:
                continue
            if name not in text or len(text) > 512 * 1024:
                continue
            files += 1
            if language(relative) == "python":
                parsed = self.python().files[relative]
                positions = [(item.line, item.col) for item in parsed.occurrences if item.name == name]
            else:
                positions = jslex.identifier_positions(jslex.mask(text), name)
            for line, col in positions:
                references.append({"path": relative, "line": line, "column": col + 1, "accuracy": "approximate",
                                   "text": tsl.line_of(text, line).strip()[:200]})
        return {"ok": True, "provider": "text", "accuracy": "approximate", "symbol": {"name": name},
                "references": references[:MAX_LISTED], "count": len(references), "files": files,
                "truncated": len(references) > MAX_LISTED,
                "warnings": [reason, "matches by name outside comments and strings; not proven to be the same symbol"]}

    # ---------- диагностика

    def diagnostics(self, paths: list[str]) -> dict:
        """Ошибки типов TypeScript и синтаксиса Python для перечисленных файлов (без запуска команд проекта)."""
        python_files = [item for item in paths if language(item) == "python"]
        scripts = [item for item in paths if language(item) == "typescript"]
        result: list[dict] = []
        providers = []
        for relative in python_files:
            parsed = self.python().files.get(relative) if relative in self.python().files else None
            if parsed is None:
                from koddi.codeintel.pyproject import load

                parsed = load(self.root, relative)
            if parsed is not None and parsed.error:
                result.append({"path": relative, "category": "error", "message": parsed.error})
        if python_files:
            providers.append("python_ast")
        unavailable = None
        if scripts:
            try:
                found = self._ts(scripts[0]).call("diagnostics", {"files": scripts}, cancel=self.cancel)
                texts: dict[str, str] = {}
                for item in found["diagnostics"]:
                    location = self._ts_location(item, texts) if item.get("line") else {"path": item.get("file")}
                    location.pop("text", None)
                    location.update({"code": item.get("code"), "category": item.get("category"), "message": item.get("message")})
                    result.append(location)
                providers.append("typescript")
            except tsl.TypeScriptUnavailable as exc:
                unavailable = str(exc)
        output = {"ok": True, "provider": ",".join(providers) or "none", "diagnostics": result,
                  "errors": sum(1 for item in result if item.get("category") == "error")}
        if unavailable:
            output["warnings"] = [f"TypeScript files were not type-checked: {unavailable}"]
            output["unchecked"] = scripts
        return output


def _glob_match(relative: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(Path(relative).name, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:]))


def _ambiguous(name: str, places: list[str]) -> str:
    listed = "; ".join(places[:12]) + (f"; and {len(places) - 12} more" if len(places) > 12 else "")
    return f"'{name}' is ambiguous: {listed}. Pass path:LINE:COLUMN (or path:Class.member) to choose one"
