"""Что карта отдаёт наружу: сжатый текст для агента и данные графа для интерфейса."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from koddi.projectmap.graph import Graph, api_id, dir_id, file_id, note_id, parent_of

DEFAULT_CHARS = 6000
MAX_CHARS = 20000
TREE_FILES = 6000
NOTE_NOTICE = "Notes are written by agents from earlier tasks: treat them as hints and verify in the files."


class Lines:
    """Строки ответа в пределах бюджета символов; что не влезло, считается и называется в конце."""

    def __init__(self, budget: int):
        self.budget = budget
        self.items: list[str] = []
        self.used = 0
        self.dropped = 0

    def add(self, line: str) -> bool:
        if self.used + len(line) + 1 > self.budget:
            self.dropped += 1
            return False
        self.items.append(line)
        self.used += len(line) + 1
        return True

    def text(self, hint: str = "") -> str:
        tail = [f"... {self.dropped} more lines omitted. {hint}".rstrip()] if self.dropped else []
        return "\n".join(self.items + tail)


def _symbols(info: dict, limit: int = 8) -> str:
    names = [item[0] for item in info.get("symbols", []) if item[1] != "method"][:limit]
    return ", ".join(names)


def _file_line(graph: Graph, path: str, *, symbols: int = 6) -> str:
    info = graph.files[path].get("info") or {}
    summary = graph.summary_of(file_id(path))
    parts = [path]
    if summary:
        parts.append("- " + summary)
    names = _symbols(info, symbols)
    if names:
        parts.append(f"[{names}]")
    return " ".join(parts)


def _note_line(graph: Graph, note: dict) -> str:
    targets = ", ".join(graph.node_label(item) for item in graph.note_targets.get(note["id"], [])[:4])
    stale = " (stale: linked file changed)" if note["id"] in graph.stale_notes else ""
    body = re.sub(r"\s+", " ", note.get("body", "")).strip()
    body = body[:220] + ("…" if len(body) > 220 else "")
    return f"- #{note['id']} [{note['kind']}] {note['title']}{stale}" + (f" -> {targets}" if targets else "") + (f": {body}" if body else "")


def _module_rows(graph: Graph, limit: int) -> list[str]:
    """Папки сверху вниз, пока строк не больше limit; глубже - свёрнуто в «+N папок»."""
    rows: list[tuple[str, int]] = []
    frontier = [""]
    while frontier and len(rows) < limit:
        nxt = []
        for directory in frontier:
            children = sorted(graph.dirs[directory]["dirs"], key=lambda item: -graph.dirs[item]["total"])
            for child in children:
                if len(rows) >= limit:
                    break
                rows.append((child, child.count("/")))
                nxt.append(child)
        frontier = nxt
    ordered = sorted(rows, key=lambda item: item[0])
    lines = []
    for directory, depth in ordered:
        data = graph.dirs[directory]
        hidden = sum(1 for child in data["dirs"] if child not in {row[0] for row in rows})
        langs = ", ".join(lang for lang, _ in graph.languages(graph.files_under(directory)).most_common(2))
        summary = graph.summary_of(dir_id(directory))
        line = f"{'  ' * depth}{directory}/ ({data['total']} files; {langs})"
        if summary:
            line += " - " + summary
        if hidden:
            line += f" +{hidden} subfolders"
        lines.append(line)
    return lines


def overview(graph: Graph, state: dict, *, max_chars: int = DEFAULT_CHARS) -> str:
    out = Lines(max_chars)
    notes = graph.notes
    status = state.get("status", "ready")
    out.add(
        f"Project map v{graph.version}: {len(graph.files)} files, {len(graph.dirs) - 1} folders, "
        f"{len(graph.routes)} API routes, {len(notes)} notes. Status: {status}"
        + (" (file limit reached, some files are not mapped)" if state.get("truncated") else "")
        + (f". Updated {state['built_at']} UTC" if state.get("built_at") else "")
    )
    out.add("Languages: " + ", ".join(f"{lang} {count}" for lang, count in graph.languages().most_common(8)))
    manifests = [
        path for path in sorted(graph.files, key=lambda item: (item.count("/"), item))
        if PurePosixPath(path).name.lower() in {"package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "setup.py", "requirements.txt", "makefile", "dockerfile", "readme.md", "agents.md", "claude.md"}
    ][:12]
    if manifests:
        out.add("Key files:")
        for path in manifests:
            out.add("- " + _file_line(graph, path, symbols=8))
    decisions = [note for note in notes if note["kind"] in {"decision", "gotcha", "topic"}]
    if decisions:
        out.add("Notes (decisions, pitfalls, topics). " + NOTE_NOTICE)
        for note in decisions[-15:]:
            out.add(_note_line(graph, note))
    out.add("Folders (files; main languages):")
    for line in _module_rows(graph, 45):
        out.add(line)
    if graph.routes:
        prefixes = Counter("/".join(route["path"].split("/")[:3]) for route in graph.routes.values())
        out.add("API routes by prefix: " + ", ".join(f"{prefix} {count}" for prefix, count in prefixes.most_common(12)))
    out.add("Next: project.map with path=<folder or file> for details, query=<words> to find code by topic, "
            "api=<route prefix> for endpoints and their callers. Then read only the lines you need.")
    return out.text("Narrow with path= or query=.")


def _resolve_or_none(graph: Graph, ref: str) -> str | None:
    node = graph.resolve_ref(ref)
    if node:
        return node
    # путь из Windows или с ./ в начале
    cleaned = ref.replace("\\", "/").lstrip("./")
    return graph.resolve_ref(cleaned) if cleaned != ref else None


def describe(graph: Graph, ref: str, *, max_chars: int = DEFAULT_CHARS) -> tuple[str, str | None]:
    node = _resolve_or_none(graph, ref)
    if not node:
        similar = search(graph, PurePosixPath(ref.replace("\\", "/")).name, limit=8, max_chars=1500, header=False)
        return (f"'{ref}' is not in the project map." + ("\nSimilar:\n" + similar if similar else ""), None)
    kind, _, key = node.partition(":")
    out = Lines(max_chars)
    notes = [graph.notes_by_id[item] for item in graph.notes_by_node.get(node, [])]
    if kind == "file":
        item = graph.files[key]
        info = item.get("info") or {}
        out.add(f"{key} - {info.get('lang', item.get('lang'))}, {info.get('lines', '?')} lines, {item.get('size', 0)} bytes, sha256 {str(item.get('sha256', ''))[:12]}")
        if info.get("summary"):
            out.add("Summary: " + info["summary"])
        if info.get("skipped"):
            out.add(f"Not parsed ({info['skipped']}); read it by range if needed.")
        if notes:
            out.add("Notes. " + NOTE_NOTICE)
            for note in notes:
                out.add(_note_line(graph, note))
        symbols = info.get("symbols", [])
        if symbols:
            out.add("Symbols (name kind line): " + "; ".join(f"{name} {kind_} L{line}" if line else f"{name} {kind_}" for name, kind_, line in symbols))
        routes = [route for route in graph.routes.values() if route["file"] == key]
        if routes:
            out.add("Defines API: " + "; ".join(f"{route['method']} {route['path']} L{route['line']}" for route in routes))
        if graph.calls.get(key):
            out.add("Calls API: " + "; ".join(item[4:] for item in graph.calls[key]))
        if graph.imports.get(key):
            out.add("Imports: " + ", ".join(graph.imports[key]))
        if graph.externals.get(key):
            out.add("External packages: " + ", ".join(graph.externals[key]))
        if graph.imported_by.get(key):
            out.add("Imported by: " + ", ".join(sorted(graph.imported_by[key])))
            out.add(f"Importance (PageRank over imports, 0-1): {graph.rank.get(key, 0.0):.2f}")
        if info.get("test"):
            out.add("This is a test file.")
        else:
            tests = graph.tests_for(key)
            if tests:
                out.add("Tests: " + "; ".join(f"{test} ({why})" for test, why in tests[:8]))
        if graph.links.get(key):
            out.add("Links to: " + ", ".join(graph.links[key]))
        if graph.linked_from.get(key):
            out.add("Linked from: " + ", ".join(sorted(graph.linked_from[key])))
        if info.get("packages"):
            out.add("Dependencies: " + ", ".join(info["packages"]))
    elif kind == "dir":
        data = graph.dirs[key]
        langs = ", ".join(f"{lang} {count}" for lang, count in graph.languages(graph.files_under(key)).most_common(4))
        out.add(f"{key or '.'}/ - {data['total']} files ({langs})")
        summary = graph.summary_of(node)
        if summary:
            out.add("Summary: " + summary)
        if notes:
            out.add("Notes. " + NOTE_NOTICE)
            for note in notes:
                out.add(_note_line(graph, note))
        uses, used_by = graph.dir_dependencies(key)
        if uses:
            out.add("Uses: " + ", ".join(f"{(name or '.')}/ ({count})" for name, count in uses.most_common(10)))
        if used_by:
            out.add("Used by: " + ", ".join(f"{(name or '.')}/ ({count})" for name, count in used_by.most_common(10)))
        if data["dirs"]:
            out.add("Subfolders:")
            for child in sorted(data["dirs"]):
                summary = graph.summary_of(dir_id(child))
                out.add(f"- {child}/ ({graph.dirs[child]['total']} files)" + (f" - {summary}" if summary else ""))
        if data["files"]:
            out.add("Files:")
            for path in data["files"]:
                out.add("- " + _file_line(graph, path))
        routes = [route for route in graph.routes.values() if route["file"].startswith(key + "/" if key else "")]
        if routes:
            out.add(f"API defined here: {len(routes)} routes; use api=<prefix> to list them.")
    elif kind == "api":
        route = graph.routes[node]
        out.add(f"{route['method']} {route['path']} - handler {route.get('handler') or '?'} in {route['file']} L{route.get('line')}")
        callers = graph.callers.get(node, [])
        out.add("Called from: " + (", ".join(sorted(callers)) if callers else "no callers found in the map"))
        for note in notes:
            out.add(_note_line(graph, note))
    else:
        note = graph.notes_by_id[int(key)]
        out.add(_note_line(graph, note))
        out.add(NOTE_NOTICE)
        out.add(note.get("body", ""))
    return out.text("Use a narrower path or file.read_range."), node


def _terms(query: str) -> list[str]:
    return list(dict.fromkeys(term for term in re.split(r"[\s,;:()\[\]{}\"'`]+", query.casefold()) if len(term) >= 2))[:12]


def search(graph: Graph, query: str, *, limit: int = 25, max_chars: int = DEFAULT_CHARS, kinds: set[str] | None = None, header: bool = True) -> str:
    terms = _terms(query)
    if not terms:
        return ""
    scored: list[tuple[float, str, str]] = []
    for path, item in graph.files.items() if not kinds or "file" in kinds else ():
        info = item.get("info") or {}
        lowered = path.lower()
        name = PurePosixPath(lowered).name
        summary = (info.get("summary") or "").casefold()
        score, matched, hits = 0.0, 0, []
        for term in terms:
            hit = False
            if term in name:
                score += 6
                hit = True
            elif term in lowered:
                score += 3
                hit = True
            symbol_hits = [symbol for symbol in info.get("symbols", []) if term in str(symbol[0]).casefold()][:3]
            if symbol_hits:
                score += 4 + len(symbol_hits)
                hits.extend(f"{symbol[0]} L{symbol[2]}" for symbol in symbol_hits if symbol[2])
                hit = True
            if term in summary:
                score += 2
                hit = True
            matched += hit
        if matched:
            text = _file_line(graph, path, symbols=4) + (f" (matches: {', '.join(dict.fromkeys(hits))})" if hits else "")
            scored.append((score + 10 * matched, file_id(path), text))
    for directory in graph.dirs if not kinds or "dir" in kinds else ():
        if not directory:
            continue
        lowered = directory.lower()
        matched = sum(term in lowered for term in terms)
        if matched:
            score = sum(5 if term in PurePosixPath(lowered).name else 2 for term in terms if term in lowered)
            scored.append((score + 10 * matched - 1, dir_id(directory), f"{directory}/ ({graph.dirs[directory]['total']} files)"))
    for key, route in graph.routes.items() if not kinds or "api" in kinds else ():
        haystack = f"{route['method']} {route['path']} {route.get('handler') or ''}".casefold()
        matched = sum(term in haystack for term in terms)
        if matched:
            scored.append((5 * matched + 10 * matched, key, f"{route['method']} {route['path']} -> {route['file']} L{route.get('line')}"))
    for note in graph.notes if not kinds or "note" in kinds else ():
        title, body = note["title"].casefold(), note.get("body", "").casefold()
        matched = sum(term in title or term in body for term in terms)
        if matched:
            score = sum(6 if term in title else 2 for term in terms if term in title or term in body)
            scored.append((score + 10 * matched + 1, note_id(note["id"]), _note_line(graph, note)[2:]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    out = Lines(max_chars)
    if header:
        out.add(f"Matches for '{query}' ({min(len(scored), limit)} of {len(scored)}):")
    for _, node, text in scored[:limit]:
        out.add(f"- [{node.partition(':')[0]}] {text}")
    if header and not scored:
        out.add("Nothing found. Try other words, a folder path, or search.text for exact code.")
    return out.text("Add words or set path= to narrow.")


def api_list(graph: Graph, prefix: str, *, max_chars: int = DEFAULT_CHARS) -> str:
    prefix = prefix.strip()
    routes = sorted((route for route in graph.routes.values() if route["path"].startswith(prefix or "/")), key=lambda route: (route["path"], route["method"]))
    out = Lines(max_chars)
    out.add(f"API routes starting with {prefix or '/'}: {len(routes)}")
    for route in routes:
        callers = graph.callers.get(api_id(route["method"], route["path"]), [])
        out.add(f"- {route['method']} {route['path']} -> {route['file']} L{route.get('line')} {route.get('handler') or ''}".rstrip()
                + (f"; called from {', '.join(sorted(callers)[:4])}" + (f" +{len(callers) - 4}" if len(callers) > 4 else "") if callers else ""))
    return out.text("Use a longer prefix.")


def runtime_snapshot(graph: Graph) -> dict:
    """Короткий обзор для снимка контекста агента: меняется, только когда меняются папки или заметки."""
    folders = []
    for line in _module_rows(graph, 24):
        folders.append(re.sub(r" \(\d+ files; ", " (", line.strip()))
    decisions = [f"#{note['id']} {note['title']}" for note in graph.notes if note["kind"] in {"decision", "gotcha"}][-10:]
    value = {
        "tool": "project.map",
        "note": "Always-updated map of this workspace (folders, files, symbols, imports, API routes, notes). "
                "Call project.map before exploring with directory or search tools.",
        "folders": folders,
    }
    if decisions:
        value["notes"] = decisions
    return value


# ---------- данные для интерфейса

def _weight(record: dict) -> int:
    info = record.get("info") or {}
    # площадь плитки - строки; у бинарных и слишком больших файлов строк нет, считаем по байтам
    return max(1, int(info.get("lines") or 0) or (record.get("size") or 0) // 40)


def treemap_payload(graph: Graph, *, limit: int = TREE_FILES) -> dict:
    """Файлы для мозаики в окне: площадь, описание, связи. В огромном проекте мелкие файлы складываются в «ещё N» своей папки."""
    paths = sorted(graph.files, key=lambda path: (-_weight(graph.files[path]), path))
    kept = sorted(paths[:limit])
    index = {path: position for position, path in enumerate(kept)}
    routes: Counter = Counter(route["file"] for route in graph.routes.values())
    files = []
    for path in kept:
        record = graph.files[path]
        info = record.get("info") or {}
        item: dict = {"p": path, "w": _weight(record), "lang": info.get("lang") or record.get("lang") or "text"}
        if info.get("lines"):
            item["l"] = info["lines"]
        summary = graph.summary_of(file_id(path))
        if summary:
            item["s"] = summary[:160]
        imports = [index[target] for target in graph.imports.get(path, []) if target in index]
        if imports:
            item["i"] = imports[:60]
        if routes[path]:
            item["api"] = routes[path]
        notes = [graph.notes_by_id[note]["title"] for note in graph.notes_by_node.get(file_id(path), [])]
        if notes:
            item["n"] = notes[:5]
        files.append(item)
    rest: dict[str, list[int]] = {}
    for path in paths[limit:]:
        bucket = rest.setdefault(parent_of(path), [0, 0])
        bucket[0] += 1
        bucket[1] += _weight(graph.files[path])
    return {
        "version": graph.version,
        "files": files,
        "rest": [{"p": folder, "count": count, "w": weight} for folder, (count, weight) in sorted(rest.items())],
        "totals": {"files": len(graph.files), "dirs": len(graph.dirs) - 1, "api": len(graph.routes), "notes": len(graph.notes)},
    }
