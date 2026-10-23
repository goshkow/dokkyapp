from __future__ import annotations

import importlib
import os
import sqlite3
import stat
import subprocess
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from koddi.changes import service
from koddi.storage.db import db, init_db


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    storage = importlib.import_module("koddi.storage.db")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "profile" / "test.sqlite3")
    init_db()


def change_set(workspace, entries, project=False):
    workspace.mkdir(exist_ok=True)
    project_id = None
    with db() as conn:
        if project:
            project_id = conn.execute(
                "INSERT INTO projects(name, path) VALUES ('test', ?)", (str(workspace),)
            ).lastrowid
        change_id = conn.execute(
            "INSERT INTO change_sets(project_id, description) VALUES (?, 'test')", (project_id,)
        ).lastrowid
        for name, before, after in entries:
            path = workspace / Path(name.replace("\\", "/"))
            if after is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(after.encode("utf-8"))
            conn.execute(
                "INSERT INTO file_changes(change_set_id, path, before_hash, after_hash, before_text, after_text) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    change_id,
                    name,
                    service.sha256_text(before),
                    service.sha256_text(after),
                    before,
                    after,
                ),
            )
            conn.execute(
                "INSERT INTO snapshots(change_set_id, path, content, existed) VALUES (?, ?, ?, ?)",
                (
                    change_id,
                    str(path),
                    before.encode("utf-8") if before is not None else None,
                    int(before is not None),
                ),
            )
    return change_id


def backup_row(workspace, source):
    workspace.mkdir(exist_ok=True)
    with db() as conn:
        return conn.execute(
            "INSERT INTO backups(path, backup_path, reason) VALUES (?, ?, 'test')",
            (str(workspace), str(source)),
        ).lastrowid


def assert_applied(change_id):
    with db() as conn:
        assert (
            conn.execute("SELECT status FROM change_sets WHERE id=?", (change_id,)).fetchone()[0]
            == "applied"
        )


def make_link(link, target, kind):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    else:
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc.winerror if os.name == 'nt' else exc.errno}")


def test_deleted_recreated_conflict_and_explicit_force(tmp_path):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", "before", None)])
    path = workspace / "note.txt"
    path.write_bytes(b"new file")
    result = service.rollback_change_set(change_id)
    assert result["conflicts"] == ["note.txt"]
    assert path.read_bytes() == b"new file"
    assert_applied(change_id)
    assert service.rollback_change_set(change_id, force=True)["rolled_back"]
    assert path.read_bytes() == b"before"


@pytest.mark.parametrize("before", [None, "before"])
def test_missing_after_file_is_a_conflict(tmp_path, before):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", before, "after")])
    path = workspace / "note.txt"
    path.unlink()
    assert service.rollback_change_set(change_id)["conflicts"] == ["note.txt"]
    assert not path.exists()
    assert_applied(change_id)


def test_snapshot_matching_is_exact_not_sql_like_or_suffix(tmp_path):
    workspace = tmp_path / "workspace"
    entries = [
        ("a.txt", "old a", "new a"),
        ("ba.txt", "old ba", "new ba"),
        ("%.txt", "old percent", "new percent"),
        ("_.txt", "old underscore", "new underscore"),
    ]
    change_id = change_set(workspace, entries)
    with db() as conn:
        conn.execute(
            "UPDATE snapshots SET path=? WHERE change_set_id=? AND path=?",
            (str(workspace / "./a.txt"), change_id, str(workspace / "a.txt")),
        )
    assert service.rollback_change_set(change_id)["rolled_back"]
    for name, before, _after in entries:
        assert (workspace / name).read_bytes() == before.encode()


@pytest.mark.parametrize("relative_snapshot", [False, True])
def test_windows_separators_and_normalized_paths(tmp_path, relative_snapshot):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("nested\\note.txt", "before", "after")], project=True)
    with db() as conn:
        snap = (
            "nested\\.\\note.txt"
            if relative_snapshot
            else str(workspace / "nested" / "note.txt").replace("/", "\\")
        )
        conn.execute("UPDATE snapshots SET path=? WHERE change_set_id=?", (snap, change_id))
        conn.execute(
            "UPDATE file_changes SET path=? WHERE change_set_id=?",
            (str(workspace / "nested" / "note.txt").replace("\\", "/"), change_id),
        )
    assert service.rollback_change_set(change_id)["rolled_back"]
    assert (workspace / "nested" / "note.txt").read_bytes() == b"before"


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "extra",
        "duplicate",
        "mismatch",
        "content",
        "outside",
        "truncated",
        "existed",
        "before_hash",
    ],
)
@pytest.mark.parametrize("force", [False, True])
def test_invalid_snapshot_set_never_partially_rolls_back(tmp_path, corruption, force):
    workspace = tmp_path / "workspace"
    change_id = change_set(
        workspace, [("a.txt", "old a", "new a"), ("b.txt", "old b", "new b")], project=True
    )
    outside = tmp_path / "b.txt"
    outside.write_bytes(b"outside")
    with db() as conn:
        last = conn.execute(
            "SELECT id FROM snapshots WHERE change_set_id=? ORDER BY id DESC", (change_id,)
        ).fetchone()[0]
        if corruption == "missing":
            conn.execute("DELETE FROM snapshots WHERE id=?", (last,))
        elif corruption == "extra":
            conn.execute(
                "INSERT INTO snapshots(change_set_id,path,content,existed) VALUES (?, ?, X'61', 1)",
                (change_id, str(outside)),
            )
        elif corruption == "duplicate":
            conn.execute("UPDATE snapshots SET path=? WHERE id=?", (str(workspace / "a.txt"), last))
        elif corruption == "content":
            conn.execute("UPDATE snapshots SET content=NULL WHERE id=?", (last,))
        elif corruption == "truncated":
            conn.execute("UPDATE snapshots SET content=X'6f6c64' WHERE id=?", (last,))
        elif corruption == "existed":
            conn.execute("UPDATE snapshots SET existed=0 WHERE id=?", (last,))
        elif corruption == "before_hash":
            conn.execute(
                "UPDATE file_changes SET before_hash='invalid' WHERE change_set_id=?", (change_id,)
            )
        else:
            conn.execute(
                "UPDATE snapshots SET path=? WHERE id=?",
                (str(outside if corruption == "outside" else workspace / "ba.txt"), last),
            )
    assert not service.rollback_change_set(change_id, force=force)["rolled_back"]
    assert (workspace / "a.txt").read_bytes() == b"new a"
    assert (workspace / "b.txt").read_bytes() == b"new b"
    assert outside.read_bytes() == b"outside"
    assert_applied(change_id)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["parent", "root"])
def test_rollback_rejects_redirected_paths_even_with_force(tmp_path, kind, location):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("nested/note.txt", "before", "after")])
    source = workspace if location == "root" else workspace / "nested"
    outside = tmp_path / "outside"
    source.rename(outside)
    make_link(source, outside, kind)
    assert not service.rollback_change_set(change_id, force=True)["rolled_back"]
    assert (
        outside / ("nested/note.txt" if location == "root" else "note.txt")
    ).read_bytes() == b"after"
    assert_applied(change_id)


def test_rollback_rejects_leaf_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", "before", "after")])
    path = workspace / "note.txt"
    outside = tmp_path / "outside.txt"
    path.rename(outside)
    make_link(path, outside, "symlink")
    assert not service.rollback_change_set(change_id, force=True)["rolled_back"]
    assert outside.read_bytes() == b"after"


@pytest.mark.parametrize("operation", ["rollback", "restore"])
@pytest.mark.parametrize("failure", ["stage", "replace"])
def test_write_failures_leave_all_original_files(tmp_path, monkeypatch, operation, failure):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("a.txt", "old a", "new a"), ("b.txt", "old b", "new b")])
    if operation == "restore":
        source = tmp_path / "backup.zip"
        with zipfile.ZipFile(source, "w") as bundle:
            bundle.writestr("a.txt", b"old a")
            bundle.writestr("b.txt", b"old b")
        backup_id = backup_row(workspace, source)
    original = service._stage if failure == "stage" else service.os.replace
    count = 0

    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        service if failure == "stage" else service.os,
        "_stage" if failure == "stage" else "replace",
        fail_second,
    )
    result = (
        service.rollback_change_set(change_id)
        if operation == "rollback"
        else service.restore_backup(backup_id)
    )
    assert not result["rolled_back" if operation == "rollback" else "ok"]
    assert (workspace / "a.txt").read_bytes() == b"new a"
    assert (workspace / "b.txt").read_bytes() == b"new b"
    assert not list(workspace.glob(".koddi-restore-*"))
    assert_applied(change_id)


def test_rollback_checks_again_after_staging(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("a.txt", "old a", "new a"), ("b.txt", "old b", "new b")])
    original = service._stage

    def concurrent_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        (workspace / "b.txt").write_bytes(b"external")
        return result

    monkeypatch.setattr(service, "_stage", concurrent_edit)
    assert not service.rollback_change_set(change_id)["rolled_back"]
    assert (workspace / "a.txt").read_bytes() == b"new a"
    assert (workspace / "b.txt").read_bytes() == b"external"
    assert_applied(change_id)


def test_database_failure_compensates_rollback(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("a.txt", "old", "new")])
    original = service.db

    class FailCommit:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, *args):
            return self.conn.execute(*args)

        def commit(self):
            raise sqlite3.OperationalError("injected commit failure")

    @contextmanager
    def broken_db():
        with original() as conn:
            try:
                yield FailCommit(conn)
            except BaseException:
                conn.rollback()
                raise

    monkeypatch.setattr(service, "db", broken_db)
    assert not service.rollback_change_set(change_id)["rolled_back"]
    assert (workspace / "a.txt").read_bytes() == b"new"
    assert_applied(change_id)


@pytest.mark.parametrize(
    "entry",
    [
        "../outside.txt",
        "..\\outside.txt",
        "/absolute.txt",
        "C:\\absolute.txt",
        "note.txt:stream",
        "note.txt.",
        "CON",
    ],
)
def test_unsafe_zip_entry_rejects_whole_restore(tmp_path, entry):
    workspace = tmp_path / "workspace"
    source = tmp_path / "backup.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("note.txt", b"old")
        bundle.writestr(entry, b"unsafe")
    backup_id = backup_row(workspace, source)
    (workspace / "note.txt").write_bytes(b"new")
    assert not service.restore_backup(backup_id)["ok"]
    assert (workspace / "note.txt").read_bytes() == b"new"
    assert list(workspace.iterdir()) == [workspace / "note.txt"]


@pytest.mark.parametrize(
    "collision", ["duplicate", "normalized", "ancestor", "directory", "symlink"]
)
def test_zip_collisions_and_symlinks_do_not_partially_restore(tmp_path, collision):
    source = tmp_path / "backup.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("note.txt", b"old")
        if collision == "duplicate":
            with pytest.warns(UserWarning):
                bundle.writestr("note.txt", b"duplicate")
        elif collision == "normalized":
            bundle.writestr("./note.txt", b"duplicate")
        elif collision == "ancestor":
            bundle.writestr("note.txt/child.txt", b"bad")
        elif collision == "directory":
            bundle.writestr("note.txt/", b"")
        else:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, b"../outside")
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)
    (workspace / "note.txt").write_bytes(b"new")
    assert not service.restore_backup(backup_id)["ok"]
    assert (workspace / "note.txt").read_bytes() == b"new"
    assert list(workspace.iterdir()) == [workspace / "note.txt"]


def test_corrupt_late_zip_member_does_not_write_earlier_files(tmp_path):
    source = tmp_path / "backup.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("a.txt", b"old a")
        bundle.writestr("b.txt", b"unique payload")
    source.write_bytes(source.read_bytes().replace(b"unique payload", b"broken payload"))
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)
    (workspace / "a.txt").write_bytes(b"new a")
    assert not service.restore_backup(backup_id)["ok"]
    assert (workspace / "a.txt").read_bytes() == b"new a"
    assert not (workspace / "b.txt").exists()


@pytest.mark.parametrize("format", ["zip", "directory"])
def test_existing_late_directory_conflict_does_not_write_earlier_files(tmp_path, format):
    source = tmp_path / "backup"
    if format == "zip":
        with zipfile.ZipFile(source, "w") as bundle:
            bundle.writestr("a.txt", b"old a")
            bundle.writestr("blocked/child.txt", b"child")
    else:
        (source / "blocked").mkdir(parents=True)
        (source / "a.txt").write_bytes(b"old a")
        (source / "blocked" / "child.txt").write_bytes(b"child")
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)
    (workspace / "a.txt").write_bytes(b"new a")
    (workspace / "blocked").write_bytes(b"keep")
    assert not service.restore_backup(backup_id)["ok"]
    assert (workspace / "a.txt").read_bytes() == b"new a"
    assert (workspace / "blocked").read_bytes() == b"keep"


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["workspace", "target_parent", "source", "source_child"])
def test_restore_rejects_redirected_paths(tmp_path, kind, location):
    source = tmp_path / "backup"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "note.txt").write_bytes(b"old")
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)
    (workspace / "nested").mkdir()
    (workspace / "nested" / "note.txt").write_bytes(b"new")
    redirected = {
        "workspace": workspace,
        "target_parent": workspace / "nested",
        "source": source,
        "source_child": source / "nested",
    }[location]
    outside = tmp_path / "outside"
    redirected.rename(outside)
    make_link(redirected, outside, kind)
    assert not service.restore_backup(backup_id)["ok"]
    assert (workspace / "nested" / "note.txt").read_bytes() == b"new"
    assert (source / "nested" / "note.txt").read_bytes() == b"old"


def test_zip_windows_separators_binary_full_file_and_empty_directory(tmp_path):
    source = tmp_path / "backup.zip"
    content = b"\x00\xff\r\n" * 200000
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("nested\\binary.dat", content)
        bundle.writestr("empty/", b"")
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)
    assert service.restore_backup(backup_id)["ok"]
    assert (workspace / "nested" / "binary.dat").read_bytes() == content
    assert (workspace / "empty").is_dir()


def test_rollback_does_not_decode_invalid_utf8_as_expected_text(tmp_path):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", "before", "\ufffd")])
    (workspace / "note.txt").write_bytes(b"\xff")
    assert service.rollback_change_set(change_id)["conflicts"] == ["note.txt"]
    assert (workspace / "note.txt").read_bytes() == b"\xff"


def test_late_conflict_does_not_restore_earlier_file(tmp_path):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("a.txt", "old a", "new a"), ("b.txt", "old b", None)])
    (workspace / "b.txt").write_bytes(b"external")
    assert service.rollback_change_set(change_id)["conflicts"] == ["b.txt"]
    assert (workspace / "a.txt").read_bytes() == b"new a"
    assert (workspace / "b.txt").read_bytes() == b"external"
    assert_applied(change_id)


def test_restore_staging_failure_removes_created_directories(tmp_path, monkeypatch):
    source = tmp_path / "backup.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("new/deep/note.txt", b"data")
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)

    def fail_stage(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(service, "_stage", fail_stage)
    assert not service.restore_backup(backup_id)["ok"]
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_legacy_hash_only_change_sets_remain_readable(tmp_path, newline):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", "before", "after" + newline)])
    with db() as conn:
        conn.execute("UPDATE file_changes SET after_text=NULL WHERE change_set_id=?", (change_id,))
    assert service.rollback_change_set(change_id)["rolled_back"]
    assert (workspace / "note.txt").read_bytes() == b"before"


def test_hash_only_snapshot_does_not_hide_newline_only_edit(tmp_path):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", "before", "after\n")])
    with db() as conn:
        conn.execute("UPDATE file_changes SET after_text=NULL WHERE change_set_id=?", (change_id,))
    (workspace / "note.txt").write_bytes(b"after\r\n")
    assert service.rollback_change_set(change_id)["conflicts"] == ["note.txt"]
    assert (workspace / "note.txt").read_bytes() == b"after\r\n"


def test_relative_only_snapshots_without_workspace_are_refused(tmp_path):
    workspace = tmp_path / "workspace"
    change_id = change_set(workspace, [("note.txt", "before", "after")])
    with db() as conn:
        conn.execute("UPDATE snapshots SET path='note.txt' WHERE change_set_id=?", (change_id,))
    assert not service.rollback_change_set(change_id, force=True)["rolled_back"]
    assert (workspace / "note.txt").read_bytes() == b"after"


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_create_backup_rejects_linked_sources(tmp_path, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"not part of workspace")
    make_link(workspace / "linked", outside, kind)
    assert not service.create_backup(workspace, None, "unsafe")["ok"]
    assert service.list_backups() == []
    assert (outside / "private.txt").read_bytes() == b"not part of workspace"


@pytest.mark.parametrize("location", ["target", "source"])
def test_restore_rejects_leaf_symlink(tmp_path, location):
    source = tmp_path / "backup"
    source.mkdir()
    (source / "note.txt").write_bytes(b"old")
    workspace = tmp_path / "workspace"
    backup_id = backup_row(workspace, source)
    (workspace / "note.txt").write_bytes(b"new")
    leaf = (workspace if location == "target" else source) / "note.txt"
    outside = tmp_path / "outside.txt"
    leaf.rename(outside)
    make_link(leaf, outside, "symlink")
    assert not service.restore_backup(backup_id)["ok"]
    assert (workspace / "note.txt").read_bytes() == b"new"
    assert (source / "note.txt").read_bytes() == b"old"
