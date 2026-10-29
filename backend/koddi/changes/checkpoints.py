"""Чекпоинт хода агента: состояние git до первой правки, предпросмотр и выборочный откат правок хода.

Откатываются только файлы, которые меняли инструменты этого хода, и только если файл сейчас ровно такой,
каким его оставил агент (хеш последней правки). Иначе - конфликт, файл не трогается: незакоммиченные
правки пользователя и неотслеживаемые файлы не теряются. Сам откат записывается как обычный набор
изменений, поэтому его можно отменить. git reset/clean/restore не используются.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from koddi.changes.service import _apply_files, _matches_after, _state, _target, _UnsafeRestore, _workspace, sha256_text, unified_diff
from koddi.storage.db import db

MAX_DIRTY = 300
MAX_PREVIEW_CHARS = 40000


def capture(root: Path) -> dict[str, Any]:
    """HEAD, ветка и изменённые/неотслеживаемые файлы пользователя до правок хода (без содержимого)."""
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> str | None:
        try:
            proc = subprocess.run(["git", "-c", "core.fsmonitor=false", *args], cwd=root, capture_output=True, timeout=15, check=False,
                                  creationflags=HIDDEN_SUBPROCESS, env=env)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None

    head = git("rev-parse", "HEAD")
    if head is None:
        return {"git": False}
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all") or ""
    dirty, untracked = [], []
    entries = status.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            index += 1
        (untracked if code == "??" else dirty).append(path)
    return {"git": True, "head": head.strip(), "branch": (git("rev-parse", "--abbrev-ref", "HEAD") or "").strip(),
            "dirty": dirty[:MAX_DIRTY], "untracked": untracked[:MAX_DIRTY],
            "truncated": len(dirty) > MAX_DIRTY or len(untracked) > MAX_DIRTY}


def ensure_captured(turn_id: int | None, root: Path) -> None:
    """Снимок один раз за ход, до первой записи; повторные правки хода его не меняют."""
    if turn_id is None:
        return
    from koddi.quality import gate

    if gate.load(turn_id).get("checkpoint"):
        return
    gate.save(turn_id, checkpoint=capture(root))


def _rows(turn_id: int) -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT f.path,f.before_hash,f.after_hash,f.before_text,f.after_text,c.id AS change_set_id FROM file_changes f "
            "JOIN change_sets c ON c.id=f.change_set_id WHERE c.turn_id=? AND c.status='applied' AND c.project_id IS NOT NULL "
            "ORDER BY f.id", (turn_id,)).fetchall()
    files: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = files.setdefault(row["path"], {"path": row["path"], "before_text": row["before_text"],
                                              "before_hash": row["before_hash"], "change_sets": []})
        item.update(after_text=row["after_text"], after_hash=row["after_hash"])
        if row["change_set_id"] not in item["change_sets"]:
            item["change_sets"].append(row["change_set_id"])
    return files


def _turn(turn_id: int, owner: str) -> dict[str, Any] | None:
    from koddi.policy.account import owns

    with db() as conn:
        row = conn.execute(
            "SELECT t.id,t.chat_id,c.owner_id,c.project_id,p.path AS root FROM turns t JOIN chats c ON c.id=t.chat_id "
            "LEFT JOIN projects p ON p.id=c.project_id WHERE t.id=?", (turn_id,)).fetchone()
    if row is None or not owns(row["owner_id"], owner) or not row["root"]:
        return None
    return dict(row)


def preview(turn_id: int, owner: str) -> dict[str, Any]:
    turn = _turn(turn_id, owner)
    if turn is None:
        raise LookupError("Ход не найден")
    from koddi.quality import gate

    root = _workspace(turn["root"])
    files = []
    budget = MAX_PREVIEW_CHARS
    for item in _rows(turn_id).values():
        path = _target(root, item["path"])
        try:
            current, _signature = _state(path)
            conflict = not _matches_after(current, item)
        except (_UnsafeRestore, OSError):
            current, conflict = None, True
        action = "delete" if item["before_text"] is None else ("create" if current is None else "restore")
        diff = unified_diff(item["path"], current.decode("utf-8", errors="replace") if current is not None else None,
                            item["before_text"])
        entry = {"path": item["path"], "action": action, "conflict": conflict, "change_sets": item["change_sets"]}
        if len(diff) <= budget:
            entry["diff"] = diff
            budget -= len(diff)
        else:
            entry["diff_truncated"] = True
        files.append(entry)
    return {"turn_id": turn_id, "checkpoint": gate.load(turn_id).get("checkpoint") or {}, "files": files,
            "conflicts": [item["path"] for item in files if item["conflict"]]}


def restore(turn_id: int, owner: str, paths: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
    """Возвращает выбранные файлы хода к состоянию до хода; при конфликте ничего не пишет."""
    turn = _turn(turn_id, owner)
    if turn is None:
        raise LookupError("Ход не найден")
    root = _workspace(turn["root"])
    available = _rows(turn_id)
    selected = list(available) if not paths else [path for path in dict.fromkeys(paths) if path in available]
    unknown = sorted(set(paths or []) - set(available))
    if unknown:
        return {"restored": False, "reason": "files were not changed by this turn", "unknown": unknown}
    if not selected:
        return {"restored": False, "reason": "nothing to restore"}
    files: dict[Path, bytes | None] = {}
    expected, conflicts, current_text = {}, [], {}
    try:
        for relative in selected:
            item = available[relative]
            path = _target(root, relative)
            current, signature = _state(path)
            expected[path] = (current, signature)
            if not _matches_after(current, item):
                conflicts.append(relative)
            files[path] = item["before_text"].encode("utf-8") if item["before_text"] is not None else None
            current_text[relative] = current
        if conflicts and not force:
            return {"restored": False, "reason": "file conflicts", "conflicts": conflicts}
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            with _apply_files(root, files, expected=expected):
                diff = "".join(unified_diff(relative, (current_text[relative] or b"").decode("utf-8", errors="replace")
                                            if current_text[relative] is not None else None, available[relative]["before_text"])
                               for relative in selected)
                change_set_id = int(conn.execute(
                    "INSERT INTO change_sets(project_id, turn_id, description, diff, owner_id) VALUES (?, NULL, ?, ?, ?)",
                    (turn["project_id"], f"Откат хода #{turn_id}", diff, owner)).lastrowid)
                for relative in selected:
                    before = current_text[relative]
                    before_text = before.decode("utf-8", errors="replace") if before is not None else None
                    after_text = available[relative]["before_text"]
                    conn.execute(
                        "INSERT INTO file_changes(change_set_id, path, before_hash, after_hash, before_text, after_text) VALUES (?,?,?,?,?,?)",
                        (change_set_id, relative, sha256_text(before_text), sha256_text(after_text), before_text, after_text))
                    conn.execute("INSERT INTO snapshots(change_set_id, path, content, existed) VALUES (?,?,?,?)",
                                 (change_set_id, str(_target(root, relative)), before, int(before is not None)))
                if set(selected) == set(available):
                    ids = sorted({cid for item in available.values() for cid in item["change_sets"]})
                    conn.executemany("UPDATE change_sets SET status='rolled_back', rolled_back_at=CURRENT_TIMESTAMP WHERE id=?",
                                     [(cid,) for cid in ids])
                conn.commit()
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {"restored": False, "reason": "unsafe or unavailable restore",
                "error": str(exc) if isinstance(exc, _UnsafeRestore) else "filesystem operation failed"}
    return {"restored": True, "turn_id": turn_id, "paths": selected, "change_set_id": change_set_id, "forced": bool(conflicts)}
