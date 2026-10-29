"""Операции с символами Python в пределах проекта: определение, ссылки, символы, подсказка, переименование."""

from __future__ import annotations

import ast
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from pathlib import Path

from koddi.codeintel.python import Binding, Occurrence, PyFile, PythonIndex, is_identifier, split_lines

MAX_FILE_BYTES = 512 * 1024
CACHE_FILES = 4000
MAX_REFERENCES = 2000
_cache: OrderedDict[tuple[str, str], tuple[int, int, PyFile]] = OrderedDict()
_cache_lock = threading.Lock()


class SymbolError(ValueError):
    pass


def load(root: Path, relative: str) -> PyFile | None:
    """Разбор файла с кэшем по mtime и размеру; большой или нечитаемый файл не разбирается."""
    path = root / relative
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > MAX_FILE_BYTES:
        return None
    key = (str(root), relative)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            _cache.move_to_end(key)
            return cached[2]
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    parsed = PyFile(relative, data.decode("utf-8-sig", errors="replace"))
    with _cache_lock:
        _cache[key] = (stat.st_mtime_ns, stat.st_size, parsed)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_FILES:
            _cache.popitem(last=False)
    return parsed


def parse_text(relative: str, text: str) -> PyFile:
    return PyFile(relative, text)


class LazyFiles(Mapping):
    """Файлы проекта разбираются только при обращении: модули сопоставляются по одним путям."""

    def __init__(self, root: Path, paths: list[str], overrides: dict[str, PyFile] | None = None):
        self.root = root
        self.paths = list(dict.fromkeys(paths))
        self.overrides = overrides or {}
        self._known = set(self.paths) | set(self.overrides)
        self._loaded: dict[str, PyFile | None] = {}

    def __getitem__(self, key: str) -> PyFile:
        if key in self.overrides:
            return self.overrides[key]
        if key not in self._known:
            raise KeyError(key)
        if key not in self._loaded:
            self._loaded[key] = load(self.root, key)
        value = self._loaded[key]
        if value is None:
            # файл есть, но не разбирается: пустой разбор не даёт объявлений
            value = PyFile(key, "")
            self._loaded[key] = value
        return value

    def __iter__(self) -> Iterator[str]:
        yield from self.paths
        yield from (path for path in self.overrides if path not in set(self.paths))

    def __len__(self) -> int:
        return len(self._known)

    def __contains__(self, key: object) -> bool:
        return key in self._known


def python_paths(root: Path, project_id: int | None = None) -> list[str]:
    """Python-файлы проекта: из карты проекта, если она есть, иначе обходом с теми же правилами доступа."""
    if project_id is not None:
        from koddi.storage.db import db

        with db() as conn:
            rows = conn.execute(
                "SELECT path FROM project_map_files WHERE project_id=? AND lang='python'", (project_id,)
            ).fetchall()
        if rows:
            return sorted(row["path"] for row in rows if row["path"].endswith((".py", ".pyi")))
    from koddi.projectmap.service import walk

    return [relative for relative, _ in walk(root) if relative.endswith((".py", ".pyi"))]


def _mentions(root: Path, paths: list[str], name: str, overrides: dict | None = None) -> list[str]:
    pattern = re.compile(rb"(?<![\w])" + re.escape(name.encode("utf-8")) + rb"(?![\w])")
    found = []
    overrides = overrides or {}
    for relative in [*paths, *(item for item in overrides if item not in set(paths))]:
        try:
            # проверочный проект после правок видит новый текст, а не файл на диске
            data = overrides[relative].text.encode("utf-8") if relative in overrides else (root / relative).read_bytes()
        except OSError:
            continue
        if len(data) <= MAX_FILE_BYTES and pattern.search(data):
            found.append(relative)
    return found


class PythonProject:
    def __init__(self, root: Path, project_id: int | None = None, *, cancel: threading.Event | None = None,
                 overrides: dict[str, PyFile] | None = None, paths: list[str] | None = None):
        self.root = root
        self.cancel = cancel
        self.paths = paths if paths is not None else python_paths(root, project_id)
        self.files = LazyFiles(root, self.paths, overrides)
        self.index = PythonIndex(self.files)

    def _check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise SymbolError("operation cancelled")

    # ---------- поиск объявления

    def file(self, relative: str) -> PyFile:
        if relative not in self.files:
            raise SymbolError(f"{relative} is not a Python file of the project")
        parsed = self.files[relative]
        if parsed.error:
            raise SymbolError(f"{relative} has a syntax error: {parsed.error}")
        return parsed

    def at(self, relative: str, line: int, column: int) -> tuple[Occurrence | None, str | None]:
        """Вхождение под курсором (строка и колонка с 1) и его конечное объявление."""
        parsed = self.file(relative)
        best = None
        for occurrence in self.index.resolve(parsed):
            if occurrence.line == line and occurrence.col <= column - 1 < occurrence.col + len(occurrence.name):
                best = occurrence
                if occurrence.role in {"definition", "import"}:
                    break
        if best is None:
            return None, None
        return best, self.index.final(best)

    def by_name(self, spec: str, scope_paths: list[str] | None = None) -> list[str]:
        """Объявления по имени: `name`, `Class.method` или `path.py:Class.method`."""
        path_part, _, qual = spec.rpartition(":") if ":" in spec else ("", "", spec)
        path_part = path_part.rstrip(":")
        parts = qual.split(".")
        name = parts[-1]
        if not is_identifier(name):
            raise SymbolError(f"'{spec}' is not a Python symbol name")
        if path_part:
            candidates = [path_part] if path_part in self.files else []
        else:
            candidates = _mentions(self.root, scope_paths or self.paths, name, self.files.overrides)
        result = []
        for relative in candidates:
            self._check()
            parsed = self.files[relative]
            if parsed.error:
                continue
            for binding in parsed.bindings.values():
                if binding.name != name or binding.kind in {"import", "parameter"}:
                    continue
                if binding.kind in {"variable", "attribute"} and binding.scope and not binding.owner:
                    continue  # локальные переменные функций по одному имени не выбираются
                qualified = binding.id.split("::", 1)[1]
                if len(parts) > 1 and qualified != qual and not qualified.endswith("." + qual):
                    continue
                result.append(binding.id)
        return sorted(set(result))

    # ---------- сведения

    def describe(self, binding_id: str) -> dict:
        if binding_id.endswith("::"):
            path = binding_id[:-2]
            return {"id": binding_id, "name": self.index.exact.get(path, path), "kind": "module", "path": path, "line": 1, "column": 1}
        binding = self.index.binding(binding_id)
        if binding is None:
            raise SymbolError("symbol not found")
        info = {
            "id": binding_id,
            "name": binding.name,
            "kind": binding.kind,
            "path": binding.path,
            "line": binding.line,
            "column": binding.col + 1,
            "container": binding.scope or None,
        }
        if binding.end_line:
            info["end_line"] = binding.end_line
        if binding.signature:
            info["signature"] = binding.signature
        elif binding.kind == "class":
            parsed = self.files[binding.path]
            bases = [ast.unparse(base) for base in binding.bases][:6]
            info["signature"] = f"class {binding.name}" + (f"({', '.join(bases)})" if bases else "")
            members = parsed.members.get(binding_id, {})
            info["members"] = sorted(name for name in members if not name.startswith("__"))[:40]
        elif binding.kind in {"variable", "attribute", "parameter"} and binding.type_expr is not None:
            try:
                info["type"] = ast.unparse(binding.type_expr)[:200]
            except Exception:
                pass
        if binding.doc:
            info["doc"] = binding.doc
        return info

    def document_symbols(self, relative: str) -> list[dict]:
        parsed = self.file(relative)
        result = []
        for binding in parsed.bindings.values():
            if binding.kind in {"class", "function", "method"} or (
                binding.kind in {"variable", "attribute"} and (not binding.scope or binding.owner)
            ):
                item = {"name": binding.name, "kind": binding.kind, "line": binding.line, "column": binding.col + 1,
                        "container": binding.scope or None}
                if binding.end_line:
                    item["end_line"] = binding.end_line
                if binding.signature:
                    item["signature"] = binding.signature
                result.append(item)
        return sorted(result, key=lambda item: (item["line"], item["column"]))

    # ---------- ссылки

    def references(self, binding_id: str, *, include_candidates: bool = True) -> dict:
        """Подтверждённые ссылки на объявление и кандидаты с тем же именем при неизвестном получателе."""
        name = self.describe(binding_id)["name"]
        if binding_id.endswith("::"):
            raise SymbolError("module references are not supported; search imports with project.map")
        files = _mentions(self.root, self.paths, name, self.files.overrides)
        if binding_id.split("::", 1)[0] not in files:
            files.append(binding_id.split("::", 1)[0])
        verified, candidates = [], []
        for relative in files:
            self._check()
            parsed = self.files[relative]
            if parsed.error:
                continue
            for occurrence in self.index.resolve(parsed):
                if occurrence.name != name and occurrence.role != "import":
                    continue
                final = self.index.final(occurrence)
                if final == binding_id:
                    verified.append(occurrence)
                elif include_candidates and occurrence.accuracy in {"approximate", "unresolved"} and occurrence.name == name:
                    candidates.append(occurrence)
            if len(verified) > MAX_REFERENCES:
                break
        return {"verified": verified, "candidates": candidates, "files_scanned": len(files)}

    def family(self, binding_id: str) -> list[str]:
        """Метод вместе с объявлениями в базовых классах и переопределениями в наследниках проекта."""
        binding = self.index.binding(binding_id)
        if binding is None or binding.kind not in {"method", "attribute"} or not binding.owner:
            return [binding_id]
        roots = []
        for owner in self.index.class_chain(binding.owner):
            found = self.files[owner.split("::", 1)[0]].members.get(owner, {}).get(binding.name)
            if found is not None:
                roots.append(owner)
        top = roots[-1] if roots else binding.owner
        related = {binding_id}
        mentioning = _mentions(self.root, self.paths, binding.name, self.files.overrides)
        for relative in mentioning:
            parsed = self.files[relative]
            for candidate in parsed.bindings.values():
                if candidate.kind == "class" and top in self.index.class_chain(candidate.id):
                    member = parsed.members.get(candidate.id, {}).get(binding.name)
                    if member is not None:
                        related.add(member.id)
        for owner in roots:
            member = self.files[owner.split("::", 1)[0]].members.get(owner, {}).get(binding.name)
            if member is not None:
                related.add(member.id)
        return sorted(related)

    # ---------- переименование

    def rename_edits(self, binding_id: str, new_name: str) -> dict:
        """Правки текста для переименования: только подтверждённые вхождения, с проверкой захвата имён."""
        if not is_identifier(new_name):
            raise SymbolError(f"'{new_name}' is not a valid Python identifier")
        target = self.index.binding(binding_id)
        if target is None:
            raise SymbolError("symbol not found")
        old = target.name
        if old == new_name:
            raise SymbolError("the new name is the same as the old one")
        if old.startswith("__") and old.endswith("__"):
            raise SymbolError("special methods (dunder names) are protocol names and are not renamed")
        warnings = []
        ids = self.family(binding_id)
        if len(ids) > 1:
            warnings.append(f"{old} is overridden or declared in related classes; all {len(ids)} declarations are renamed together")
        owner = self.index.binding(target.owner) if target.owner else None
        if owner is not None:
            owner_file = self.files[owner.path]
            unresolved = [base for base in owner.bases
                          if self.index.resolve_expr(owner_file, owner_file.scope_named(owner.scope), base) is None]
            if unresolved:
                names = ", ".join(ast.unparse(base) for base in unresolved)[:200]
                warnings.append(f"class {owner.name} has bases outside the project ({names}); if {old} overrides a library method, renaming breaks it")
        edits: dict[str, list[tuple[int, int, int]]] = {}
        candidates: list[Occurrence] = []
        count = 0
        for symbol in ids:
            refs = self.references(symbol)
            candidates.extend(refs["candidates"])
            for occurrence in refs["verified"]:
                if occurrence.name != old:
                    continue  # импорт под псевдонимом: локальное имя остаётся прежним
                edits.setdefault(occurrence.path, []).append((occurrence.line, occurrence.col, len(old)))
                count += 1
        self._check_conflicts(ids, new_name, warnings)
        unique_candidates = {(item.path, item.line, item.col): item for item in candidates
                             if (item.line, item.col, len(old)) not in set(edits.get(item.path, []))}
        return {
            "old": old,
            "new": new_name,
            "edits": {path: sorted(set(items)) for path, items in edits.items()},
            "count": count,
            "symbols": ids,
            "candidates": list(unique_candidates.values()),
            "warnings": warnings,
        }

    def _check_conflicts(self, ids: list[str], new_name: str, warnings: list[str]) -> None:
        for symbol in ids:
            binding = self.index.binding(symbol)
            if binding is None:
                continue
            parsed = self.files[binding.path]
            scope = parsed.scope_named(binding.scope)
            if new_name in scope.names:
                raise SymbolError(f"'{new_name}' is already defined in the same scope ({binding.path}:{scope.names[new_name].line})")
            if binding.owner and self.index.member(binding.owner, new_name):
                raise SymbolError(f"class already has a member '{new_name}'")
            if not binding.scope and not binding.owner:
                # другие модули импортируют имя без псевдонима: новое имя не должно быть занято у них
                for relative in _mentions(self.root, self.paths, binding.name, self.files.overrides):
                    other = self.files[relative]
                    if other.tree is None or relative == binding.path:
                        continue
                    for local in other.module.names.values():
                        if local.kind == "import" and local.member == binding.name and self.index.target(local.id) == symbol:
                            if new_name in other.module.names:
                                raise SymbolError(f"'{new_name}' is already defined in {relative}, which imports {binding.name}")
            # ссылка внутри вложенной области не должна оказаться перехвачена локальным именем
            for occurrence in self.references(symbol, include_candidates=False)["verified"]:
                if occurrence.role not in {"read", "write"}:
                    continue
                other = self.files[occurrence.path]
                caller = occurrence.caller.split("::", 1)[1]
                scope_at = other.scope_named(caller)
                shadow = other.lookup(scope_at, new_name)
                if shadow is not None and self.index.target(shadow.id) not in ids:
                    raise SymbolError(f"renaming would capture '{new_name}' at {occurrence.path}:{occurrence.line}")


def apply_edits(text: str, edits: list[tuple[int, int, int]], replacement: str) -> str:
    """Замена фрагментов (строка с 1, колонка с 0, длина) на одно и то же имя; правки не пересекаются."""
    lines = split_lines(text, keepends=True)
    for line, col, length in sorted(set(edits), reverse=True):
        current = lines[line - 1]
        lines[line - 1] = current[:col] + replacement + current[col + length:]
    return "".join(lines)


def occurrence_json(occurrence: Occurrence, parsed: PyFile | None = None) -> dict:
    item = {
        "path": occurrence.path,
        "line": occurrence.line,
        "column": occurrence.col + 1,
        "role": occurrence.role,
        "accuracy": "verified" if occurrence.accuracy == "verified" else "approximate",
    }
    if parsed is not None:
        item["text"] = parsed.line_text(occurrence.line).strip()[:200]
    return item


def binding_line(binding: Binding) -> str:
    return f"{binding.path}:{binding.line}"
