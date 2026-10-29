"""Семантический разбор Python без выполнения кода: области видимости, импорты между файлами, атрибуты.

Ссылка подтверждена (`verified`), только если имя доказуемо связано с объявлением: правила LEGB, цепочки
импортов и реэкспортов, `self`/`cls` внутри методов, атрибуты классов и модулей, простой вывод типа по вызову
конструктора или аннотации. Атрибут с неизвестным получателем - только кандидат (`approximate`), его никогда
не правят автоматически.
"""

from __future__ import annotations

import ast
import io
import keyword
import re
import tokenize
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
SCOPE_NODES = (ast.ClassDef, *FUNCTION_NODES, *COMPREHENSIONS)
IDENTIFIER = re.compile(r"^[^\W\d]\w*$")
MAX_CHAIN = 12
LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")


def split_lines(text: str, *, keepends: bool = False) -> list[str]:
    """Строки так, как их считает ast: только \\r\\n, \\r и \\n (splitlines режет ещё и по \\x0c и \\u2028)."""
    lines = LINE.findall(text)
    return lines if keepends else [line.rstrip("\r\n") for line in lines]


@dataclass
class Binding:
    """Объявление имени в одной области видимости файла."""

    id: str
    name: str
    kind: str  # class function method parameter variable import attribute
    path: str
    line: int
    col: int
    scope: str
    end_line: int = 0
    # для импорта: модуль и имя внутри него (None - импортирован сам модуль)
    module: str | None = None
    member: str | None = None
    relative: int = 0
    signature: str = ""
    doc: str = ""
    owner: str | None = None  # класс, чьим членом является объявление
    bases: list = field(default_factory=list)
    type_expr: ast.AST | None = None  # выражение типа для вывода класса значения


@dataclass
class Occurrence:
    path: str
    line: int
    col: int
    name: str
    role: str  # definition read write import attribute keyword export
    binding: str | None
    accuracy: str = "verified"  # verified approximate unresolved builtin pending
    call: bool = False
    caller: str = ""  # id области, из которой идёт обращение
    key: int = 0  # номер вхождения в файле для отложенного разрешения


class Scope:
    def __init__(self, node: ast.AST, kind: str, qual: str, parent: "Scope | None"):
        self.node = node
        self.kind = kind  # module class function comprehension
        self.qual = qual
        self.parent = parent
        self.names: dict[str, Binding] = {}
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        self.cls: ast.ClassDef | None = None
        self.self_name: str | None = None


def module_names(path: str, packages: set[str]) -> list[str]:
    """Имена модуля для файла: точное (от верхнего пакета по цепочке __init__.py), затем все суффиксы пути."""
    pure = PurePosixPath(path)
    parts = list(pure.with_suffix("").parts)
    is_init = bool(parts) and parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    directory = str(pure.parent) if str(pure.parent) != "." else ""
    depth = 0
    while directory and directory in packages:
        depth += 1
        directory = str(PurePosixPath(directory).parent) if "/" in directory else ""
    keep = depth if is_init else depth + 1
    exact = ".".join(parts[-keep:]) if keep and parts else ".".join(parts)
    names = [exact] if exact else []
    for start in range(len(parts)):
        candidate = ".".join(parts[start:])
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def is_identifier(name: str) -> bool:
    return bool(IDENTIFIER.match(name)) and name.isidentifier() and not keyword.iskeyword(name)


def outer_parts(node: ast.AST) -> list[ast.AST]:
    """Части объявления, которые вычисляются в объемлющей области: декораторы, умолчания, аннотации, базы."""
    parts: list[ast.AST] = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        parts.extend(node.decorator_list)
    if isinstance(node, FUNCTION_NODES):
        parts.extend(node.args.defaults)
        parts.extend(item for item in node.args.kw_defaults if item is not None)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parts.extend(arg.annotation for arg in all_args(node) if arg.annotation is not None)
        if node.returns is not None:
            parts.append(node.returns)
    if isinstance(node, ast.ClassDef):
        parts.extend(node.bases)
        parts.extend(item.value for item in node.keywords)
    if isinstance(node, COMPREHENSIONS):
        parts.append(node.generators[0].iter)
    return parts


def all_args(node: ast.AST) -> list[ast.arg]:
    arguments = node.args  # type: ignore[attr-defined]
    return [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
            *([arguments.vararg] if arguments.vararg else []), *([arguments.kwarg] if arguments.kwarg else [])]


def inner_roots(node: ast.AST) -> list[ast.AST]:
    """Узлы, которые исполняются внутри области узла."""
    if isinstance(node, ast.Lambda):
        return [node.body]
    if isinstance(node, COMPREHENSIONS):
        roots: list[ast.AST] = []
        for index, generator in enumerate(node.generators):
            roots.append(generator.target)
            if index:
                roots.append(generator.iter)
            roots.extend(generator.ifs)
        roots.extend([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
        return roots
    return list(getattr(node, "body", []))


def shallow(roots: list[ast.AST]):
    """Обход в прямом порядке без захода внутрь вложенных областей (сами узлы областей выдаются)."""
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, SCOPE_NODES):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


class PyFile:
    """Разбор одного файла: области видимости, объявления и вхождения имён."""

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self.lines = split_lines(text)
        self.error: str | None = None
        self.scopes: list[Scope] = []
        self.bindings: dict[str, Binding] = {}
        self.occurrences: list[Occurrence] = []
        self.members: dict[str, dict[str, Binding]] = {}
        self.exports: list[tuple[str, int, int]] = []
        self._scope_of: dict[int, Scope] = {}
        self._pending: dict[int, tuple[Scope, ast.AST | None]] = {}
        self._call_funcs: set[int] = set()
        self._parent_call: dict[int, ast.Call] = {}
        try:
            self.tree = ast.parse(text)
        except (SyntaxError, ValueError) as exc:
            self.tree = None
            self.error = f"{type(exc).__name__}: {exc}"
            return
        self.module = Scope(self.tree, "module", "", None)
        self.scopes.append(self.module)
        self._scope_of[id(self.tree)] = self.module
        for node in shallow(inner_roots(self.tree)):
            self._declare(node, self.module)
        self._visit_scope(self.tree, self.module)

    # ---------- позиции

    def col(self, line: int, byte_col: int) -> int:
        if line < 1 or line > len(self.lines):
            return byte_col
        return len(self.lines[line - 1].encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""

    def _name_position(self, node: ast.AST, word: str, name: str) -> tuple[int, int]:
        """Позиция имени после def/class: в ast её нет, ищем токенами от ключевого слова."""
        line = node.lineno
        body = getattr(node, "body", None)
        last = body[0].lineno if body else line
        segment = "\n".join(self.lines[line - 1:max(last, line)])
        try:
            previous = None
            for token in tokenize.generate_tokens(io.StringIO(segment + "\n").readline):
                if token.type == tokenize.NAME:
                    if previous == word and token.string == name:
                        return line + token.start[0] - 1, token.start[1]
                    previous = token.string
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
        found = re.search(rf"\b{word}\s+({re.escape(name)})\b", self.line_text(line))
        return line, found.start(1) if found else self.col(line, node.col_offset)

    def _attr_position(self, node: ast.Attribute) -> tuple[int, int]:
        line = node.end_lineno or node.lineno
        return line, self.col(line, node.end_col_offset or 0) - len(node.attr)

    def _alias_position(self, alias: ast.alias) -> tuple[tuple[int, int], tuple[int, int] | None]:
        """Позиции имени в импорте и псевдонима после `as`."""
        line = getattr(alias, "lineno", None) or 1
        start = (line, self.col(line, getattr(alias, "col_offset", 0)))
        if not alias.asname:
            return start, None
        end_line = getattr(alias, "end_lineno", line) or line
        return start, (end_line, self.col(end_line, getattr(alias, "end_col_offset", 0)) - len(alias.asname))

    def _find_col(self, line: int, name: str, byte_col: int) -> int:
        text = self.line_text(line)
        start = self.col(line, byte_col)
        found = re.search(rf"\b{re.escape(name)}\b", text[start:])
        return start + found.start() if found else start

    # ---------- объявления

    def _enclosing_function(self, scope: Scope, name: str) -> Scope | None:
        current = scope.parent
        while current is not None and current.kind != "module":
            if current.kind == "function" and name in current.names:
                return current
            current = current.parent
        return None

    def _bind(self, scope: Scope, name: str, kind: str, line: int, col: int, **extra) -> Binding:
        if name in scope.globals:
            scope = self.module
        elif name in scope.nonlocals:
            scope = self._enclosing_function(scope, name) or scope
        existing = scope.names.get(name)
        if existing is not None:
            if existing.type_expr is None and extra.get("type_expr") is not None:
                existing.type_expr = extra["type_expr"]
            return existing
        qual = f"{scope.qual}.{name}" if scope.qual else name
        binding = Binding(id=f"{self.path}::{qual}", name=name, kind=kind, path=self.path, line=line, col=col,
                          scope=scope.qual, **extra)
        scope.names[name] = binding
        self.bindings[binding.id] = binding
        if scope.kind == "class":
            class_id = f"{self.path}::{scope.qual}"
            binding.owner = class_id
            self.members.setdefault(class_id, {})[name] = binding
        return binding

    def _new_scope(self, node: ast.AST, parent: Scope) -> Scope:
        if isinstance(node, ast.ClassDef):
            kind, name = "class", node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind, name = "function", node.name
        elif isinstance(node, ast.Lambda):
            kind, name = "function", f"<lambda:{node.lineno}:{node.col_offset}>"
        else:
            kind, name = "comprehension", f"<{type(node).__name__.lower()}:{node.lineno}:{node.col_offset}>"
        if parent.kind == "function" and kind != "comprehension":
            qual = f"{parent.qual}.<locals>.{name}"
        else:
            qual = f"{parent.qual}.{name}" if parent.qual else name
        scope = Scope(node, kind, qual, parent)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.kind == "class":
            scope.cls = parent.node  # type: ignore[assignment]
            decorators = {getattr(item, "id", getattr(item, "attr", "")) for item in node.decorator_list}
            positional = node.args.posonlyargs + node.args.args
            if positional and "staticmethod" not in decorators:
                scope.self_name = positional[0].arg
        self._scope_of[id(node)] = scope
        self.scopes.append(scope)
        return scope

    def _declare(self, node: ast.AST, scope: Scope) -> None:
        line = getattr(node, "lineno", 0)
        if isinstance(node, SCOPE_NODES):
            # декораторы и умолчания объявляют свои имена (лямбды, :=) в объемлющей области
            for part in shallow(outer_parts(node)):
                self._declare(part, scope)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                word = "class" if isinstance(node, ast.ClassDef) else "def"
                line, col = self._name_position(node, word, node.name)
                kind = "class" if word == "class" else ("method" if scope.kind == "class" else "function")
                extra: dict = {"end_line": getattr(node, "end_lineno", line) or line,
                               "doc": (ast.get_docstring(node) or "").strip()[:400]}
                if word == "def":
                    extra["signature"] = self._signature(node)
                else:
                    extra["bases"] = list(node.bases)
                self._bind(scope, node.name, kind, line, col, **extra)
            inner = self._new_scope(node, scope)
            if isinstance(node, FUNCTION_NODES):
                self._declare_arguments(node, inner)
            for child in shallow(inner_roots(node)):
                self._declare(child, inner)
            return
        if isinstance(node, ast.Global):
            scope.globals.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            scope.nonlocals.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(scope, node.id, "variable", line, self.col(line, node.col_offset))
        elif isinstance(node, ast.NamedExpr):
            target = scope
            while target.kind == "comprehension" and target.parent is not None:
                target = target.parent
            self._bind(target, node.target.id, "variable", node.target.lineno,
                       self.col(node.target.lineno, node.target.col_offset))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name.split(".")[0]
                start, renamed = self._alias_position(alias)
                position = renamed or start
                if isinstance(node, ast.Import):
                    module = alias.name if alias.asname else alias.name.split(".")[0]
                    self._bind(scope, local, "import", *position, module=module)
                else:
                    self._bind(scope, local, "import", *position, module=node.module or "",
                               member=alias.name, relative=node.level)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._bind(scope, node.name, "variable", line, self._find_col(line, node.name, node.col_offset))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            self._bind(scope, node.name, "variable", line, self._find_col(line, node.name, node.col_offset))
        elif isinstance(node, ast.MatchMapping) and node.rest:
            self._bind(scope, node.rest, "variable", line, self._find_col(line, node.rest, node.col_offset))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            self._declare_types(node, scope)
        if isinstance(node, ast.Assign) and scope.kind == "module":
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                    for item in node.value.elts:
                        if isinstance(item, ast.Constant) and isinstance(item.value, str):
                            self.exports.append((item.value, item.lineno, self.col(item.lineno, item.col_offset) + 1))

    def _declare_types(self, node: ast.Assign | ast.AnnAssign, scope: Scope) -> None:
        """x = Class(...) и x: Class дают тип переменной; self.x = ... объявляет атрибут экземпляра класса."""
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value.func if isinstance(getattr(node, "value", None), ast.Call) else None
        annotation = node.annotation if isinstance(node, ast.AnnAssign) else None
        for target in targets:
            if isinstance(target, ast.Name):
                binding = self._bind(scope, target.id, "variable", target.lineno, self.col(target.lineno, target.col_offset))
                binding.type_expr = binding.type_expr or annotation or value
            elif (
                isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                and scope.self_name and target.value.id == scope.self_name and scope.cls is not None
            ):
                owner = self._scope_of.get(id(scope.cls))
                if owner is not None and target.attr not in owner.names:
                    line, col = self._attr_position(target)
                    member = self._bind(owner, target.attr, "attribute", line, col)
                    member.type_expr = annotation or value

    def _declare_arguments(self, node: ast.AST, scope: Scope) -> None:
        for arg in all_args(node):
            binding = self._bind(scope, arg.arg, "parameter", arg.lineno, self.col(arg.lineno, arg.col_offset))
            binding.type_expr = arg.annotation
        if scope.self_name and scope.cls is not None:
            marker = ast.Name(id=scope.cls.name, ctx=ast.Load())
            marker._koddi_class = scope.cls  # type: ignore[attr-defined]
            scope.names[scope.self_name].type_expr = marker

    @staticmethod
    def _signature(node: ast.AST) -> str:
        try:
            args = ast.unparse(node.args)  # type: ignore[attr-defined]
            returns = " -> " + ast.unparse(node.returns) if getattr(node, "returns", None) is not None else ""  # type: ignore[attr-defined]
        except Exception:
            args, returns = "...", ""
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        return f"{prefix}{node.name}({args}){returns}"[:300]  # type: ignore[attr-defined]

    # ---------- вхождения

    def scope_for(self, node: ast.AST) -> Scope | None:
        return self._scope_of.get(id(node))

    def scope_named(self, qual: str) -> Scope:
        if not qual:
            return self.module
        return next((scope for scope in self.scopes if scope.qual == qual), self.module)

    def lookup(self, scope: Scope, name: str) -> Binding | None:
        """Разрешение имени по LEGB: области классов не видны вложенным функциям."""
        if name in scope.globals:
            return self.module.names.get(name)
        if name in scope.nonlocals:
            target = self._enclosing_function(scope, name)
            return target.names.get(name) if target else None
        if name in scope.names:
            return scope.names[name]
        current = scope.parent
        while current is not None:
            if current.kind != "class" and name in current.names:
                return current.names[name]
            current = current.parent
        return None

    def _caller(self, scope: Scope) -> str:
        while scope.kind == "comprehension" and scope.parent is not None:
            scope = scope.parent
        return f"{self.path}::{scope.qual}"

    def _add(self, occurrence: Occurrence, scope: Scope, receiver: ast.AST | None = None) -> None:
        occurrence.key = len(self.occurrences)
        occurrence.caller = self._caller(scope)
        self.occurrences.append(occurrence)
        if occurrence.accuracy == "pending":
            self._pending[occurrence.key] = (scope, receiver)

    def _visit_scope(self, node: ast.AST, scope: Scope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and scope.parent is not None:
            binding = scope.parent.names.get(node.name)
            if binding is not None:
                self._add(Occurrence(self.path, binding.line, binding.col, node.name, "definition", binding.id), scope.parent)
        if isinstance(node, FUNCTION_NODES):
            for arg in all_args(node):
                found = scope.names.get(arg.arg)
                if found is not None:
                    self._add(Occurrence(self.path, found.line, found.col, arg.arg, "definition", found.id), scope)
        for child in shallow(inner_roots(node)):
            self._visit(child, scope)

    def _visit(self, node: ast.AST, scope: Scope) -> None:
        if isinstance(node, SCOPE_NODES):
            for part in shallow(outer_parts(node)):
                self._visit(part, scope)
            self._visit_scope(node, self._scope_of[id(node)])
            return
        if isinstance(node, ast.Call):
            self._call_funcs.add(id(node.func))
            for item in node.keywords:
                self._parent_call[id(item)] = node
        if isinstance(node, ast.Name):
            binding = self.lookup(scope, node.id)
            role = "write" if isinstance(node.ctx, (ast.Store, ast.Del)) else "read"
            self._add(Occurrence(self.path, node.lineno, self.col(node.lineno, node.col_offset), node.id, role,
                                 binding.id if binding else None, "verified" if binding else "builtin",
                                 call=id(node) in self._call_funcs), scope)
        elif isinstance(node, ast.Attribute):
            line, col = self._attr_position(node)
            role = "write" if isinstance(node.ctx, (ast.Store, ast.Del)) else "attribute"
            self._add(Occurrence(self.path, line, col, node.attr, role, None, "pending",
                                 call=id(node) in self._call_funcs), scope, node.value)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name.split(".")[0]
                binding = scope.names.get(local) or self.lookup(scope, local)
                start, renamed = self._alias_position(alias)
                spelled = alias.name.split(".")[-1] if isinstance(node, ast.ImportFrom) else alias.name
                self._add(Occurrence(self.path, *start, spelled, "import", binding.id if binding else None), scope)
                if renamed and binding is not None:
                    self._add(Occurrence(self.path, *renamed, alias.asname, "definition", binding.id), scope)
        elif isinstance(node, ast.keyword) and node.arg:
            self._add(Occurrence(self.path, node.lineno, self.col(node.lineno, node.col_offset), node.arg,
                                 "keyword", None, "pending"), scope, self._parent_call.get(id(node)))


class PythonIndex:
    """Ссылки между файлами проекта: импорты, реэкспорты, атрибуты модулей и классов."""

    def __init__(self, files: dict[str, PyFile]):
        self.files = files
        self.packages = {str(PurePosixPath(path).parent) for path in files if path.endswith("/__init__.py")}
        self.modules: dict[str, list[str]] = {}
        self.exact: dict[str, str] = {}
        for path in files:
            names = module_names(path, self.packages)
            if names:
                self.exact[path] = names[0]
            for name in names:
                self.modules.setdefault(name, []).append(path)
        self._resolved: dict[str, str | None] = {}

    # ---------- модули

    def module_path(self, dotted: str, importer: str) -> str | None:
        candidates = self.modules.get(dotted)
        if not candidates:
            return None
        exact = [path for path in candidates if self.exact.get(path) == dotted]
        pool = exact or candidates
        if len(pool) == 1:
            return pool[0]

        def closeness(path: str) -> tuple[int, int]:
            common = 0
            for left, right in zip(PurePosixPath(importer).parts, PurePosixPath(path).parts):
                if left != right:
                    break
                common += 1
            return common, -len(path)

        return max(pool, key=closeness)

    def _absolute(self, binding: Binding) -> str:
        if not binding.relative:
            return binding.module or ""
        base = list(PurePosixPath(binding.path).parent.parts)
        if binding.relative > 1:
            base = base[: max(len(base) - (binding.relative - 1), 0)]
        directory = "/".join(base)
        init = f"{directory}/__init__.py" if directory else "__init__.py"
        package = self.exact.get(init) or ".".join(base)
        return ".".join(item for item in (package, binding.module) if item)

    @staticmethod
    def module_id(path: str) -> str:
        return f"{path}::"

    # ---------- объявления

    def binding(self, binding_id: str | None) -> Binding | None:
        if not binding_id or "::" not in binding_id:
            return None
        file = self.files.get(binding_id.split("::", 1)[0])
        return file.bindings.get(binding_id) if file else None

    def target(self, binding_id: str | None, depth: int = 0) -> str | None:
        """Конечное объявление: импорт ведёт к объявлению в другом модуле или к самому модулю (`path::`)."""
        if binding_id is None or depth > MAX_CHAIN:
            return binding_id
        if binding_id in self._resolved:
            return self._resolved[binding_id]
        self._resolved[binding_id] = binding_id  # защита от циклических импортов
        binding = self.binding(binding_id)
        result = binding_id
        if binding is not None and binding.kind == "import":
            module = self._absolute(binding)
            path = self.module_path(module, binding.path) if module else None
            if binding.member is None:
                result = self.module_id(path) if path else binding_id
            else:
                found = None
                if path is not None and self.files[path].tree is not None:
                    found = self.files[path].module.names.get(binding.member)
                if found is not None:
                    result = self.target(found.id, depth + 1) or found.id
                else:
                    submodule = self.module_path(f"{module}.{binding.member}" if module else binding.member, binding.path)
                    result = self.module_id(submodule) if submodule else binding_id
        self._resolved[binding_id] = result
        return result

    def class_chain(self, class_id: str, depth: int = 0) -> list[str]:
        """Класс и его базовые классы проекта (MRO упрощённо: в глубину слева направо)."""
        binding = self.binding(class_id)
        if depth > 8 or binding is None or binding.kind != "class":
            return []
        file = self.files[binding.path]
        scope = file.scope_named(binding.scope)
        chain = [class_id]
        for base in binding.bases:
            base_id = self.resolve_expr(file, scope, base)
            if base_id and base_id not in chain:
                chain.extend(item for item in self.class_chain(base_id, depth + 1) if item not in chain)
        return chain

    def subclasses(self, class_id: str) -> list[str]:
        result = []
        for file in self.files.values():
            for binding in file.bindings.values():
                if binding.kind == "class" and binding.id != class_id and class_id in self.class_chain(binding.id):
                    result.append(binding.id)
        return result

    def member(self, class_id: str | None, name: str) -> str | None:
        if not class_id:
            return None
        for owner in self.class_chain(class_id):
            file = self.files[owner.split("::", 1)[0]]
            found = file.members.get(owner, {}).get(name)
            if found is not None:
                return found.id
        return None

    def resolve_expr(self, file: PyFile, scope: Scope | None, expr: ast.AST | None, depth: int = 0) -> str | None:
        """Объявление, на которое указывает выражение: имя, атрибут модуля или класса."""
        if expr is None or scope is None or depth > MAX_CHAIN:
            return None
        if hasattr(expr, "_koddi_class"):
            owner = file.scope_for(expr._koddi_class)  # type: ignore[attr-defined]
            return f"{file.path}::{owner.qual}" if owner else None
        if isinstance(expr, ast.Name):
            binding = file.lookup(scope, expr.id)
            return self.target(binding.id) if binding else None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                parsed = ast.parse(expr.value, mode="eval").body
            except SyntaxError:
                return None
            return self.resolve_expr(file, scope, parsed, depth + 1)
        if isinstance(expr, ast.Subscript) and not isinstance(expr.slice, ast.Tuple):
            return self.resolve_expr(file, scope, expr.slice, depth + 1)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            if isinstance(expr.right, ast.Constant) and expr.right.value is None:
                return self.resolve_expr(file, scope, expr.left, depth + 1)
            return None
        if isinstance(expr, ast.Attribute):
            return self.attribute_of(self.resolve_expr(file, scope, expr.value, depth + 1), expr.attr)
        return None

    def attribute_of(self, owner: str | None, name: str) -> str | None:
        if owner is None:
            return None
        if owner.endswith("::"):
            path = owner[:-2]
            file = self.files.get(path)
            if file is None or file.tree is None:
                return None
            found = file.module.names.get(name)
            if found is not None:
                return self.target(found.id)
            if path.endswith("__init__.py") and path in self.exact:
                sub = self.module_path(f"{self.exact[path]}.{name}", path)
                return self.module_id(sub) if sub else None
            return None
        binding = self.binding(owner)
        if binding is not None and binding.kind == "class":
            return self.member(owner, name)
        return None

    def _class_only(self, value: str | None) -> str | None:
        found = self.binding(value)
        return value if found is not None and found.kind == "class" else None

    def instance_class(self, file: PyFile, scope: Scope, expr: ast.AST, depth: int = 0) -> str | None:
        """Класс значения выражения, если его можно вывести без выполнения."""
        if depth > MAX_CHAIN:
            return None
        if isinstance(expr, ast.Name):
            binding = file.lookup(scope, expr.id)
            if binding is None:
                return None
            final = self.binding(self.target(binding.id))
            if final is not None and final.kind == "class":
                return None  # имя класса, а не экземпляр
            source = binding if binding.type_expr is not None or final is None else final
            if source.type_expr is None:
                return None
            source_file = self.files.get(source.path, file)
            return self._class_only(self.resolve_expr(source_file, source_file.scope_named(source.scope), source.type_expr, depth + 1))
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Name) and expr.func.id == "super" and file.lookup(scope, "super") is None:
                return self._super_class(file, scope)
            return self._class_only(self.resolve_expr(file, scope, expr.func, depth + 1))
        if isinstance(expr, ast.Attribute):
            owner = self.instance_class(file, scope, expr.value, depth + 1)
            member = self.binding(self.member(owner, expr.attr)) if owner else None
            if member is not None and member.type_expr is not None:
                member_file = self.files[member.path]
                return self._class_only(self.resolve_expr(member_file, member_file.scope_named(member.scope), member.type_expr, depth + 1))
        return None

    def _super_class(self, file: PyFile, scope: Scope) -> str | None:
        current: Scope | None = scope
        while current is not None and current.cls is None:
            current = current.parent
        if current is None or current.cls is None:
            return None
        class_scope = file.scope_for(current.cls)
        binding = self.binding(f"{file.path}::{class_scope.qual}") if class_scope else None
        if binding is None:
            return None
        for base in binding.bases:
            resolved = self._class_only(self.resolve_expr(file, class_scope.parent, base))
            if resolved:
                return resolved
        return None

    def resolve(self, file: PyFile) -> list[Occurrence]:
        """Окончательные объявления вхождений файла; кэшированный разбор файла не меняется."""
        if file.tree is None:
            return []
        result = []
        for occurrence in file.occurrences:
            if occurrence.accuracy != "pending":
                result.append(occurrence)
                continue
            scope, extra = file._pending[occurrence.key]
            if occurrence.role == "keyword":
                result.append(replace(occurrence, **self._keyword(file, scope, occurrence, extra)))
                continue
            target = None
            owner = self.resolve_expr(file, scope, extra)
            if owner is not None:
                target = self.attribute_of(owner, occurrence.name)
            if target is None:
                instance = self.instance_class(file, scope, extra) if extra is not None else None
                target = self.member(instance, occurrence.name) if instance else None
            if target is not None:
                accuracy = "verified"
            elif self._external_receiver(file, scope, extra, owner):
                accuracy = "external"
            else:
                accuracy = "approximate"
            result.append(replace(occurrence, binding=target, accuracy=accuracy))
        for name, line, col in file.exports:
            binding = file.module.names.get(name)
            if binding is not None:
                result.append(Occurrence(file.path, line, col, name, "export", binding.id, key=-1))
        return result

    def _external_receiver(self, file: PyFile, scope: Scope, receiver: ast.AST | None, owner: str | None) -> bool:
        """Получатель точно не из проекта: литерал, встроенное имя или импорт, не найденный среди файлов."""
        if isinstance(receiver, (ast.Constant, ast.JoinedStr, ast.List, ast.Tuple, ast.Dict, ast.Set,
                                 ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return True
        if isinstance(receiver, ast.Name) and file.lookup(scope, receiver.id) is None:
            import builtins

            return hasattr(builtins, receiver.id)
        found = self.binding(owner)
        if found is not None and found.kind == "import" and self.target(owner) == owner:
            return True
        if isinstance(receiver, ast.Attribute):
            return self._external_receiver(file, scope, receiver.value, self.resolve_expr(file, scope, receiver.value))
        return False

    def _keyword(self, file: PyFile, scope: Scope, occurrence: Occurrence, call: ast.AST | None) -> dict:
        """Именованный аргумент вызова ссылается на параметр вызываемой функции, если она известна."""
        function = self.resolve_callable(file, scope, call.func) if isinstance(call, ast.Call) else None
        found = self.binding(function)
        if found is not None and found.kind == "class":
            function = self.member(function, "__init__")
            found = self.binding(function)
        if found is not None and found.kind in {"function", "method"}:
            callee = self.files[found.path]
            qual = function.split("::", 1)[1]
            callee_scope = next((item for item in callee.scopes if item.qual == qual and item.kind == "function"), None)
            parameter = callee_scope.names.get(occurrence.name) if callee_scope else None
            if parameter is not None and parameter.kind == "parameter":
                return {"binding": parameter.id, "accuracy": "verified"}
        return {"binding": None, "accuracy": "unresolved"}

    def resolve_callable(self, file: PyFile, scope: Scope, func: ast.AST) -> str | None:
        """Вызываемое объявление: имя, атрибут модуля или класса, либо метод выведенного экземпляра."""
        found = self.resolve_expr(file, scope, func)
        if found is None and isinstance(func, ast.Attribute):
            instance = self.instance_class(file, scope, func.value)
            found = self.member(instance, func.attr) if instance else None
        return found

    def final(self, occurrence: Occurrence) -> str | None:
        return self.target(occurrence.binding) if occurrence.binding else None
