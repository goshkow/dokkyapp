from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from koddi.config import data_dir
from koddi.storage.db import db


def sha256_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_text_if_exists(path: Path) -> tuple[bool, str | None]:
    if not path.exists():
        return False, None
    return True, path.read_text(encoding="utf-8", errors="replace")


class _UnsafeRestore(ValueError):
    pass


def _path(value: str | Path) -> Path:
    value = str(value).replace("\\", "/")
    if not value or "\x00" in value:
        raise _UnsafeRestore("invalid path")
    windows = PureWindowsPath(value)
    if windows.drive and (os.name != "nt" or not windows.is_absolute()):
        raise _UnsafeRestore("unsupported absolute path")
    # алиасы Windows и alternate data streams не должны обходить проверку коллизий
    parts = value.split("/")
    for part in parts:
        if part in {"", ".", "..", windows.drive}:
            continue
        if ":" in part or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved():
            raise _UnsafeRestore("unsafe path component")
    return Path(os.path.normpath(value))


def _absolute(value: str | Path) -> Path:
    path = _path(value)
    if not path.is_absolute():
        raise _UnsafeRestore("absolute path required")
    return path


def _no_links(path: Path) -> None:
    # lstat видит также dangling symlink и junction; resolve скрыл бы подмену пути
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise _UnsafeRestore("symlink or reparse point is not allowed")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise _UnsafeRestore("parent is not a directory")


def _workspace(value: str | Path) -> Path:
    root = _absolute(value)
    _no_links(root)
    if not root.is_dir():
        raise _UnsafeRestore("workspace is unavailable")
    return root


def _target(root: Path, value: str | Path) -> Path:
    path = _path(value)
    target = path if path.is_absolute() else root / path
    target = Path(os.path.normpath(target))
    if target == root or not target.is_relative_to(root):
        raise _UnsafeRestore("path is outside workspace")
    return target


def _state(path: Path) -> tuple[bytes | None, tuple | None]:
    _no_links(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(info.st_mode):
        raise _UnsafeRestore("target is not a regular file")
    data = path.read_bytes()
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
    _no_links(path)
    after = path.lstat()
    if (
        signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)
        or len(data) != info.st_size
    ):
        raise _UnsafeRestore("file changed while reading")
    return data, signature


def _validate_layout(root: Path, files: dict[Path, bytes | None], directories: set[Path]) -> None:
    _workspace(root)
    for path in directories:
        _no_links(path)
        if path.exists() and not path.is_dir():
            raise _UnsafeRestore("directory conflicts with a file")
    for path in files:
        _no_links(path)
        if path.exists() and not path.is_file():
            raise _UnsafeRestore("target is not a regular file")
        if path in directories:
            raise _UnsafeRestore("overlapping restore targets")
    for path in set(files) | directories:
        if any(parent in files for parent in path.parents):
            raise _UnsafeRestore("overlapping restore targets")


def _stage(path: Path, content: bytes, mode: int | None) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".koddi-restore-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            if mode is not None:
                os.chmod(temporary, stat.S_IMODE(mode))
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        _discard_stage(temporary)
        raise


def _discard_stage(path: Path) -> None:
    try:
        _no_links(path)
        if path.exists():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            path.unlink()
    except (OSError, ValueError):
        pass


@contextmanager
def _apply_files(
    root: Path, files: dict[Path, bytes | None], directories: set[Path] | None = None, expected=None
):
    directories = directories or set()
    _validate_layout(root, files, directories)
    original = {path: _state(path) for path in files}
    if expected is not None and original != expected:
        raise _UnsafeRestore("file changed during restore preparation")
    staged: dict[Path, Path] = {}
    undo: dict[Path, Path] = {}
    created: list[Path] = []
    applied: list[Path] = []
    completed = False
    recovery_failed = False
    try:
        parents = set(directories)
        for path in (*files, *directories):
            parents.update(
                parent for parent in path.parents if parent != root and parent.is_relative_to(root)
            )
        for parent in sorted(parents, key=lambda path: len(path.parts)):
            _no_links(parent)
            if not parent.exists():
                parent.mkdir()
                created.append(parent)
        # все данные и обратные копии готовы до первой замены пользовательского файла
        for path, content in files.items():
            _no_links(path)
            before, signature = original[path]
            mode = signature[-1] if signature else None
            if content is not None:
                staged[path] = _stage(path, content, mode)
            if before is not None:
                undo[path] = _stage(path, before, mode)
        _validate_layout(root, files, directories)
        if any(_state(path) != state for path, state in original.items()):
            raise _UnsafeRestore("file changed during restore preparation")
        for path, content in files.items():
            _workspace(root)
            if _state(path) != original[path]:
                raise _UnsafeRestore("file changed during restore")
            if content is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(staged[path], path)
            applied.append(path)
        yield
        completed = True
    except BaseException:
        for path in reversed(applied):
            try:
                _workspace(root)
                current, _signature = _state(path)
                if current != files[path]:
                    raise _UnsafeRestore("file changed before recovery")
                if path in undo:
                    os.replace(undo[path], path)
                else:
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                recovery_failed = True
        if recovery_failed:
            raise _UnsafeRestore("restore failed; recovery incomplete") from None
        raise
    finally:
        for temporary in (*staged.values(), *(undo.values() if not recovery_failed else ())):
            _discard_stage(temporary)
        if not completed:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass


def unified_diff(path: str, before: str | None, after: str | None) -> str:
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    parts = []
    for line in difflib.unified_diff(
        before_lines, after_lines, fromfile=f"a/{path}", tofile=f"b/{path}"
    ):
        parts.append(line)
        if not line.endswith("\n"):
            parts.append("\n\\ No newline at end of file\n")
    return "".join(parts)


def create_change_set(
    *,
    project_id: int | None,
    turn_id: int | None,
    workspace: Path,
    relative_path: str,
    before: str | None,
    after: str | None,
    description: str,
) -> int:
    diff = unified_diff(relative_path, before, after)
    workspace = _workspace(Path(os.path.abspath(workspace)))
    abs_path = _target(workspace, relative_path)
    _no_links(abs_path)
    existed = 1 if before is not None else 0
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO change_sets(project_id, turn_id, description, diff, owner_id) VALUES (?, ?, ?, ?, ?)",
            (project_id, turn_id, description, diff, _change_owner()),
        )
        change_set_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO file_changes(change_set_id, path, before_hash, after_hash, before_text, after_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                change_set_id,
                relative_path,
                sha256_text(before),
                sha256_text(after),
                before,
                after,
            ),
        )
        conn.execute(
            "INSERT INTO snapshots(change_set_id, path, content, existed) VALUES (?, ?, ?, ?)",
            (
                change_set_id,
                str(abs_path),
                before.encode("utf-8") if before is not None else None,
                existed,
            ),
        )
    return change_set_id


def _change_owner() -> str:
    from koddi.policy.account import local_owner

    return local_owner()


def _change_scope() -> tuple[str, str]:
    owner = _change_owner()
    return owner, "" if os.getenv("KODDI_INTERNAL_TEST") == "1" else owner


def latest_change_set(project_id: int | None = None, *, scoped: bool = False) -> dict | None:
    condition = "owner_id IN (?,?)" + (" AND project_id IS ?" if scoped else "")
    params = (*_change_scope(), project_id) if scoped else _change_scope()
    with db() as conn:
        row = conn.execute(f"SELECT * FROM change_sets WHERE {condition} ORDER BY id DESC LIMIT 1", params).fetchone()
        if row is None:
            return None
        item = dict(row)
        item.pop("owner_id", None)
        return attach_change_files(conn, [item])[0]


def list_change_sets(project_id: int | None = None, limit: int = 100) -> list[dict]:
    condition = "owner_id IN (?,?)" + (" AND project_id=?" if project_id is not None else "")
    params = (*_change_scope(), project_id, limit) if project_id is not None else (*_change_scope(), limit)
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM change_sets WHERE {condition} ORDER BY id DESC LIMIT ?", params).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value.pop("owner_id", None)
        return attach_change_files(conn, values)


def attach_change_files(conn, changes: list[dict]) -> list[dict]:
    if not changes:
        return changes
    by_id = {change["id"]: change for change in changes}
    for change in changes:
        change["files"] = []
    placeholders = ",".join("?" for _ in changes)
    for row in conn.execute(
        f"SELECT change_set_id,path,before_hash,after_hash FROM file_changes WHERE change_set_id IN ({placeholders}) ORDER BY id",
        tuple(by_id),
    ):
        by_id[row["change_set_id"]]["files"].append(
            {key: row[key] for key in ("path", "before_hash", "after_hash")}
        )
    return changes


def rollback_latest() -> dict:
    latest = latest_change_set()
    if not latest:
        return {"rolled_back": False, "reason": "no change sets"}
    return rollback_change_set(int(latest["id"]))


def rollback_change_set(change_set_id: int, force: bool = False) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM change_sets WHERE id=? AND owner_id IN (?,?)", (change_set_id, *_change_scope())).fetchone()
    if not row:
        return {"rolled_back": False, "reason": "change set not found", "id": change_set_id}
    latest = dict(row)
    if latest["status"] == "rolled_back":
        return {
            "rolled_back": False,
            "reason": "change set already rolled back",
            "id": change_set_id,
        }

    try:
        # блокируем второй rollback той же записи, но не обещаем блокировку внешних редакторов
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            status = conn.execute(
                "SELECT * FROM change_sets WHERE id=? AND owner_id IN (?,?)", (change_set_id, *_change_scope())
            ).fetchone()
            if status is None:
                return {"rolled_back": False, "reason": "change set not found", "id": change_set_id}
            if status["status"] == "rolled_back":
                return {
                    "rolled_back": False,
                    "reason": "change set already rolled back",
                    "id": change_set_id,
                }
            changes = conn.execute(
                "SELECT * FROM file_changes WHERE change_set_id=? ORDER BY id", (change_set_id,)
            ).fetchall()
            snapshots = conn.execute(
                "SELECT * FROM snapshots WHERE change_set_id=? ORDER BY id", (change_set_id,)
            ).fetchall()
            project = conn.execute(
                "SELECT path FROM projects WHERE id=?", (status["project_id"],)
            ).fetchone()
            if status["project_id"] is not None and project is None:
                raise _UnsafeRestore("project workspace is unavailable")
            root, pairs = _rollback_paths(changes, snapshots, project)
            files: dict[Path, bytes | None] = {}
            expected = {}
            conflicts: list[str] = []
            for path, change, snap in pairs:
                if snap["existed"] not in (0, 1) or (
                    snap["existed"] and not isinstance(snap["content"], bytes)
                ):
                    raise _UnsafeRestore("invalid snapshot content")
                if snap["existed"]:
                    before = change["before_text"]
                    digest = change["before_hash"]
                    if before is not None:
                        valid = snap["content"] == before.encode("utf-8") and digest in {
                            sha256_text(before),
                            sha256_text(before.replace("\r\n", "\n").replace("\r", "\n")),
                        }
                    else:
                        valid = hashlib.sha256(snap["content"]).hexdigest() == digest
                else:
                    valid = (
                        change["before_hash"] is None
                        and change["before_text"] is None
                        and snap["content"] in (None, b"")
                    )
                if not valid:
                    raise _UnsafeRestore("snapshot integrity check failed")
                current, signature = _state(path)
                expected[path] = (current, signature)
                if not _matches_after(current, change):
                    conflicts.append(change["path"])
                files[path] = snap["content"] if snap["existed"] else None
            if conflicts and not force:
                return {
                    "rolled_back": False,
                    "reason": "file conflicts",
                    "id": change_set_id,
                    "conflicts": conflicts,
                }
            with _apply_files(root, files, expected=expected):
                conn.execute(
                    "UPDATE change_sets SET status='rolled_back', rolled_back_at=CURRENT_TIMESTAMP WHERE id=?",
                    (change_set_id,),
                )
                # ошибка commit тоже должна запускать восстановление исходных файлов
                conn.commit()
            restored = [str(path) for path in files]
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {
            "rolled_back": False,
            "reason": "unsafe or unavailable rollback",
            "id": change_set_id,
            "error": str(exc) if isinstance(exc, _UnsafeRestore) else "filesystem operation failed",
        }
    return {"rolled_back": True, "id": change_set_id, "paths": restored, "forced": force}


def _rollback_paths(changes, snapshots, project):
    if not changes or len(changes) != len(snapshots):
        raise _UnsafeRestore("incomplete snapshots")

    def match(root):
        by_path = {}
        for snap in snapshots:
            path = _target(root, snap["path"])
            if path in by_path:
                raise _UnsafeRestore("duplicate snapshot path")
            by_path[path] = snap
        pairs = []
        seen = set()
        for change in changes:
            path = _target(root, change["path"])
            if path in seen or path not in by_path:
                raise _UnsafeRestore("snapshot path mismatch")
            seen.add(path)
            pairs.append((path, change, by_path[path]))
        return pairs

    if project is not None:
        root = _workspace(project["path"])
        return root, match(root)
    # старые записи без project_id не хранят workspace: выводим корень только
    # при однозначном полном совпадении всех путей, без LIKE и поиска по суффиксу
    candidates = set()
    for row in (*snapshots, *changes):
        path = _path(row["path"])
        if path.is_absolute():
            candidates.update(path.parents)
    matches = []
    for root in candidates:
        try:
            matches.append((root, match(root)))
        except _UnsafeRestore:
            continue
    if not matches:
        raise _UnsafeRestore("cannot determine snapshot workspace")
    if len(matches) > 1 and any(
        not _path(row["path"]).is_absolute() for row in (*changes, *snapshots)
    ):
        raise _UnsafeRestore("ambiguous snapshot workspace")
    root, pairs = max(matches, key=lambda item: len(item[0].parts))
    return _workspace(root), pairs


def _matches_after(current: bytes | None, change) -> bool:
    if change["after_hash"] is None:
        return current is None and change["after_text"] is None
    if current is None:
        return False
    if change["after_text"] is not None:
        return current == change["after_text"].encode("utf-8")
    # без after_text нельзя безопасно отличить старую нормализацию от новых правок
    return hashlib.sha256(current).hexdigest() == change["after_hash"]


def create_backup(
    workspace: Path, project_id: int | None, reason: str, retention: int = 10
) -> dict:
    if not isinstance(retention, int) or isinstance(retention, bool) or retention < 1:
        return {"ok": False, "error": "retention must be a positive integer"}
    temporary = None
    try:
        workspace = _workspace(Path(os.path.abspath(workspace)))
        backups_dir = _absolute(Path(os.path.abspath(data_dir() / "backups")))
        _no_links(backups_dir)
        if backups_dir.is_relative_to(workspace):
            raise _UnsafeRestore("backup storage is inside workspace")
        files, directories = _backup_contents(workspace)
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        archive = backups_dir / f"project-{project_id or 'workspace'}-{stamp}-{uuid4().hex}.zip"
        descriptor, name = tempfile.mkstemp(prefix=".koddi-backup-", dir=backups_dir)
        temporary = Path(name)
        with os.fdopen(descriptor, "w+b") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for directory in sorted(directories):
                    bundle.writestr(directory + "/", b"")
                for name, content in files.items():
                    bundle.writestr(name, content)
            handle.flush()
            os.fsync(handle.fileno())
        _no_links(backups_dir)
        os.replace(temporary, archive)
    except (OSError, ValueError, zipfile.BadZipFile):
        return {"ok": False, "error": "backup source is unsafe or unavailable"}
    finally:
        if temporary is not None:
            _discard_stage(temporary)
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO backups(project_id, path, backup_path, reason, size_bytes) VALUES (?, ?, ?, ?, ?)",
            (project_id, str(workspace), str(archive), reason, archive.stat().st_size),
        )
        backup_id = int(cursor.lastrowid)
        # сначала фиксируем новую копию, только потом удаляем устаревшие
        conn.commit()
        rows = conn.execute(
            "SELECT id, backup_path FROM backups WHERE project_id IS ? AND path=? ORDER BY id DESC",
            (project_id, str(workspace)),
        ).fetchall()
        for stale in rows[retention:]:
            try:
                path = _absolute(stale["backup_path"])
                if path == backups_dir or not path.is_relative_to(backups_dir):
                    continue
                _no_links(path)
                if path.is_dir():
                    # удаляем только проверенный каталог внутри собственного хранилища backups
                    for current, dirs, names in os.walk(path):
                        for name in (*dirs, *names):
                            _no_links(Path(current) / name)
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                conn.execute("DELETE FROM backups WHERE id=?", (stale["id"],))
            except (OSError, ValueError):
                continue
    return {
        "ok": True,
        "id": backup_id,
        "backup_path": str(archive),
        "size_bytes": archive.stat().st_size,
    }


def list_backups(project_id: int | None = None) -> list[dict]:
    with db() as conn:
        if project_id is None:
            rows = conn.execute("SELECT * FROM backups ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM backups WHERE project_id=? ORDER BY id DESC", (project_id,)
            ).fetchall()
    result = []
    for row in rows:
        try:
            path = _absolute(row["backup_path"])
            _no_links(path)
            available = path.is_dir() or path.is_file()
        except (OSError, ValueError):
            available = False
        result.append({**dict(row), "available": available})
    return result


def _backup_contents(source: Path) -> tuple[dict[str, bytes], set[str]]:
    _no_links(source)
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    seen = set()

    def entry(name: str, directory: bool) -> str:
        raw = name.replace("\\", "/")
        if ".." in raw.split("/") or PureWindowsPath(raw).root or PureWindowsPath(raw).drive:
            raise _UnsafeRestore("unsafe backup entry")
        relative = _path(raw).as_posix()
        if relative == "." and directory:
            return ""
        key = os.path.normcase(relative)
        if relative == "." or key in seen:
            raise _UnsafeRestore("duplicate or invalid backup entry")
        seen.add(key)
        if directory:
            directories.add(relative)
        return relative

    def walk_error(error):
        raise error

    if source.is_dir():
        for current, dirs, names in os.walk(source, onerror=walk_error):
            for name in sorted(dirs):
                path = Path(current) / name
                _no_links(path)
                entry(path.relative_to(source).as_posix(), True)
            for name in sorted(names):
                path = Path(current) / name
                content, _signature = _state(path)
                if content is None:
                    raise _UnsafeRestore("backup file disappeared")
                files[entry(path.relative_to(source).as_posix(), False)] = content
    elif source.is_file():
        with zipfile.ZipFile(source) as bundle:
            for member in bundle.infolist():
                mode = member.external_attr >> 16
                if (
                    stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or member.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise _UnsafeRestore("unsafe backup entry type")
                directory = member.is_dir() or member.filename.endswith("\\")
                if stat.S_IFMT(mode) == stat.S_IFDIR and not directory:
                    raise _UnsafeRestore("inconsistent backup entry type")
                name = entry(member.filename, directory)
                if not directory:
                    # read проверяет CRC до любых изменений в workspace
                    files[name] = bundle.read(member)
    else:
        raise _UnsafeRestore("backup is unavailable")
    return files, directories


def restore_backup(backup_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "backup not found"}
    item = dict(row)
    try:
        workspace = _workspace(item["path"])
        source = _absolute(item["backup_path"])
        files, directories = _backup_contents(source)
        targets = {_target(workspace, name): content for name, content in files.items()}
        target_dirs = {_target(workspace, name) for name in directories}
        if source in targets or any(source.is_relative_to(path) for path in targets):
            raise _UnsafeRestore("backup overlaps restore targets")
        with _apply_files(workspace, targets, target_dirs):
            pass
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
        EOFError,
        NotImplementedError,
    ) as exc:
        return {
            "ok": False,
            "error": str(exc)
            if isinstance(exc, _UnsafeRestore)
            else "backup is corrupt or unavailable",
        }
    return {"ok": True, "id": backup_id, "workspace": str(workspace)}
