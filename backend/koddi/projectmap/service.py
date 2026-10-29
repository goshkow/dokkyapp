"""Карта проекта: инкрементальное сканирование по mtime и SHA-256, хранение в SQLite папки данных,
отслеживание изменений файлов и заметки агента.

Сканирование всегда идёт в одном фоновом потоке; вызывающий ждёт его ограниченное время и получает
то, что уже есть. Автоматический запуск (открытие проекта, начало хода, отслеживание файлов) включается
только в работающем приложении (`enable_background`), чтобы тесты и консольные утилиты не оставляли потоков.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import stat
import subprocess
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from koddi.logs import log_event
from koddi.logs.service import redact
from koddi.policy.paths import PathAccessError, WorkspacePaths
from koddi.projectmap import chunks
from koddi.projectmap.graph import NOTE_KINDS, Graph
from koddi.projectmap.parse import PARSER_VERSION, language_of, parse_file
from koddi.projectmap import views
import koddi.storage.db as storage
from koddi.storage.db import db

MAX_FILES = 20000
MAX_HASH_BYTES = 16 * 1024 * 1024
BATCH = 400
# сгенерированное и зависимости: их .gitignore обычно и так прячет, но не во всех проектах
SKIP_DIRS = {
    "node_modules", "bower_components", "vendor", "dist", "build", "out", "target", "coverage", "htmlcov",
    "__pycache__", "venv", "env", "Pods", "DerivedData", "bin", "obj", "site-packages",
}
VISIBLE_DOT_DIRS = {".github", ".gitlab"}
FRESH_SECONDS = 30.0
DEBOUNCE_SECONDS = 1.5
POLL_SECONDS = 30.0
MAX_WATCHERS = 3
MAX_NOTES = 400
MAX_NOTE_BODY = 4000
MAX_NOTE_TITLE = 120
MAX_TARGETS = 20

_lock = threading.RLock()
# ключи кэшей включают файл базы: другая база (другой профиль, тесты) не получит чужой граф с тем же id
_project_locks: dict[tuple[str, int], threading.Lock] = {}
_graphs: dict[tuple[str, int, str], Graph] = {}
_futures: dict[tuple[str, int], Future] = {}
_dirty: set[tuple[str, int]] = set()
_scanned_at: dict[tuple[str, int], float] = {}
_watchers: "OrderedDict[int, _Watch]" = OrderedDict()
_executor: ThreadPoolExecutor | None = None
_stop = threading.Event()
_background = False
_notifier: Callable[[str, dict[str, Any]], None] | None = None


class MapError(RuntimeError):
    pass


def _k(project_id: int) -> tuple[str, int]:
    return (str(storage.DB_PATH), project_id)


# ---------- пути

def relative_key(root: str, path: str, *, pathmod=os.path) -> str | None:
    """Путь события относительно корня проекта в виде a/b/c.py или None, если он вне проекта.

    На Windows регистр и разделители не важны, на macOS имена из FSEvents бывают в другой форме Unicode.
    """
    root_norm = unicodedata.normalize("NFC", pathmod.normcase(pathmod.normpath(root)))
    path_norm = unicodedata.normalize("NFC", pathmod.normcase(pathmod.normpath(path)))
    if path_norm == root_norm:
        return ""
    prefix = root_norm.rstrip(pathmod.sep) + pathmod.sep
    if not path_norm.startswith(prefix):
        return None
    tail = pathmod.normpath(path)[len(pathmod.normpath(root).rstrip(pathmod.sep)) + 1:]
    parts = [part for part in re.split(r"[\\/]" if pathmod is ntpath else "/", tail) if part]
    if any(part in {".", ".."} for part in parts):
        return None
    return posixpath.join(*parts) if parts else ""


def skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.lower() in {".git", ".venv"} or (name.startswith(".") and name not in VISIBLE_DOT_DIRS)


def relevant(relative: str) -> bool:
    """Касается ли изменение карты: служебные папки и неподдерживаемые файлы её не трогают."""
    parts = relative.split("/")
    if any(skipped_dir(part) for part in parts[:-1]):
        return False
    return not parts[-1] or language_of(relative) is not None or "." not in parts[-1]


def walk(root: Path):
    """Файлы карты: границы доступа и .gitignore те же, что у поиска агента, плюс пропуск зависимостей и сборок."""
    paths = WorkspacePaths(root)
    for current, dirs, files in os.walk(root, followlinks=False):
        if _stop.is_set():
            return
        base = Path(current)
        dirs[:] = sorted(
            name for name in dirs
            if not skipped_dir(name) and not paths.is_noise(name) and paths.accessible(base / name, ignore=True)
        )
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if language_of(relative) is None or not paths.accessible(path, ignore=True):
                continue
            yield relative, path


# ---------- состояние и хранение

def _project_lock(project_id: int) -> threading.Lock:
    with _lock:
        return _project_locks.setdefault(_k(project_id), threading.Lock())


def state(project_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM project_map_state WHERE project_id=?", (project_id,)).fetchone()
    value = dict(row) if row else {"project_id": project_id, "version": 0, "status": "missing", "files": 0, "truncated": 0, "built_at": None}
    with _lock:
        future = _futures.get(_k(project_id))
        if future and not future.done():
            value["status"] = "building" if not value.get("built_at") else "updating"
    value["truncated"] = bool(value.get("truncated"))
    return value


def _set_state(conn, project_id: int, **fields) -> None:
    conn.execute("INSERT OR IGNORE INTO project_map_state(project_id) VALUES (?)", (project_id,))
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn.execute(f"UPDATE project_map_state SET {assignments} WHERE project_id=?", (*fields.values(), project_id))


def _notify(project_id: int, version: int, status: str) -> None:
    if _notifier is not None:
        try:
            _notifier("project_map.updated", {"project_id": project_id, "version": version, "status": status})
        except Exception:
            pass


def _project_exists(project_id: int) -> bool:
    with db() as conn:
        return conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None


def scan(project_id: int, root: Path) -> dict:
    """Один проход: новые и изменённые файлы перечитываются, удалённые убираются. Возвращает счётчики."""
    with _project_lock(project_id):
        root = Path(os.path.abspath(root))
        stats = {"added": 0, "changed": 0, "removed": 0, "touched": 0, "unchanged": 0, "truncated": False, "errors": 0}
        if not _project_exists(project_id):
            raise MapError("project not found")
        if not root.is_dir():
            with db() as conn:
                _set_state(conn, project_id, status="error", error="project folder is not available")
            raise MapError("project folder is not available")
        with db() as conn:
            current = conn.execute("SELECT parser_version, version FROM project_map_state WHERE project_id=?", (project_id,)).fetchone()
            rows = {
                row["path"]: (row["size"], row["mtime_ns"], row["sha256"])
                for row in conn.execute("SELECT path,size,mtime_ns,sha256 FROM project_map_files WHERE project_id=?", (project_id,))
            }
            _set_state(conn, project_id, status="building" if current is None else "updating", error=None)
        reparse = current is None or current["parser_version"] != PARSER_VERSION
        seen: set[str] = set()
        upserts: list[tuple] = []
        touches: list[tuple] = []
        indexed: list[tuple] = []
        changed = False

        def flush(final: bool = False) -> None:
            nonlocal upserts, touches, indexed
            if not upserts and not touches and not final:
                return
            with db() as conn:
                for relative, digest, text, ranges in indexed:
                    chunks.index_file(conn, project_id, relative, digest, text, ranges)
                conn.executemany(
                    "INSERT INTO project_map_files(project_id,path,size,mtime_ns,sha256,lang,info_json,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(project_id,path) DO UPDATE SET "
                    "size=excluded.size,mtime_ns=excluded.mtime_ns,sha256=excluded.sha256,lang=excluded.lang,"
                    "info_json=excluded.info_json,updated_at=CURRENT_TIMESTAMP",
                    upserts,
                )
                conn.executemany("UPDATE project_map_files SET size=?,mtime_ns=? WHERE project_id=? AND path=?", touches)
            upserts, touches, indexed = [], [], []

        try:
            for relative, path in walk(root):
                if _stop.is_set():
                    raise MapError("stopped")
                if len(seen) >= MAX_FILES:
                    stats["truncated"] = True
                    break
                try:
                    info_stat = path.stat()
                except OSError:
                    if relative in rows:
                        seen.add(relative)
                    continue
                if not stat.S_ISREG(info_stat.st_mode):
                    continue
                seen.add(relative)
                old = rows.get(relative)
                if old and not reparse and old[0] == info_stat.st_size and old[1] == info_stat.st_mtime_ns:
                    stats["unchanged"] += 1
                    continue
                try:
                    if info_stat.st_size > MAX_HASH_BYTES:
                        digest = f"size:{info_stat.st_size}:{info_stat.st_mtime_ns}"
                        info = {"lang": language_of(relative) or "text", "skipped": "large"}
                    else:
                        data = path.read_bytes()
                        digest = hashlib.sha256(data).hexdigest()
                        if old and not reparse and old[2] == digest:
                            touches.append((info_stat.st_size, info_stat.st_mtime_ns, project_id, relative))
                            stats["touched"] += 1
                            continue
                        info = parse_file(relative, data)
                except OSError:
                    # файл занят другим процессом (Windows) или пропал во время чтения: прежняя запись остаётся
                    stats["errors"] += 1
                    continue
                upserts.append((project_id, relative, info_stat.st_size, info_stat.st_mtime_ns, digest, info["lang"], json.dumps(info, ensure_ascii=False)))
                # фрагменты для context.retrieve: термы в FTS5, сам текст читается из файла при выдаче
                indexed.append((relative, digest, data.decode("utf-8", errors="replace") if info.get("chunks") else "", info.get("chunks") or []))
                stats["changed" if old else "added"] += 1
                changed = True
                if len(upserts) + len(touches) >= BATCH:
                    flush()
            flush()
            removed = [path for path in rows if path not in seen]
            if stats["truncated"]:
                # за пределом лимита файлы не просматривались: удаляются только реально пропавшие
                removed = [path for path in removed if not (root / path).is_file()]
            stats["removed"] = len(removed)
            changed = changed or bool(removed)
            with db() as conn:
                conn.executemany("DELETE FROM project_map_files WHERE project_id=? AND path=?", [(project_id, path) for path in removed])
                chunks.remove(conn, project_id, removed)
                count = conn.execute("SELECT COUNT(*) FROM project_map_files WHERE project_id=?", (project_id,)).fetchone()[0]
                fields = {
                    "status": "ready", "error": None, "files": count, "truncated": int(stats["truncated"]),
                    "parser_version": PARSER_VERSION, "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                }
                if changed or current is None:
                    fields["version"] = (current["version"] if current else 0) + 1
                    fields["built_at"] = fields["scanned_at"]
                _set_state(conn, project_id, **fields)
                version = conn.execute("SELECT version FROM project_map_state WHERE project_id=?", (project_id,)).fetchone()[0]
        except Exception as exc:
            with db() as conn:
                _set_state(conn, project_id, status="error", error=str(exc)[:300])
            if isinstance(exc, (PathAccessError, MapError, OSError)):
                raise MapError(str(exc)) from exc
            raise
        with _lock:
            _scanned_at[_k(project_id)] = time.monotonic()
        if changed or current is None:
            _notify(project_id, version, "ready")
            log_event("INFO", "project_map.scan", "project map updated", {"project_id": project_id, **stats})
        return {**stats, "version": version}


def _executor_instance() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="project-map")
        return _executor


def _submit(project_id: int, root: Path) -> Future:
    with _lock:
        future = _futures.get(_k(project_id))
        if future and not future.done():
            return future
        _dirty.discard(_k(project_id))

        def job():
            try:
                return scan(project_id, root)
            except MapError as exc:
                log_event("WARNING", "project_map.scan_failed", str(exc), {"project_id": project_id})
                raise
            except Exception as exc:
                log_event("ERROR", "project_map.scan_failed", str(exc), {"project_id": project_id})
                raise

        future = _executor_instance().submit(job)
        _futures[_k(project_id)] = future
        return future


def ensure(project_id: int, root: Path, *, wait: float = 20.0, cancel: threading.Event | None = None) -> dict:
    """Свежая карта перед ответом: пересканирует, если были изменения или давно не проверялась.

    Если первая сборка большого проекта не успела за `wait` секунд, возвращается текущее состояние со статусом building.
    """
    with _lock:
        last = _scanned_at.get(_k(project_id))
        # опрос раз в 30 с не ловит свежие правки, поэтому полагаемся только на события
        watched = project_id in _watchers and _watchers[project_id].mode == "events"
        dirty = _k(project_id) in _dirty
    fresh = last is not None and not dirty and (watched or time.monotonic() - last < FRESH_SECONDS)
    current = state(project_id)
    if not fresh or current["status"] in {"missing", "error"}:
        future = _submit(project_id, root)
        deadline = time.monotonic() + wait
        while not future.done() and time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise MapError("operation cancelled")
            try:
                future.result(timeout=0.1)
            except TimeoutError:
                continue
            except Exception:
                break
        if future.done() and future.exception() is not None and state(project_id).get("built_at") is None:
            raise MapError(str(future.exception()))
    if _background:
        watch(project_id, root)
    return state(project_id)


def schedule(project_id: int | None, root: Path | None) -> None:
    """Фоновая сборка или обновление (открытие проекта, начало хода агента) без ожидания результата."""
    if not _background or project_id is None or root is None or not Path(root).is_dir():
        return
    with _lock:
        watcher = _watchers.get(project_id)
        current = watcher is not None and watcher.mode == "events" and _k(project_id) in _scanned_at and _k(project_id) not in _dirty
    if not current:
        _submit(project_id, Path(root))
    watch(project_id, Path(root))


def _load_notes(project_id: int, owner: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id,kind,title,body,targets_json,updated_at,source,confidence,status,basis,fingerprint "
            "FROM project_map_notes WHERE project_id=? AND owner_id=? ORDER BY id",
            (project_id, owner),
        ).fetchall()
    notes = []
    for row in rows:
        item = dict(row)
        try:
            item["targets"] = json.loads(item.pop("targets_json") or "[]")
        except ValueError:
            item["targets"] = []
        notes.append(item)
    return notes


def graph(project_id: int, owner: str) -> Graph:
    version = state(project_id)["version"]
    key = (*_k(project_id), owner)
    with _lock:
        cached = _graphs.get(key)
        if cached is not None and cached.version == version:
            return cached
    with db() as conn:
        rows = conn.execute("SELECT path,size,sha256,lang,info_json FROM project_map_files WHERE project_id=?", (project_id,)).fetchall()
    files = {}
    for row in rows:
        try:
            info = json.loads(row["info_json"] or "{}")
        except ValueError:
            info = {}
        files[row["path"]] = {"size": row["size"], "sha256": row["sha256"], "lang": row["lang"], "info": info}
    value = Graph(files, _load_notes(project_id, owner), version=version)
    with _lock:
        # графы других аккаунтов и старых версий не держатся в памяти
        for stale in [item for item in _graphs if item[:2] == key[:2]]:
            _graphs.pop(stale, None)
        _graphs[key] = value
        while len(_graphs) > 6:
            _graphs.pop(next(iter(_graphs)))
    return value


def _bump(project_id: int) -> int:
    with db() as conn:
        _set_state(conn, project_id, version=(conn.execute("SELECT version FROM project_map_state WHERE project_id=?", (project_id,)).fetchone() or [0])[0] + 1)
        return conn.execute("SELECT version FROM project_map_state WHERE project_id=?", (project_id,)).fetchone()[0]


def mark_dirty(project_id: int | None = None, workspace: Path | None = None) -> None:
    """Агент или редактор что-то изменил: следующий запрос карты пересканирует проект, не дожидаясь событий."""
    if project_id is None and workspace is not None:
        project_id = project_for_workspace(workspace)
    if project_id is not None:
        with _lock:
            _dirty.add(_k(project_id))


def forget(project_id: int) -> None:
    """Проект убран из списка: его наблюдатель и кэш больше не нужны (строки в базе удаляет каскад)."""
    with _lock:
        watcher = _watchers.pop(project_id, None)
        for key in [item for item in _graphs if item[:2] == _k(project_id)]:
            _graphs.pop(key, None)
        _scanned_at.pop(_k(project_id), None)
        _dirty.discard(_k(project_id))
    if watcher:
        watcher.stop()

    with db() as conn:
        conn.execute("DELETE FROM context_chunks WHERE project_id=?", (project_id,))
        chunks.prune(conn)


# ---------- заметки агента

def _fingerprint(root: Path | None) -> str:
    """Коммит HEAD рабочей копии: по нему видно, к какой версии кода относится знание."""
    if root is None:
        return ""
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    try:
        proc = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=root, capture_output=True, text=True,
                              timeout=5, check=False, creationflags=HIDDEN_SUBPROCESS)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def save_note(
    project_id: int, owner: str, *, kind: str, title: str, body: str = "", targets: list[str] | None = None,
    note: int | None = None, chat_id: int | None = None, source: str = "agent", confidence: str = "medium",
    basis: str = "", root: Path | None = None,
) -> dict:
    """Создаёт или меняет заметку. Агент не перезаписывает заметку, которую правил человек (source=user)."""
    if source not in {"agent", "user"}:
        raise MapError("source must be agent or user")
    if confidence not in {"low", "medium", "high"}:
        raise MapError("confidence must be low, medium or high")
    basis = redact(re.sub(r"\s+", " ", basis or "").strip())[:300]
    # гипотеза агента ждёт подтверждения человека, факт с основанием считается действующим
    status = "needs-review" if source == "agent" and confidence == "low" else "valid"
    kind = (kind or "topic").strip().lower()
    if kind not in NOTE_KINDS:
        raise MapError(f"kind must be one of: {', '.join(NOTE_KINDS)}")
    title = redact(re.sub(r"\s+", " ", title or "").strip())
    if not title:
        raise MapError("title is required")
    if len(title) > MAX_NOTE_TITLE:
        raise MapError(f"title is longer than {MAX_NOTE_TITLE} characters")
    body = redact((body or "").strip())
    if len(body) > MAX_NOTE_BODY:
        raise MapError(f"body is longer than {MAX_NOTE_BODY} characters; keep notes short and link files instead")
    refs = [str(item).strip() for item in (targets or []) if str(item).strip()]
    if len(refs) > MAX_TARGETS:
        raise MapError(f"at most {MAX_TARGETS} targets")
    current = graph(project_id, owner)
    resolved, missing = [], []
    for ref in refs:
        node = current.resolve_ref(ref)
        if node is None:
            missing.append(ref)
            continue
        target = {"ref": node}
        if node.startswith("file:"):
            target["sha256"] = current.files[node[5:]]["sha256"]
        resolved.append(target)
    if missing:
        raise MapError("targets not found in the map: " + ", ".join(missing[:10]) + ". Use workspace-relative paths, folder paths, 'METHOD /route' or note titles.")
    fingerprint = _fingerprint(root)
    with db() as conn:
        protected = conn.execute(
            "SELECT id FROM project_map_notes WHERE project_id=? AND owner_id=? AND source='user' AND (id=? OR title=?)",
            (project_id, owner, note or -1, title),
        ).fetchone()
        if protected is not None and source == "agent":
            raise MapError(f"note #{protected['id']} was edited by the user and is not overwritten; save your proposal "
                           "as a new note with confidence low or tell the user what to change")
        meta = (source, confidence, status, basis, fingerprint)
        if note is not None:
            row = conn.execute("SELECT id FROM project_map_notes WHERE id=? AND project_id=? AND owner_id=?", (note, project_id, owner)).fetchone()
            if row is None:
                raise MapError("note not found")
            clash = conn.execute("SELECT id FROM project_map_notes WHERE project_id=? AND owner_id=? AND title=? AND id<>?", (project_id, owner, title, note)).fetchone()
            if clash:
                raise MapError(f"another note #{clash['id']} already has this title")
            conn.execute(
                "UPDATE project_map_notes SET kind=?,title=?,body=?,targets_json=?,source=?,confidence=?,status=?,basis=?,"
                "fingerprint=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (kind, title, body, json.dumps(resolved, ensure_ascii=False), *meta, note),
            )
            note_id_value = note
            action = "updated"
        else:
            existing = conn.execute("SELECT id FROM project_map_notes WHERE project_id=? AND owner_id=? AND title=?", (project_id, owner, title)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE project_map_notes SET kind=?,body=?,targets_json=?,source=?,confidence=?,status=?,basis=?,"
                    "fingerprint=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (kind, body, json.dumps(resolved, ensure_ascii=False), *meta, existing["id"]),
                )
                note_id_value = existing["id"]
                action = "updated"
            else:
                count = conn.execute("SELECT COUNT(*) FROM project_map_notes WHERE project_id=? AND owner_id=?", (project_id, owner)).fetchone()[0]
                if count >= MAX_NOTES:
                    raise MapError(f"the map already has {MAX_NOTES} notes; update or delete old ones")
                note_id_value = conn.execute(
                    "INSERT INTO project_map_notes(project_id,owner_id,kind,title,body,targets_json,chat_id,source,confidence,"
                    "status,basis,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, owner, kind, title, body, json.dumps(resolved, ensure_ascii=False), chat_id, *meta),
                ).lastrowid
                action = "created"
    version = _bump(project_id)
    _notify(project_id, version, "ready")
    return {"id": note_id_value, "action": action, "targets": [item["ref"] for item in resolved], "version": version,
            "status": status}


def delete_note(project_id: int, owner: str, note: int, *, source: str = "agent") -> dict:
    with db() as conn:
        row = conn.execute("SELECT source FROM project_map_notes WHERE id=? AND project_id=? AND owner_id=?", (note, project_id, owner)).fetchone()
        if row is not None and row["source"] == "user" and source != "user":
            raise MapError(f"note #{note} was edited by the user; ask the user before deleting it")
        deleted = conn.execute("DELETE FROM project_map_notes WHERE id=? AND project_id=? AND owner_id=?", (note, project_id, owner)).rowcount
        conn.execute("DELETE FROM knowledge_exports WHERE project_id=? AND owner_id=? AND note_id=?", (project_id, owner, note))
    if not deleted:
        raise MapError("note not found")
    version = _bump(project_id)
    _notify(project_id, version, "ready")
    return {"id": note, "action": "deleted", "version": version}


# ---------- отслеживание файлов

class _Watch:
    """Наблюдатель за папкой проекта: события помечают карту устаревшей и через паузу запускают пересканирование.

    watchdog (FSEvents на macOS, ReadDirectoryChangesW на Windows); если он недоступен - опрос раз в 30 с.
    """

    def __init__(self, project_id: int, root: Path):
        self.project_id = project_id
        self.root = root
        self.timer: threading.Timer | None = None
        self.observer = None
        self.poller: threading.Thread | None = None
        self.stopped = threading.Event()
        self.mode = "poll"

    def start(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            owner = self

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    if event.event_type in {"opened", "closed_no_write"}:
                        return
                    paths = [event.src_path, getattr(event, "dest_path", "")]
                    owner.changed([os.fsdecode(path) for path in paths if path])

            observer = Observer()
            observer.schedule(Handler(), str(self.root), recursive=True)
            observer.daemon = True
            observer.start()
            self.observer = observer
            self.mode = "events"
        except Exception as exc:
            log_event("WARNING", "project_map.watch_fallback", str(exc)[:300], {"project_id": self.project_id})
            self.poller = threading.Thread(target=self._poll, name="project-map-poll", daemon=True)
            self.poller.start()

    def changed(self, paths: list[str]) -> None:
        if self.stopped.is_set():
            return
        if not any((key := relative_key(str(self.root), path)) is not None and relevant(key) for path in paths):
            return
        with _lock:
            _dirty.add(_k(self.project_id))
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(DEBOUNCE_SECONDS, self._fire)
            self.timer.daemon = True
            self.timer.start()

    def _fire(self) -> None:
        if not self.stopped.is_set() and _background:
            _submit(self.project_id, self.root)

    def _poll(self) -> None:
        while not self.stopped.wait(POLL_SECONDS):
            if _background:
                with _lock:
                    _dirty.add(_k(self.project_id))
                _submit(self.project_id, self.root)

    def stop(self) -> None:
        self.stopped.set()
        with _lock:
            if self.timer:
                self.timer.cancel()
        if self.observer is not None:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass


def watch(project_id: int, root: Path) -> None:
    root = Path(os.path.abspath(root))
    with _lock:
        if not _background:
            return
        current = _watchers.get(project_id)
        if current is not None and current.root == root:
            _watchers.move_to_end(project_id)
            return
        evicted = []
        if current is not None:
            evicted.append(_watchers.pop(project_id))
        watcher = _Watch(project_id, root)
        _watchers[project_id] = watcher
        while len(_watchers) > MAX_WATCHERS:
            evicted.append(_watchers.popitem(last=False)[1])
    for item in evicted:
        item.stop()
    watcher.start()


def enable_background(notifier: Callable[[str, dict[str, Any]], None] | None) -> None:
    """Приложение запущено: открытие проекта и начало хода строят карту сами, папки отслеживаются."""
    global _background, _notifier
    _stop.clear()
    _notifier = notifier
    _background = True


def shutdown() -> None:
    global _background, _notifier, _executor
    with _lock:
        _background = False
        _notifier = None
        watchers = list(_watchers.values())
        _watchers.clear()
        executor, _executor = _executor, None
        _futures.clear()
    _stop.set()
    for watcher in watchers:
        watcher.stop()
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)
    _stop.clear()


def watched_mode(project_id: int) -> str | None:
    with _lock:
        watcher = _watchers.get(project_id)
        return watcher.mode if watcher else None


# ---------- вход для инструментов агента

def project_for_workspace(workspace: Path) -> int | None:
    with db() as conn:
        row = conn.execute("SELECT id FROM projects WHERE path=?", (str(Path(workspace).resolve()),)).fetchone()
    return int(row["id"]) if row else None


def _bounded(args: dict) -> int:
    value = args.get("max_chars") or views.DEFAULT_CHARS
    return max(1000, min(int(value), views.MAX_CHARS))


def map_tool(project_id: int | None, workspace: Path, args: dict, *, owner: str, cancel: threading.Event | None = None) -> dict:
    project_id = project_id or project_for_workspace(workspace)
    if project_id is None:
        raise MapError("the project map needs a project opened in Dokky")
    current = ensure(project_id, workspace, wait=25.0, cancel=cancel)
    value = graph(project_id, owner)
    budget = _bounded(args)
    result: dict[str, Any] = {"ok": True, "version": value.version, "status": current["status"]}
    if current["status"] == "building":
        result["note"] = "The first build is still running; the map is partial. Call project.map again shortly."
    sections = []
    if args.get("path"):
        text, node = views.describe(value, str(args["path"]), max_chars=budget)
        sections.append(text)
        if node:
            result["node"] = node
    if args.get("api") is not None and str(args.get("api")).strip():
        sections.append(views.api_list(value, str(args["api"]), max_chars=budget))
    if args.get("query"):
        kinds = set(args.get("kinds") or []) or None
        sections.append(views.search(value, str(args["query"]), limit=int(args.get("limit") or 25), max_chars=budget, kinds=kinds))
    if not sections:
        sections.append(views.overview(value, current, max_chars=budget))
    result["map"] = "\n\n".join(sections)[: budget + 200]
    return result


def map_update_tool(project_id: int | None, workspace: Path, args: dict, *, owner: str, chat_id: int | None = None) -> dict:
    project_id = project_id or project_for_workspace(workspace)
    if project_id is None:
        raise MapError("the project map needs a project opened in Dokky")
    action = args.get("action", "upsert")
    if action == "delete":
        if not args.get("note_id"):
            raise MapError("note_id is required to delete a note")
        return {"ok": True, **delete_note(project_id, owner, int(args["note_id"]))}
    if action == "rescan":
        stats = scan(project_id, workspace)
        return {"ok": True, "action": "rescanned", **stats}
    ensure(project_id, workspace, wait=10.0)
    result = save_note(
        project_id, owner, kind=str(args.get("kind") or "topic"), title=str(args.get("title") or ""),
        body=str(args.get("body") or ""), targets=list(args.get("targets") or []),
        note=int(args["note_id"]) if args.get("note_id") else None, chat_id=chat_id,
        confidence=str(args.get("confidence") or "medium"), basis=str(args.get("basis") or ""), root=workspace,
    )
    from koddi.knowledge.service import sync_after_change

    sync_after_change(project_id, owner, workspace)
    return {"ok": True, **result}


def context_snapshot(project_id: int | None, workspace: Path | None, owner: str) -> dict | None:
    """Обзор карты для снимка контекста агента; None, пока карта не построена (тогда её строит фон)."""
    if project_id is None or workspace is None:
        return None
    current = state(project_id)
    schedule(project_id, workspace)
    if not current.get("built_at"):
        return None
    return views.runtime_snapshot(graph(project_id, owner))


def ui_tree(project_id: int, root: Path, owner: str) -> dict:
    current = ensure(project_id, root, wait=3.0)
    payload = views.treemap_payload(graph(project_id, owner))
    payload["state"] = {key: current.get(key) for key in ("status", "files", "truncated", "built_at", "error")}
    payload["watch"] = watched_mode(project_id)
    return payload
