from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from koddi.changes.service import _apply_files, _state
from koddi.policy.account import local_owner, owns
from koddi.policy.paths import WorkspacePaths
from koddi.storage.db import db


class WriteRecoveryError(ValueError):
    pass


def _hash(content: bytes | None) -> str | None:
    return None if content is None else hashlib.sha256(content).hexdigest()


@contextmanager
def _transaction():
    with db() as conn:
        # prepared фиксируется отдельно и с FULL до любых изменений workspace
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _paths(root: Path) -> WorkspacePaths:
    if not root.is_absolute():
        raise WriteRecoveryError("absolute workspace root required")
    paths = WorkspacePaths(root)
    if not paths.root.is_dir():
        raise WriteRecoveryError("workspace is unavailable")
    return paths


def _scope(conn, owner, project_id, turn_id, paths):
    if not isinstance(owner, str) or not owner or owner != local_owner():
        raise WriteRecoveryError("write operation is unavailable")
    if any(
        value is not None and (type(value) is not int or value <= 0)
        for value in (project_id, turn_id)
    ):
        raise WriteRecoveryError("invalid project or turn id")
    if project_id is not None:
        project = conn.execute("SELECT path FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None or _paths(Path(project["path"])).root != paths.root:
            raise WriteRecoveryError("project workspace does not match")
    if turn_id is not None:
        turn = conn.execute(
            "SELECT c.owner_id, c.project_id FROM turns t JOIN chats c ON c.id=t.chat_id WHERE t.id=?",
            (turn_id,),
        ).fetchone()
        if turn is None or not owns(turn["owner_id"], owner) or turn["project_id"] != project_id:
            raise WriteRecoveryError("turn is unavailable")


def _read(conn, operation_id, owner):
    row = conn.execute(
        "SELECT * FROM file_operations WHERE id=? AND owner_id=?", (operation_id, owner)
    ).fetchone()
    if row is None:
        raise WriteRecoveryError("write operation is unavailable")
    entries = conn.execute(
        "SELECT * FROM file_operation_entries WHERE operation_id=? ORDER BY path_key",
        (operation_id,),
    ).fetchall()
    if not entries:
        raise WriteRecoveryError("empty write journal")
    for entry in entries:
        for side in ("before", "after"):
            content = entry[side + "_content"]
            if (content is not None and not isinstance(content, bytes)) or _hash(content) != entry[
                side + "_hash"
            ]:
                raise WriteRecoveryError("write journal integrity check failed")
    return row, entries


def _context(conn, row):
    paths = _paths(Path(row["root"]))
    _scope(conn, row["owner_id"], row["project_id"], row["turn_id"], paths)
    info = paths.root.stat()
    if (str(info.st_dev), str(info.st_ino)) != (row["root_dev"], row["root_ino"]):
        raise WriteRecoveryError("workspace identity changed")
    return paths


def _current(paths, entries):
    result = {}
    for entry in entries:
        path = paths.resolve(entry["path"])
        if path == paths.root or os.path.normcase(str(path)) != entry["path_key"] or path in result:
            raise WriteRecoveryError("write journal path mismatch")
        result[path] = _state(path)
    for path in result:
        if any(parent in result for parent in path.parents):
            raise WriteRecoveryError("overlapping write paths")
    return result


def _insert(conn, *, owner_id, project_id, turn_id, paths, changes, recovery_of=None):
    canonical = {}
    for name, pair in changes.items():
        path = paths.resolve(str(name))
        if (
            path == paths.root
            or path in canonical
            or not isinstance(pair, (tuple, list))
            or len(pair) != 2
        ):
            raise WriteRecoveryError("invalid or duplicate write path")
        before, after = pair
        if any(value is not None and not isinstance(value, bytes) for value in pair):
            raise WriteRecoveryError("write journal requires bytes or None")
        if before != after:
            canonical[path] = (before, after)
    if not canonical:
        raise WriteRecoveryError("no file changes")
    for path in canonical:
        if any(parent in canonical for parent in path.parents):
            raise WriteRecoveryError("overlapping write paths")
    allowed = set()
    parent = recovery_of
    while parent is not None:
        if parent in allowed:
            raise WriteRecoveryError("cyclic recovery journal")
        allowed.add(parent)
        row = conn.execute(
            "SELECT owner_id,recovery_of FROM file_operations WHERE id=?", (parent,)
        ).fetchone()
        if row is None or row["owner_id"] != owner_id:
            raise WriteRecoveryError("recovery parent is unavailable")
        parent = row["recovery_of"]
    pending = conn.execute(
        "SELECT e.path_key, o.id FROM file_operation_entries e JOIN file_operations o ON o.id=e.operation_id "
        "WHERE o.status IN ('prepared','recovering')"
    ).fetchall()
    for item in pending:
        if item["id"] in allowed:
            continue
        other = Path(item["path_key"])
        if any(
            path == other or path in other.parents or other in path.parents for path in canonical
        ):
            raise WriteRecoveryError("pending write overlaps requested paths")
    initial = {path: _state(path) for path in canonical}
    if any(initial[path][0] != before for path, (before, _after) in canonical.items()):
        raise WriteRecoveryError("file changed before journal preparation")
    info = paths.root.stat()
    operation_id = uuid4().hex
    conn.execute(
        "INSERT INTO file_operations(id,owner_id,project_id,turn_id,root,root_dev,root_ino,recovery_of) VALUES (?,?,?,?,?,?,?,?)",
        (
            operation_id,
            owner_id,
            project_id,
            turn_id,
            str(paths.root),
            str(info.st_dev),
            str(info.st_ino),
            recovery_of,
        ),
    )
    for path, (before, after) in canonical.items():
        conn.execute(
            "INSERT INTO file_operation_entries(operation_id,path,path_key,before_hash,after_hash,before_content,after_content) VALUES (?,?,?,?,?,?,?)",
            (
                operation_id,
                path.relative_to(paths.root).as_posix(),
                os.path.normcase(str(path)),
                _hash(before),
                _hash(after),
                before,
                after,
            ),
        )
    if any(_state(paths.resolve(str(path))) != initial[path] for path in canonical):
        raise WriteRecoveryError("file changed during journal preparation")
    return operation_id


def prepare(
    *,
    owner_id: str,
    project_id: int | None,
    turn_id: int | None,
    root: Path,
    changes: Mapping[str | Path, tuple[bytes | None, bytes | None]],
) -> str:
    """сохраняет точные байты отдельным commit; файлы не меняет."""
    if not isinstance(changes, Mapping):
        raise WriteRecoveryError("changes must be a mapping")
    with _transaction() as conn:
        paths = _paths(Path(root))
        _scope(conn, owner_id, project_id, turn_id, paths)
        operation_id = _insert(
            conn,
            owner_id=owner_id,
            project_id=project_id,
            turn_id=turn_id,
            paths=paths,
            changes=changes,
        )
    return operation_id


def _mark_restored(conn, row):
    conn.execute(
        "UPDATE file_operations SET status='restored',completed_at=CURRENT_TIMESTAMP WHERE id=?",
        (row["id"],),
    )
    if row["recovery_of"] is not None:
        # отмена обратной операции снова оставляет исходную запись на ручной разбор
        conn.execute(
            "UPDATE file_operations SET status='prepared',completed_at=NULL WHERE id=? AND status='recovering'",
            (row["recovery_of"],),
        )


def finish(operation_id: str) -> dict:
    """вызывается после commit бизнес-записей, а не внутри их транзакции."""
    with _transaction() as conn:
        row, entries = _read(conn, operation_id, local_owner())
        if row["status"] == "finished":
            return {"id": operation_id, "status": "finished"}
        if row["status"] != "prepared":
            raise WriteRecoveryError("write operation is not prepared")
        paths = _context(conn, row)
        current = _current(paths, entries)
        if any(
            current[paths.resolve(entry["path"])][0] != entry["after_content"] for entry in entries
        ):
            raise WriteRecoveryError("files do not match prepared after content")
        conn.execute(
            "UPDATE file_operations SET status='finished',completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (operation_id,),
        )
        if row["recovery_of"] is not None:
            parent = conn.execute(
                "SELECT * FROM file_operations WHERE id=? AND owner_id=? AND status='recovering'",
                (row["recovery_of"], row["owner_id"]),
            ).fetchone()
            if parent is None:
                raise WriteRecoveryError("recovery parent is unavailable")
            parent, parent_entries = _read(conn, parent["id"], row["owner_id"])
            parent_paths = _context(conn, parent)
            parent_current = _current(parent_paths, parent_entries)
            if any(
                parent_current[parent_paths.resolve(entry["path"])][0] != entry["before_content"]
                for entry in parent_entries
            ):
                raise WriteRecoveryError("recovery parent still has conflicts")
            _mark_restored(conn, parent)
    return {"id": operation_id, "status": "finished"}


def inspect_pending(project_id: int | None) -> list[dict]:
    """только классификация; содержимое файлов в ответ не попадает."""
    owner = local_owner()
    result = []
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM file_operations WHERE owner_id=? AND project_id IS ? AND status IN ('prepared','recovering') ORDER BY created_at,id",
            (owner, project_id),
        ).fetchall()
        for row in rows:
            item = {
                key: row[key]
                for key in ("id", "project_id", "turn_id", "root", "status", "recovery_of")
            }
            files = []
            try:
                row, entries = _read(conn, row["id"], owner)
                paths = _context(conn, row)
                current = _current(paths, entries)
                for entry in entries:
                    content = current[paths.resolve(entry["path"])][0]
                    state = (
                        "unchanged"
                        if content == entry["before_content"]
                        else "applied"
                        if content == entry["after_content"]
                        else "conflict"
                    )
                    files.append(
                        {
                            "path": entry["path"],
                            "state": state,
                            "before_hash": entry["before_hash"],
                            "after_hash": entry["after_hash"],
                            "current_hash": _hash(content),
                        }
                    )
                states = {file["state"] for file in files}
                item["state"] = next(iter(states)) if len(states) == 1 else "conflict"
            except (OSError, ValueError):
                item["state"] = "conflict"
                item["error"] = "journal, workspace or access checks failed"
            item["files"] = files
            result.append(item)
    if owner != local_owner():
        raise WriteRecoveryError("active owner changed during journal inspection")
    return result


def restore(
    operation_id: str, *, expected_hashes: Mapping[str, str | None], allow_conflicts: bool = False
) -> dict:
    """явный возврат before; конфликт требует подтверждения и свежих hash всех файлов."""
    if not isinstance(expected_hashes, Mapping) or not isinstance(allow_conflicts, bool):
        raise WriteRecoveryError("explicit hashes and boolean confirmation required")
    with _transaction() as conn:
        row, entries = _read(conn, operation_id, local_owner())
        if row["status"] != "prepared":
            raise WriteRecoveryError("resolve pending recovery operation first")
        paths = _context(conn, row)
        current = _current(paths, entries)
        if set(expected_hashes) != {entry["path"] for entry in entries}:
            raise WriteRecoveryError("expected hashes must cover every journal path")
        changes = {}
        for entry in entries:
            path = paths.resolve(entry["path"])
            content = current[path][0]
            if _hash(content) != expected_hashes[entry["path"]]:
                raise WriteRecoveryError("file changed since explicit confirmation")
            if (
                content not in (entry["before_content"], entry["after_content"])
                and not allow_conflicts
            ):
                raise WriteRecoveryError("explicit conflict confirmation required")
            changes[path] = (content, entry["before_content"])
        if all(before == after for before, after in changes.values()):
            _mark_restored(conn, row)
            return {"id": operation_id, "status": "restored"}
        recovery_id = _insert(
            conn,
            owner_id=row["owner_id"],
            project_id=row["project_id"],
            turn_id=row["turn_id"],
            paths=paths,
            changes=changes,
            recovery_of=operation_id,
        )
        conn.execute("UPDATE file_operations SET status='recovering' WHERE id=?", (operation_id,))
    # обратный журнал уже durable; сбой ниже оставляет обе записи для inspect_pending
    targets = {
        path: after for path, (_before, after) in changes.items() if current[path][0] != after
    }
    expected = {path: current[path] for path in targets}
    with db() as conn:
        _context(conn, row)
        _current(_paths(paths.root), entries)
    with _apply_files(paths.root, targets, expected=expected):
        with db() as conn:
            checked = _context(conn, row)
            restored = _current(checked, entries)
            if any(
                restored[checked.resolve(entry["path"])][0] != entry["before_content"]
                for entry in entries
            ):
                raise WriteRecoveryError("files changed during explicit recovery")
        finish(recovery_id)
    return {"id": operation_id, "status": "restored", "recovery_id": recovery_id}
