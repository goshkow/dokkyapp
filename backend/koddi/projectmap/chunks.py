"""Фрагменты файлов для поиска контекста: границы по объявлениям, термы для FTS5 и индекс в SQLite.

Текст кода в базу не попадает: таблица FTS5 хранит только термы (contentless), а фрагмент читается из файла
при выдаче и сверяется с хешем. Если SQLite собран без FTS5, индекс не создаётся и поиск работает по карте.
"""

from __future__ import annotations

import ast
import bisect
import re
import sqlite3
import threading

from koddi.codeintel.python import split_lines

MAX_CHUNKS = 400
MAX_SCRIPT_CHARS = 2_000_000
WINDOW = 60
OVERLAP = 10
BIG_CLASS = 80
MAX_TERMS = 4000
WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|[0-9]+|[^\W\d_]{2,}", re.UNICODE)
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
STOP = {
    "the", "and", "for", "self", "return", "import", "from", "def", "class", "const", "let", "var", "function",
    "this", "none", "true", "false", "null", "undefined", "async", "await", "if", "else", "elif", "in", "of",
    "is", "not", "or", "to", "a", "an", "new", "export", "default", "pass", "with", "as",
}
_lock = threading.Lock()
_fts: dict[str, bool] = {}


# ---------- термы

def terms(text: str, limit: int = MAX_TERMS) -> str:
    """Слова и части идентификаторов (getUserId -> get user id getuserid) в нижнем регистре."""
    seen: list[str] = []
    for match in WORD.finditer(text):
        word = match.group(0)
        lowered = word.lower()
        parts = [lowered]
        pieces = [piece.lower() for chunk in word.split("_") if chunk for piece in CAMEL.findall(chunk)]
        if len(pieces) > 1:
            parts.extend(pieces)
        for part in parts:
            if len(part) > 1 and part not in STOP:
                seen.append(part)
        if len(seen) >= limit:
            break
    return " ".join(seen)


def query_terms(text: str) -> list[str]:
    result = []
    for part in terms(text, 64).split():
        if part not in result:
            result.append(part)
    return result[:24]


# ---------- границы фрагментов

def _windows(total: int, start: int = 1) -> list[list]:
    chunks = []
    line = start
    while line <= total:
        end = min(total, line + WINDOW - 1)
        chunks.append([line, end, "", "window"])
        if end == total:
            break
        line = end - OVERLAP + 1
    return chunks


def python_chunks(tree: ast.Module, total: int) -> list[list]:
    chunks: list[list] = []
    covered_until = 0
    header_end = 0
    for node in tree.body:
        start = min([node.lineno, *(item.lineno for item in getattr(node, "decorator_list", []))])
        end = node.end_lineno or node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append([start, end, node.name, "function"])
        elif isinstance(node, ast.ClassDef):
            methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if end - start + 1 > BIG_CLASS and methods:
                first = min([methods[0].lineno, *(item.lineno for item in methods[0].decorator_list)])
                chunks.append([start, max(start, first - 1), node.name, "class"])
                for item in methods:
                    item_start = min([item.lineno, *(dec.lineno for dec in item.decorator_list)])
                    chunks.append([item_start, item.end_lineno or item.lineno, f"{node.name}.{item.name}", "method"])
            else:
                chunks.append([start, end, node.name, "class"])
        else:
            if not chunks:
                header_end = end
            continue
        covered_until = max(covered_until, end)
    if header_end:
        chunks.insert(0, [1, header_end, "", "module"])
    # длинные участки модуля без объявлений (скрипты, конфигурация) - окнами
    if covered_until < total and total - covered_until > 15:
        chunks.extend(_windows(total, covered_until + 1))
    return _split_long(chunks)


def _split_long(chunks: list[list]) -> list[list]:
    result = []
    for start, end, symbol, kind in chunks:
        if end - start + 1 <= WINDOW * 3:
            result.append([start, end, symbol, kind])
            continue
        for piece in _windows(end - start + 1):
            result.append([start + piece[0] - 1, start + piece[1] - 1, symbol, kind])
    return result[:MAX_CHUNKS]


def script_chunks(text: str, total: int) -> list[list]:
    from koddi.codeintel import jslex

    if len(text) > MAX_SCRIPT_CHARS:
        return _windows(total)  # сборки и минифицированные файлы - окнами, без разбора скобок
    masked = jslex.mask(text)
    # переводы строк те же, что у jslex.declarations: иначе номера строк разъедутся на файлах с одиночным \r
    starts = [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", masked)]
    chunks = []
    last_end = 0
    for name, kind, line, col in jslex.declarations(masked):
        if line <= last_end or kind in {"let", "var"}:
            continue
        offset = starts[line - 1] + col
        brace = masked.find("{", offset)
        semicolon = masked.find(";", offset)
        if brace < 0 or (0 <= semicolon < brace and kind in {"const", "type"}):
            end_offset = semicolon if semicolon >= 0 else offset
        else:
            depth, index = 0, brace
            while index < len(masked):
                char = masked[index]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            end_offset = index
        end = bisect.bisect_right(starts, end_offset)
        if kind == "const" and end - line < 2:
            continue  # короткие константы входят в окна, а не в отдельные фрагменты
        chunks.append([line, end, name, kind])
        last_end = end
    if not chunks:
        return _windows(total)
    covered = {line for start, end, _, _ in chunks for line in range(start, end + 1)}
    gap = [line for line in range(1, total + 1) if line not in covered]
    if len(gap) > 15:
        chunks.append([gap[0], gap[-1], "", "rest"])
    return _split_long(sorted(chunks))


def markdown_chunks(text: str, total: int) -> list[list]:
    headings = [(text.count("\n", 0, match.start()) + 1, match.group(2).strip())
                for match in re.finditer(r"^(#{1,3})\s+(.+?)\s*#*\s*$", text, re.M)]
    if not headings:
        return _windows(total)
    chunks = []
    if headings[0][0] > 1:
        chunks.append([1, headings[0][0] - 1, "", "section"])
    for index, (line, title) in enumerate(headings):
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else total
        chunks.append([line, max(line, end), title[:120], "section"])
    return _split_long(chunks)


def chunk_ranges(lang: str, text: str, tree: ast.Module | None = None) -> list[list]:
    total = len(split_lines(text))
    if total == 0:
        return []
    if lang == "python" and tree is not None:
        return python_chunks(tree, total)
    if lang in {"typescript", "javascript"}:
        return script_chunks(text, total)
    if lang == "markdown":
        return markdown_chunks(text, total)
    return _windows(total)


# ---------- индекс

def fts_available(conn: sqlite3.Connection) -> bool:
    """Создаёт таблицу FTS5 один раз на файл базы; False, если FTS5 в этой сборке SQLite нет."""
    key = conn.execute("PRAGMA database_list").fetchone()[2] or ":memory:"
    with _lock:
        if key in _fts:
            return _fts[key]
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(path, symbol, body, content='', "
                "contentless_delete=1, tokenize='unicode61 remove_diacritics 2')"
            )
            ok = True
        except sqlite3.OperationalError:
            try:
                # SQLite старше 3.43: без contentless_delete удаление идёт через обычную таблицу термов
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(path, symbol, body, "
                    "tokenize='unicode61 remove_diacritics 2')"
                )
                ok = True
            except sqlite3.OperationalError:
                ok = False
        _fts[key] = ok
        return ok


def remove(conn: sqlite3.Connection, project_id: int, paths: list[str]) -> None:
    if not paths or not fts_available(conn):
        return
    for index in range(0, len(paths), 400):
        batch = paths[index:index + 400]
        marks = ",".join("?" * len(batch))
        ids = [row[0] for row in conn.execute(
            f"SELECT id FROM context_chunks WHERE project_id=? AND path IN ({marks})", (project_id, *batch))]
        for start in range(0, len(ids), 400):
            part = ids[start:start + 400]
            conn.execute(f"DELETE FROM context_fts WHERE rowid IN ({','.join('?' * len(part))})", part)
        conn.execute(f"DELETE FROM context_chunks WHERE project_id=? AND path IN ({marks})", (project_id, *batch))


def index_file(conn: sqlite3.Connection, project_id: int, path: str, sha256: str, text: str, chunks: list[list]) -> None:
    remove(conn, project_id, [path])
    if not chunks or not fts_available(conn):
        return
    lines = split_lines(text)
    path_terms = terms(path.replace("/", " "))
    for start, end, symbol, kind in chunks[:MAX_CHUNKS]:
        body = "\n".join(lines[start - 1:end])
        cursor = conn.execute(
            "INSERT INTO context_chunks(project_id,path,start_line,end_line,symbol,kind,sha256) VALUES (?,?,?,?,?,?,?)",
            (project_id, path, start, end, symbol, kind, sha256),
        )
        conn.execute("INSERT INTO context_fts(rowid,path,symbol,body) VALUES (?,?,?,?)",
                     (cursor.lastrowid, path_terms, terms(symbol), terms(body)))


def prune(conn: sqlite3.Connection) -> None:
    """Термы фрагментов удалённого каскадом проекта: строк context_chunks уже нет."""
    if fts_available(conn):
        conn.execute("DELETE FROM context_fts WHERE rowid NOT IN (SELECT id FROM context_chunks)")


def clear(conn: sqlite3.Connection, project_id: int) -> None:
    paths = [row[0] for row in conn.execute("SELECT DISTINCT path FROM context_chunks WHERE project_id=?", (project_id,))]
    remove(conn, project_id, paths)
