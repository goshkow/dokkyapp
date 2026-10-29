"""Поиск контекста для агента: релевантные фрагменты кода под бюджет символов, с источником каждого.

Кандидаты - полнотекстовый поиск FTS5 по термам фрагментов (символ, путь, тело) и точные совпадения имён
объявлений. Порядок - сочетание BM25, важности файла в графе импортов (PageRank), близости к файлам в
фокусе (явно переданные или изменённые в рабочей копии) и штрафа тестам и документации, если о них не
спрашивали. Текст читается из файла в момент выдачи и сверяется с хешем индекса: устаревший фрагмент
не выдаётся молча.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from koddi.codeintel.python import split_lines
from koddi.policy.paths import WorkspacePaths
from koddi.projectmap import chunks as chunk_index
from koddi.storage.db import db

DEFAULT_CHARS = 12000
MAX_CHARS = 40000
MAX_PER_FILE = 3
CACHE_SECONDS = 120.0
CACHE_SIZE = 64
IDENT = re.compile(r"[A-Za-z_$][\w$.]{2,}")
TEST_WORDS = {"test", "tests", "pytest", "spec", "fixture", "e2e"}
DOC_WORDS = {"doc", "docs", "readme", "documentation", "guide", "changelog"}
_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_cache_lock = threading.Lock()


class RetrieveError(RuntimeError):
    pass


def _match_expression(words: list[str]) -> str:
    # каждое слово в кавычках (FTS5 не разбирает его как оператор), длинные - ещё и как префикс
    parts = []
    for word in words:
        quoted = '"' + word.replace('"', "") + '"'
        parts.append(f"{quoted}*" if len(word) >= 4 else quoted)
    return " OR ".join(parts)


def _candidates(project_id: int, words: list[str], identifiers: list[str]) -> tuple[dict[int, dict], str]:
    found: dict[int, dict] = {}
    with db() as conn:
        provider = "fts5" if chunk_index.fts_available(conn) else "symbols"
        if provider == "fts5" and words:
            rows = conn.execute(
                "SELECT c.id,c.path,c.start_line,c.end_line,c.symbol,c.kind,c.sha256,bm25(context_fts, 3.0, 6.0, 1.0) AS score "
                "FROM context_fts JOIN context_chunks c ON c.id=context_fts.rowid "
                "WHERE context_fts MATCH ? AND c.project_id=? ORDER BY score LIMIT 400",
                (_match_expression(words), project_id),
            ).fetchall()
            for row in rows:
                item = dict(row)
                item["text_score"] = -float(item.pop("score"))
                found[item["id"]] = item
        for name in identifiers[:8]:
            rows = conn.execute(
                "SELECT id,path,start_line,end_line,symbol,kind,sha256 FROM context_chunks "
                "WHERE project_id=? AND (symbol=? OR symbol LIKE ? ESCAPE '\\') LIMIT 40",
                (project_id, name, "%." + name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")),
            ).fetchall()
            for row in rows:
                item = found.setdefault(row["id"], {**dict(row), "text_score": 0.0})
                item["exact"] = True
        if provider == "symbols" and not found:
            # без FTS5: совпадение термов запроса с путём и символом фрагмента
            for word in words[:8]:
                like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                for row in conn.execute(
                    "SELECT id,path,start_line,end_line,symbol,kind,sha256 FROM context_chunks WHERE project_id=? "
                    "AND (lower(symbol) LIKE ? ESCAPE '\\' OR lower(path) LIKE ? ESCAPE '\\') LIMIT 100",
                    (project_id, like, like),
                ):
                    item = found.setdefault(row["id"], {**dict(row), "text_score": 0.0})
                    item["text_score"] += 1.0
    return found, provider


def _neighbors(graph, focus: list[str]) -> set[str]:
    near: set[str] = set()
    for path in focus:
        near.update(graph.imports.get(path, []))
        near.update(graph.imported_by.get(path, []))
    return near - set(focus)


def _score(items: dict[int, dict], graph, focus: list[str], wants_tests: bool, wants_docs: bool) -> list[dict]:
    if not items:
        return []
    top = max(item["text_score"] for item in items.values()) or 1.0
    focus_set = set(focus)
    near = _neighbors(graph, focus)
    ranked = []
    for item in items.values():
        reasons = []
        score = item["text_score"] / top
        if item["text_score"] > 0:
            reasons.append(f"text match {score:.2f}")
        if item.get("exact"):
            score += 1.0
            reasons.append("declaration name matches")
        importance = graph.rank.get(item["path"], 0.0)
        if importance > 0.05:
            score += 0.35 * math.sqrt(importance)
            reasons.append(f"central file (rank {importance:.2f})")
        if item["path"] in focus_set:
            score += 0.6
            reasons.append("in focus")
        elif item["path"] in near:
            score += 0.3
            reasons.append("imports or is imported by a focus file")
        lang = (graph.files.get(item["path"], {}).get("lang") or "")
        factor = 1.0
        if graph.is_test(item["path"]) and not wants_tests:
            factor *= 0.6
        if lang == "markdown" and not wants_docs:
            factor *= 0.7
        if item["kind"] in {"window", "rest"}:
            factor *= 0.85
        item["factor"] = factor
        item["score"] = round(score * factor, 4)
        item["reasons"] = reasons
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["score"], item["path"], item["start_line"]))
    return ranked


def _rerank(head: list[dict], words: list[str], root: Path, access: WorkspacePaths, texts: dict) -> None:
    """Уточнение лучших кандидатов: какая доля слов запроса есть во фрагменте и в имени символа."""
    if not head or not words:
        return
    wanted = set(words)
    for item in head:
        content = _read(root, access, item["path"], texts)
        if content is None or content[0] != item["sha256"]:
            continue
        lines = split_lines(content[1])[item["start_line"] - 1:item["end_line"]]
        present = set(chunk_index.terms("\n".join(lines)).split()) | set(chunk_index.terms(item["path"]).split())
        coverage = len(wanted & present) / len(wanted)
        named = len(wanted & set(chunk_index.terms(item["symbol"] or "").split()))
        item["score"] = round(item["score"] + (0.6 * coverage + 0.25 * min(named, 2)) * item.get("factor", 1.0), 4)
        if coverage >= 0.5:
            item["reasons"].append(f"covers {coverage:.0%} of the query words")
        # длинный фрагмент с редкими совпадениями проигрывает короткому и точному
        if len(lines) > 120:
            item["score"] = round(item["score"] * 0.85, 4)
    head.sort(key=lambda item: (-item["score"], item["path"], item["start_line"]))


def _read(root: Path, access: WorkspacePaths, path: str, cache: dict[str, tuple[str, str] | None]) -> tuple[str, str] | None:
    if path not in cache:
        target = root / path
        try:
            if not access.accessible(target):
                cache[path] = None
            else:
                data = target.read_bytes()
                cache[path] = (hashlib.sha256(data).hexdigest(), data.decode("utf-8", errors="replace"))
        except OSError:
            cache[path] = None
    return cache[path]


def _still_fresh(root: Path, result: dict[str, Any]) -> bool:
    access = WorkspacePaths(root)
    texts: dict[str, tuple[str, str] | None] = {}
    for item in result.get("items", []):
        content = _read(root, access, item["path"], texts)
        if content is None or content[0] != item["sha256"]:
            return False
    return True


def retrieve(project_id: int, root: Path, owner: str, query: str, *, focus: list[str] | None = None,
             max_chars: int = DEFAULT_CHARS, limit: int = 12, include_tests: bool | None = None,
             cancel: threading.Event | None = None) -> dict[str, Any]:
    from koddi.projectmap import service as map_service

    query = str(query or "").strip()
    if not query or len(query) > 1000:
        raise RetrieveError("query must be 1-1000 characters")
    max_chars = max(1000, min(int(max_chars or DEFAULT_CHARS), MAX_CHARS))
    limit = max(1, min(int(limit or 12), 40))
    focus = [item for item in dict.fromkeys(focus or []) if item][:40]
    current = map_service.ensure(project_id, root, wait=20.0, cancel=cancel)
    if not current.get("built_at"):
        raise RetrieveError("the project index is still being built; try again in a few seconds")
    graph = map_service.graph(project_id, owner)
    words = chunk_index.query_terms(query)
    lowered = set(words)
    wants_tests = include_tests if include_tests is not None else bool(lowered & TEST_WORDS)
    wants_docs = bool(lowered & DOC_WORDS)
    # точное имя объявления - только то, что похоже на код (snake_case, camelCase, точка) или запрос из 1-2 слов
    raw = list(dict.fromkeys(IDENT.findall(query)))
    identifiers = [item for item in raw if item.lower() not in chunk_index.STOP
                   and (len(raw) <= 2 or "_" in item or "." in item or any(char.isupper() for char in item[1:]))]
    key = (project_id, owner, graph.version, query, tuple(focus), max_chars, limit, wants_tests)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            _cache.move_to_end(key)
        else:
            cached = None
    # карта могла ещё не заметить правку: выданные фрагменты сверяем с файлами, иначе кэш устарел
    if cached and _still_fresh(root, cached[1]):
        return {**cached[1], "cached": True}
    if not words and not identifiers:
        raise RetrieveError("the query has no searchable words")
    candidates, provider = _candidates(project_id, words, identifiers)
    ranked = _score(candidates, graph, focus, wants_tests, wants_docs)
    access = WorkspacePaths(root)
    texts: dict[str, tuple[str, str] | None] = {}
    head = ranked[:80]
    _rerank(head, words, root, access, texts)
    ranked = head + ranked[80:]
    selected: list[dict] = []
    per_file: dict[str, list[tuple[int, int]]] = {}
    used = 0
    stale: set[str] = set()
    skipped = 0
    for item in ranked:
        if cancel is not None and cancel.is_set():
            raise RetrieveError("operation cancelled")
        if len(selected) >= limit or used >= max_chars:
            break
        ranges = per_file.setdefault(item["path"], [])
        if len(ranges) >= MAX_PER_FILE or any(start <= item["start_line"] and item["end_line"] <= end for start, end in ranges):
            continue
        content = _read(root, access, item["path"], texts)
        if content is None:
            continue
        digest, text = content
        if digest != item["sha256"]:
            # файл изменился после индексации: фрагмент мог сместиться, его не выдаём
            stale.add(item["path"])
            continue
        lines = split_lines(text)
        body = "\n".join(lines[item["start_line"] - 1:item["end_line"]])
        # один огромный фрагмент не забирает весь бюджет, пока есть другие кандидаты
        remaining = min(max_chars - used, max(1500, int(max_chars * 0.45)) if len(ranked) > 1 else max_chars)
        truncated = False
        if len(body) > remaining:
            if selected and remaining < 600:
                skipped += 1
                continue
            body = body[:remaining].rsplit("\n", 1)[0]
            truncated = True
        end_line = item["start_line"] + body.count("\n")
        ranges.append((item["start_line"], end_line))
        used += len(body)
        selected.append({
            "path": item["path"], "lines": [item["start_line"], end_line], "symbol": item["symbol"] or None,
            "kind": item["kind"], "score": item["score"], "reasons": item["reasons"], "sha256": digest,
            "text": body, **({"truncated": True} if truncated else {}),
        })
    related = []
    top_files = list(dict.fromkeys(item["path"] for item in selected))[:3]
    for path in top_files:
        for test, why in graph.tests_for(path)[:3]:
            if test not in top_files:
                related.append({"path": test, "reason": f"test for {path} ({why})"})
        for note_ref in graph.notes_by_node.get(f"file:{path}", [])[:2]:
            note = graph.notes_by_id.get(note_ref)
            if note:
                related.append({"note": note["title"], "kind": note["kind"], "reason": f"note on {path}",
                                **({"stale": True} if note_ref in graph.stale_notes else {})})
    result: dict[str, Any] = {
        "ok": True, "query": query, "provider": provider, "map_version": graph.version,
        "items": selected, "related": related[:10],
        "budget": {"max_chars": max_chars, "used": used},
        "candidates": len(ranked), "truncated": len(selected) < len(ranked) and (used >= max_chars or len(selected) >= limit),
    }
    warnings = []
    if stale:
        map_service.mark_dirty(project_id)
        warnings.append(f"{len(stale)} file(s) changed after indexing and were skipped: {', '.join(sorted(stale)[:5])}; call again")
    if current.get("status") == "building":
        warnings.append("the first index build is still running; results may be incomplete")
    if provider != "fts5":
        warnings.append("full-text search (SQLite FTS5) is unavailable; matched by names and paths only")
    if not selected:
        warnings.append("nothing relevant found; rephrase with identifiers, file names or domain words")
    if warnings:
        result["warnings"] = warnings
    if stale:
        return result
    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result
