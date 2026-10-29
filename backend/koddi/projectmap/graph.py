"""Граф карты проекта в памяти: папки, файлы, маршруты API и заметки со связями между ними.

Строится из разобранных файлов (`parse.py`) и заметок агента; ничего не читает с диска.
"""

from __future__ import annotations

import posixpath
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import PurePosixPath
from urllib.parse import unquote

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".json", ".css", ".scss")
NODE_BUILTINS = {
    "fs", "path", "os", "url", "http", "https", "crypto", "child_process", "events", "stream", "util", "zlib",
    "assert", "buffer", "net", "tls", "dns", "readline", "process", "querystring", "worker_threads", "module",
}
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
# первые шесть - исходные виды заметок карты, остальные - разделы знаний проекта (F2)
NOTE_KINDS = ("decision", "module", "file", "api", "topic", "gotcha", "overview", "architecture", "dataflow",
              "convention", "issue", "test", "progress")
PARAM = re.compile(r"\{[^}/]*\}|<[^>/]*>|:[A-Za-z_]\w*")


def file_id(path: str) -> str:
    return "file:" + path


def dir_id(path: str) -> str:
    return "dir:" + path


def note_id(value: int) -> str:
    return f"note:{value}"


def api_id(method: str, path: str) -> str:
    return f"api:{method} {path}"


def parent_of(path: str) -> str:
    parent = posixpath.dirname(path)
    return "" if parent in {"", "."} else parent


def _segments(path: str) -> list[str]:
    return ["{}" if PARAM.fullmatch(part) or part == "{}" else part for part in path.strip("/").split("/")]


@dataclass
class Graph:
    """Неизменяемый снимок карты одной версии; запросы агента и интерфейса читают его без блокировок."""

    files: dict[str, dict]
    notes: list[dict]
    version: int = 0
    imports: dict[str, list[str]] = field(default_factory=dict)
    imported_by: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    externals: dict[str, list[str]] = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    linked_from: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    routes: dict[str, dict] = field(default_factory=dict)
    calls: dict[str, list[str]] = field(default_factory=dict)
    callers: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    dirs: dict[str, dict] = field(default_factory=dict)
    note_targets: dict[int, list[str]] = field(default_factory=dict)
    notes_by_node: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    stale_notes: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._index()
        for path, item in self.files.items():
            info = item.get("info") or {}
            self.imports[path], self.externals[path] = self._resolve_imports(path, info)
            for target in self.imports[path]:
                self.imported_by[target].append(path)
            links = [target for link in info.get("links", []) if (target := self._resolve_link(path, link))]
            self.links[path] = list(dict.fromkeys(links))
            for target in self.links[path]:
                self.linked_from[target].append(path)
            for route in info.get("routes", []):
                key = api_id(route["m"], route["p"])
                self.routes.setdefault(key, {"method": route["m"], "path": route["p"], "file": path, "line": route.get("l"), "handler": route.get("h")})
        self._route_index: dict[int, list[str]] = defaultdict(list)
        for key, route in self.routes.items():
            self._route_index[len(_segments(route["path"]))].append(key)
        for path, item in self.files.items():
            matched = []
            for call in (item.get("info") or {}).get("calls", []):
                matched.extend(self.match_api(call))
            self.calls[path] = list(dict.fromkeys(matched))
            for key in self.calls[path]:
                self.callers[key].append(path)
        for note in self.notes:
            targets = []
            for target in note.get("targets", []):
                node = self.resolve_ref(str(target.get("ref", "")))
                if node:
                    targets.append(node)
                    sha = target.get("sha256")
                    path = node[5:] if node.startswith("file:") else None
                    if sha and path and self.files.get(path, {}).get("sha256") != sha:
                        self.stale_notes.add(note["id"])
            for link in re.findall(r"\[\[([^\]|#\n]+)", note.get("body", "")):
                node = self.resolve_ref(link.strip())
                if node:
                    targets.append(node)
            self.note_targets[note["id"]] = list(dict.fromkeys(target for target in targets if target != note_id(note["id"])))
            for target in self.note_targets[note["id"]]:
                self.notes_by_node[target].append(note["id"])

    # ---------- индексы и разрешение ссылок

    def _index(self) -> None:
        self.by_lower = {path.lower(): path for path in self.files}
        self.by_stem: dict[str, list[str]] = defaultdict(list)
        self.python_modules: dict[str, list[str]] = defaultdict(list)
        self.notes_by_title = {note["title"].casefold(): note["id"] for note in self.notes}
        self.notes_by_id = {note["id"]: note for note in self.notes}
        root = {"files": [], "dirs": [], "total": 0}
        self.dirs[""] = root
        for path in sorted(self.files):
            pure = PurePosixPath(path)
            self.by_stem[pure.stem.lower()].append(path)
            self.by_stem[pure.name.lower()].append(path)
            parent = parent_of(path)
            chain = []
            current = parent
            while True:
                chain.append(current)
                if current == "":
                    break
                current = parent_of(current)
            for depth, directory in enumerate(chain):
                if directory not in self.dirs:
                    self.dirs[directory] = {"files": [], "dirs": [], "total": 0}
                self.dirs[directory]["total"] += 1
                if depth == 0:
                    self.dirs[directory]["files"].append(path)
            if pure.suffix in {".py", ".pyi"}:
                parts = list(pure.with_suffix("").parts)
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for start in range(len(parts)):
                    self.python_modules[".".join(parts[start:])].append(path)
        for directory in sorted(self.dirs):
            if directory:
                self.dirs[parent_of(directory)]["dirs"].append(directory)
        self.go_module = ""
        for path in self.files:
            if PurePosixPath(path).name == "go.mod":
                self.go_module = (self.files[path].get("info") or {}).get("go_module", "")

    def _closest(self, importer: str, candidates: list[str]) -> str:
        if len(candidates) == 1:
            return candidates[0]
        return max(candidates, key=lambda path: (len(posixpath.commonpath([importer, path])), -len(path)))

    def _python_target(self, importer: str, dotted: str, *, relative: bool) -> str | None:
        if relative:
            base = dotted.replace(".", "/")
            for candidate in (base + ".py", base + "/__init__.py", base + ".pyi"):
                if candidate in self.files:
                    return candidate
            return None
        candidates = self.python_modules.get(dotted)
        if not candidates:
            return None
        top = dotted.split(".")[0]
        if top in STDLIB:
            # stdlib не путается с одноимённым файлом в глубине проекта: принимается только файл в корне
            exact = [path for path in candidates if path.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".") == dotted]
            return exact[0] if exact else None
        return self._closest(importer, candidates)

    def _resolve_imports(self, path: str, info: dict) -> tuple[list[str], list[str]]:
        lang = info.get("lang")
        internal: list[str] = []
        external: list[str] = []
        for spec in info.get("imports", []):
            if lang == "python":
                level = int(spec.get("lv") or 0)
                module = spec.get("m") or ""
                names = spec.get("n") or []
                if level:
                    base = parent_of(path).split("/") if parent_of(path) else []
                    if level - 1 > len(base):
                        continue
                    base = base[: len(base) - (level - 1)] if level > 1 else base
                    prefix = ".".join([*base, *([module] if module else [])])
                    found = [self._python_target(path, f"{prefix}.{name}" if prefix else name, relative=True) for name in names]
                    found = [item for item in found if item] or [self._python_target(path, prefix, relative=True)]
                else:
                    found = [self._python_target(path, f"{module}.{name}", relative=False) for name in names]
                    found = [item for item in found if item] or [self._python_target(path, module, relative=False)]
                    if not any(found):
                        top = module.split(".")[0]
                        if top and top not in STDLIB:
                            external.append(top)
                internal.extend(item for item in found if item and item != path)
                continue
            if not isinstance(spec, str):
                continue
            target = self._resolve_spec(path, spec, lang or "")
            if target:
                if target != path:
                    internal.append(target)
            elif lang in {"typescript", "javascript", "vue", "svelte"} and not spec.startswith((".", "/")):
                name = spec.removeprefix("node:")
                package = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
                if package and package not in NODE_BUILTINS and not spec.startswith("node:"):
                    external.append(package)
        return list(dict.fromkeys(internal)), sorted(set(external))

    def _existing(self, base: str, extensions: tuple[str, ...] = ()) -> str | None:
        base = posixpath.normpath(base)
        if base.startswith("..") or base in {".", ""}:
            return None
        if base in self.files:
            return base
        for extension in extensions:
            if base + extension in self.files:
                return base + extension
        for extension in extensions:
            if f"{base}/index{extension}" in self.files:
                return f"{base}/index{extension}"
        stem, suffix = posixpath.splitext(base)
        # ESM в TypeScript пишет import './x.js' для файла x.ts
        if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
            for extension in (".ts", ".tsx", ".mts", ".cts"):
                if stem + extension in self.files:
                    return stem + extension
        return None

    def _resolve_spec(self, importer: str, spec: str, lang: str) -> str | None:
        directory = parent_of(importer)
        if lang in {"typescript", "javascript", "vue", "svelte", "css", "html", "ruby", "shell"}:
            extensions = JS_EXTENSIONS if lang != "ruby" else (".rb",)
            if spec.startswith((".", "/")):
                base = spec.lstrip("/") if spec.startswith("/") else posixpath.join(directory, spec)
                found = self._existing(base, extensions)
                if not found and lang == "css":
                    head, tail = posixpath.split(base)
                    found = self._existing(posixpath.join(head, "_" + tail), (".scss", ".sass", ".css"))
                if not found and spec.startswith("/") and lang == "html":
                    found = self._existing(posixpath.join(directory, spec.lstrip("/")), extensions)
                return found
            if lang == "html" or lang == "css":
                return self._existing(posixpath.join(directory, spec), extensions)
            if lang == "ruby":
                return self._existing(posixpath.join(directory, spec), extensions)
            if spec.startswith(("@/", "~/")):
                rest = spec[2:]
                for path in self.files:
                    for extension in ("", *extensions):
                        if path.endswith("src/" + rest + extension):
                            return path
            return None
        if lang in {"c", "cpp", "objc"}:
            return self._existing(posixpath.join(directory, spec)) or self._existing(spec) or next(
                (path for path in self.files if path.endswith("/" + spec)), None
            )
        if lang == "rust":
            if spec.startswith("crate::"):
                parts = spec.split("::")[1:]
                root = next((path[: -len("lib.rs")] for path in self.files if path.endswith("src/lib.rs") and importer.startswith(path[: -len("lib.rs")])), None)
                root = root or next((path[: -len("main.rs")] for path in self.files if path.endswith("src/main.rs") and importer.startswith(path[: -len("main.rs")])), "src/")
                for length in range(len(parts), 0, -1):
                    base = root + "/".join(parts[:length])
                    found = self._existing(base, (".rs",)) or self._existing(base + "/mod.rs")
                    if found:
                        return found
                return None
            return self._existing(posixpath.join(directory, spec), (".rs",)) or self._existing(posixpath.join(directory, spec, "mod.rs"))
        if lang == "go" and self.go_module and spec.startswith(self.go_module + "/"):
            target = spec[len(self.go_module) + 1:]
            return next((path for path in self.dirs.get(target, {}).get("files", []) if path.endswith(".go")), None)
        return None

    def _resolve_link(self, path: str, link: str) -> str | None:
        if link.startswith("[["):
            node = self.resolve_ref(link[2:])
            return node[5:] if node and node.startswith("file:") else None
        target = unquote(link)
        base = target.lstrip("/") if target.startswith("/") else posixpath.join(parent_of(path), target)
        return self._existing(base, (".md",))

    def resolve_ref(self, value: str) -> str | None:
        """Ссылка заметки или [[вики-ссылка]]: путь файла или папки, маршрут «GET /api/x», заголовок заметки, имя файла."""
        ref = value.strip().strip("[]").strip().replace("\\", "/")
        if not ref:
            return None
        if ref.startswith(("file:", "dir:", "api:", "note:")):
            return ref if self.has_node(ref) else None
        method = re.match(r"(?i)^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|WS|ANY)\s+(/\S*)$", ref)
        if method:
            key = api_id(method.group(1).upper(), method.group(2))
            if key in self.routes:
                return key
            matches = [item for item in self.match_api(method.group(2)) if self.routes[item]["method"] == method.group(1).upper()]
            return matches[0] if matches else None
        if ref.startswith("/"):
            matches = self.match_api(ref)
            if matches:
                return matches[0]
        path = ref.lstrip("./").rstrip("/") if ref not in {".", "./", "/"} else ""
        if path in self.files:
            return file_id(path)
        if path.lower() in self.by_lower:
            return file_id(self.by_lower[path.lower()])
        if path in self.dirs:
            return dir_id(path)
        lowered = ref.casefold()
        if lowered in self.notes_by_title:
            return note_id(self.notes_by_title[lowered])
        for candidate in (path.lower(), path.lower() + ".md"):
            if candidate in self.by_stem and len(self.by_stem[candidate]) >= 1:
                return file_id(sorted(self.by_stem[candidate], key=len)[0])
        return None

    def has_node(self, node: str) -> bool:
        kind, _, key = node.partition(":")
        if kind == "file":
            return key in self.files
        if kind == "dir":
            return key in self.dirs
        if kind == "api":
            return node in self.routes
        if kind == "note":
            return key.isdigit() and int(key) in self.notes_by_id
        return False

    def match_api(self, path: str) -> list[str]:
        segments = _segments(path)
        result = []
        for key in self._route_index.get(len(segments), []):
            route = _segments(self.routes[key]["path"])
            if all(a == b or a == "{}" or b == "{}" for a, b in zip(segments, route)):
                exact = sum(a == b for a, b in zip(segments, route))
                result.append((exact, key))
        return [key for _, key in sorted(result, key=lambda item: (-item[0], item[1]))]

    # ---------- производные сведения

    def files_under(self, directory: str) -> list[str]:
        if directory == "":
            return sorted(self.files)
        prefix = directory + "/"
        return sorted(path for path in self.files if path.startswith(prefix))

    def dir_dependencies(self, directory: str) -> tuple[Counter, Counter]:
        """Куда ведут импорты из папки наружу и откуда в неё: счётчики по папкам соседних файлов."""
        inside = set(self.files_under(directory))
        uses: Counter = Counter()
        used_by: Counter = Counter()
        for path in inside:
            for target in self.imports.get(path, []):
                if target not in inside:
                    uses[parent_of(target)] += 1
            for source in self.imported_by.get(path, []):
                if source not in inside:
                    used_by[parent_of(source)] += 1
        return uses, used_by

    def languages(self, paths: list[str] | None = None) -> Counter:
        return Counter((self.files[path].get("info") or {}).get("lang") or self.files[path].get("lang") or "text" for path in (paths if paths is not None else self.files))

    def node_label(self, node: str) -> str:
        kind, _, key = node.partition(":")
        if kind == "note":
            note = self.notes_by_id.get(int(key)) if key.isdigit() else None
            return note["title"] if note else node
        if kind == "api":
            return key
        if kind == "dir":
            return (key or ".") + "/"
        return key

    def summary_of(self, node: str) -> str:
        kind, _, key = node.partition(":")
        notes = [self.notes_by_id[item] for item in self.notes_by_node.get(node, []) if self.notes_by_id[item]["kind"] in {"file", "module", "api"}]
        if notes:
            return notes[-1]["body"].strip().splitlines()[0][:200] if notes[-1]["body"].strip() else notes[-1]["title"]
        if kind == "file":
            info = self.files.get(key, {}).get("info") or {}
            return info.get("summary", "")
        if kind == "dir":
            readme = next((path for path in self.dirs.get(key, {}).get("files", []) if PurePosixPath(path).stem.lower() in {"readme", "index", "__init__"}), None)
            return (self.files[readme].get("info") or {}).get("summary", "") if readme else ""
        if kind == "api":
            route = self.routes.get(node, {})
            return f"{route.get('handler') or ''} in {route.get('file')}".strip()
        return ""

    # ---------- важность и тесты

    @cached_property
    def rank(self) -> dict[str, float]:
        """PageRank по импортам и ссылкам документации, от 0 до 1: на файл опирается много других - он важнее."""
        nodes = list(self.files)
        if not nodes:
            return {}
        outgoing = {path: [item for item in [*self.imports.get(path, []), *self.links.get(path, [])] if item in self.files]
                    for path in nodes}
        count = len(nodes)
        score = dict.fromkeys(nodes, 1.0 / count)
        for _ in range(30):
            dangling = sum(score[path] for path in nodes if not outgoing[path])
            base = (1 - 0.85) / count + 0.85 * dangling / count
            current = dict.fromkeys(nodes, base)
            for path in nodes:
                targets = outgoing[path]
                if targets:
                    share = 0.85 * score[path] / len(targets)
                    for target in targets:
                        current[target] += share
            delta = sum(abs(current[path] - score[path]) for path in nodes)
            score = current
            if delta < 1e-6:
                break
        top = max(score.values()) or 1.0
        return {path: value / top for path, value in score.items()}

    def is_test(self, path: str) -> bool:
        return bool((self.files.get(path, {}).get("info") or {}).get("test"))

    @cached_property
    def _tests_by_stem(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        for path in self.files:
            if self.is_test(path):
                stem = PurePosixPath(path).name.lower()
                stem = re.sub(r"^test_|_test(?=\.)|\.(test|spec)(?=\.)|tests?(?=\.)", "", stem).split(".")[0]
                result[stem].append(path)
        return result

    def tests_for(self, path: str, *, depth: int = 2) -> list[tuple[str, str]]:
        """Тесты файла: (тест, причина) - импортирует его напрямую, через модуль-посредник или по имени."""
        found: dict[str, str] = {}
        frontier = [path]
        seen = {path}
        for level in range(1, depth + 1):
            following = []
            for current in frontier:
                for importer in self.imported_by.get(current, []):
                    if importer in seen:
                        continue
                    seen.add(importer)
                    if self.is_test(importer):
                        found.setdefault(importer, "imports it" if level == 1 else f"imports it through {current}")
                    else:
                        following.append(importer)
            frontier = following
        stem = PurePosixPath(path).name.lower().split(".")[0]
        for test in self._tests_by_stem.get(stem, []):
            if test != path:
                found.setdefault(test, "named after it")
        return sorted(found.items(), key=lambda item: (not item[1].startswith("imports it"), item[0]))

