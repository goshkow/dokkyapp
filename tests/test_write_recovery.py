from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier

import pytest

from koddi.changes import recovery
from koddi.policy.paths import PathAccessError
from koddi.storage.db import db, init_db, set_setting


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    storage = importlib.import_module("koddi.storage.db")
    profile = tmp_path / "profile"
    monkeypatch.setenv("KODDI_DATA_DIR", str(profile))
    monkeypatch.setattr(storage, "DB_PATH", profile / "koddi.sqlite3")
    init_db()
    set_setting("dokky_user", {"id": "alice"})
    root = tmp_path / "workspace"
    root.mkdir()
    with db() as conn:
        project = conn.execute(
            "INSERT INTO projects(name,path) VALUES ('test',?)", (str(root),)
        ).lastrowid
        chat = conn.execute(
            "INSERT INTO chats(project_id,owner_id) VALUES (?,'alice')", (project,)
        ).lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
    return {"owner_id": "alice", "project_id": project, "turn_id": turn, "root": root}


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def prepare(workspace, changes=None):
    changes = changes or {"note.txt": (b"before\r\n", b"after\n")}
    for name, (before, _after) in changes.items():
        path = workspace["root"] / name
        if before is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before)
    return recovery.prepare(**workspace, changes=changes)


def pending(workspace):
    return recovery.inspect_pending(workspace["project_id"])


def hashes(item):
    return {file["path"]: file["current_hash"] for file in item["files"]}


def crash_process(workspace, code, *args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "backend")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(workspace["root"]),
            str(workspace["project_id"]),
            str(workspace["turn_id"]),
            *args,
        ],
        env=env,
        capture_output=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")


def test_prepare_persists_exact_bytes_metadata_and_no_workspace_writes(workspace):
    before = b"\xff\x00\r\n" * 100000
    after = b"\xfe\x00\n" * 100000
    operation = prepare(
        workspace,
        {"note.txt": (before, after), "created.txt": (None, b""), "deleted.txt": (b"", None)},
    )
    with db() as conn:
        row = conn.execute("SELECT * FROM file_operations WHERE id=?", (operation,)).fetchone()
        entries = {
            entry["path"]: entry
            for entry in conn.execute(
                "SELECT * FROM file_operation_entries WHERE operation_id=?", (operation,)
            )
        }
    for key in ("owner_id", "project_id", "turn_id"):
        assert row[key] == workspace[key]
    assert row["root"] == str(workspace["root"])
    assert row["status"] == "prepared"
    assert entries["note.txt"]["before_content"] == before
    assert entries["note.txt"]["after_content"] == after
    assert entries["note.txt"]["before_hash"] == digest(before)
    assert entries["note.txt"]["after_hash"] == digest(after)
    assert entries["created.txt"]["before_hash"] is None
    assert entries["created.txt"]["after_hash"] == digest(b"")
    assert entries["deleted.txt"]["before_hash"] == digest(b"")
    assert entries["deleted.txt"]["after_hash"] is None
    assert (workspace["root"] / "note.txt").read_bytes() == before
    assert not (workspace["root"] / "created.txt").exists()
    assert pending(workspace)[0]["state"] == "unchanged"


@pytest.mark.parametrize(
    "stage,state", [("prepared", "unchanged"), ("one", "conflict"), ("all", "applied")]
)
def test_real_process_crash_is_classified_without_automatic_repair(workspace, stage, state):
    root = workspace["root"]
    (root / "a.txt").write_bytes(b"old a")
    (root / "b.txt").write_bytes(b"old b")
    crash_process(
        workspace,
        """
import os, sys
from pathlib import Path
from koddi.changes.recovery import prepare
root = Path(sys.argv[1])
prepare(owner_id='alice', project_id=int(sys.argv[2]), turn_id=int(sys.argv[3]), root=root,
        changes={'a.txt': (b'old a', b'new a'), 'b.txt': (b'old b', b'new b')})
if sys.argv[4] in ('one', 'all'):
    (root / 'a.txt').write_bytes(b'new a')
if sys.argv[4] == 'all':
    (root / 'b.txt').write_bytes(b'new b')
os._exit(23)
""",
        stage,
    )
    initial = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}
    item = pending(workspace)[0]
    assert item["state"] == state
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()
    } == initial
    assert "old a" not in json.dumps(item) and "new a" not in json.dumps(item)


def test_finish_checks_after_bytes_and_is_idempotent(workspace):
    operation = prepare(workspace)
    with pytest.raises(recovery.WriteRecoveryError):
        recovery.finish(operation)
    assert pending(workspace)[0]["state"] == "unchanged"
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"after\r\n")
    with pytest.raises(recovery.WriteRecoveryError):
        recovery.finish(operation)
    path.write_bytes(b"after\n")
    assert recovery.finish(operation)["status"] == "finished"
    assert recovery.finish(operation)["status"] == "finished"
    assert pending(workspace) == []


def test_owner_isolation_and_turn_validation(workspace):
    operation = prepare(workspace)
    set_setting("dokky_user", {"id": "bob"})
    assert pending(workspace) == []
    for action in (
        lambda: recovery.finish(operation),
        lambda: recovery.restore(operation, expected_hashes={"note.txt": digest(b"before\r\n")}),
        lambda: recovery.prepare(**workspace, changes={"other.txt": (None, b"new")}),
    ):
        with pytest.raises(recovery.WriteRecoveryError, match="unavailable"):
            action()
    with pytest.raises(recovery.WriteRecoveryError, match="turn"):
        recovery.prepare(**{**workspace, "owner_id": "bob"}, changes={"other.txt": (None, b"new")})
    assert not (workspace["root"] / "other.txt").exists()


def test_project_and_root_mismatch_is_denied(workspace, tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    with pytest.raises(recovery.WriteRecoveryError, match="project workspace"):
        recovery.prepare(**{**workspace, "root": outside}, changes={"note.txt": (None, b"new")})
    assert pending(workspace) == []


@pytest.mark.parametrize("name", ["../outside.txt", "..\\outside.txt", "C:/outside.txt"])
def test_prepare_denies_paths_outside_the_project_without_creating_journal(workspace, name):
    with pytest.raises(PathAccessError):
        recovery.prepare(**workspace, changes={name: (None, b"new")})
    assert pending(workspace) == []


@pytest.mark.parametrize("name", [".env", "credentials.json", ".git/config", "private/secret.txt"])
def test_prepare_allows_everything_inside_the_project(workspace, name):
    (workspace["root"] / ".gitignore").write_text("private/\n", encoding="utf-8")
    recovery.prepare(**workspace, changes={name: (None, b"new")})
    assert len(pending(workspace)) == 1


def test_prepare_allows_editing_gitignore(workspace):
    prepare(workspace, {".gitignore": (b"private/\n", b"dist/\n")})
    assert len(pending(workspace)) == 1


def test_prepare_requires_matching_before_and_unique_normalized_paths(workspace):
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"external")
    with pytest.raises(recovery.WriteRecoveryError, match="changed"):
        recovery.prepare(**workspace, changes={"note.txt": (b"old", b"new")})
    with pytest.raises(recovery.WriteRecoveryError, match="duplicate"):
        recovery.prepare(
            **workspace,
            changes={"note.txt": (b"external", b"new"), "./note.txt": (b"external", b"new")},
        )
    with pytest.raises(recovery.WriteRecoveryError, match="bytes"):
        recovery.prepare(**workspace, changes={"note.txt": ("external", "new")})
    assert pending(workspace) == []
    assert path.read_bytes() == b"external"


def test_concurrent_prepare_reserves_overlapping_paths(workspace):
    gate = Barrier(2)

    def attempt():
        gate.wait(timeout=5)
        try:
            return recovery.prepare(**workspace, changes={"new.txt": (None, b"new")})
        except recovery.WriteRecoveryError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(pending(workspace)) == 1
    assert not (workspace["root"] / "new.txt").exists()


def test_pending_reservation_covers_nested_workspace_and_other_owner(workspace):
    prepare(workspace, {"nested/note.txt": (b"before", b"after")})
    root = workspace["root"] / "nested"
    set_setting("dokky_user", {"id": "bob"})
    with pytest.raises(recovery.WriteRecoveryError, match="overlaps"):
        recovery.prepare(
            owner_id="bob",
            project_id=None,
            turn_id=None,
            root=root,
            changes={"note.txt": (b"before", b"new")},
        )


def test_explicit_restore_requires_fresh_hashes_and_conflict_confirmation(workspace):
    operation = prepare(workspace)
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"external")
    item = pending(workspace)[0]
    assert item["state"] == "conflict"
    with pytest.raises(recovery.WriteRecoveryError, match="confirmation"):
        recovery.restore(operation, expected_hashes=hashes(item))
    with pytest.raises(recovery.WriteRecoveryError, match="cover every"):
        recovery.restore(operation, expected_hashes={}, allow_conflicts=True)
    path.write_bytes(b"newer external")
    with pytest.raises(recovery.WriteRecoveryError, match="changed since"):
        recovery.restore(operation, expected_hashes=hashes(item), allow_conflicts=True)
    assert path.read_bytes() == b"newer external"
    assert (
        recovery.restore(
            operation, expected_hashes=hashes(pending(workspace)[0]), allow_conflicts=True
        )["status"]
        == "restored"
    )
    assert path.read_bytes() == b"before\r\n"
    assert pending(workspace) == []
    with db() as conn:
        reverse = conn.execute(
            "SELECT * FROM file_operation_entries WHERE operation_id=(SELECT id FROM file_operations WHERE recovery_of=?)",
            (operation,),
        ).fetchone()
    assert reverse["before_content"] == b"newer external"


def test_restore_handles_create_delete_empty_and_binary_files(workspace):
    changes = {
        "created.bin": (None, b"\xff\x00"),
        "deleted.bin": (b"\xfe\r\n", None),
        "empty.txt": (b"", b"new"),
    }
    operation = prepare(workspace, changes)
    for name, (_before, after) in changes.items():
        path = workspace["root"] / name
        if after is None:
            path.unlink()
        else:
            path.write_bytes(after)
    item = pending(workspace)[0]
    assert item["state"] == "applied"
    assert recovery.restore(operation, expected_hashes=hashes(item))["status"] == "restored"
    assert not (workspace["root"] / "created.bin").exists()
    assert (workspace["root"] / "deleted.bin").read_bytes() == b"\xfe\r\n"
    assert (workspace["root"] / "empty.txt").read_bytes() == b""


def test_unchanged_operation_is_closed_only_explicitly(workspace):
    operation = prepare(workspace)
    item = pending(workspace)[0]
    path = workspace["root"] / "note.txt"
    info = path.stat()
    assert recovery.restore(operation, expected_hashes=hashes(item))["status"] == "restored"
    assert path.stat().st_ino == info.st_ino
    assert path.stat().st_mtime_ns == info.st_mtime_ns
    assert pending(workspace) == []


def test_staging_failure_preserves_files_and_keeps_reverse_journal(workspace, monkeypatch):
    import koddi.changes.service as service

    operation = prepare(workspace)
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"after\n")

    def fail(*args):
        raise OSError("injected staging failure")

    original = service._stage
    monkeypatch.setattr(service, "_stage", fail)
    with pytest.raises(OSError):
        recovery.restore(operation, expected_hashes=hashes(pending(workspace)[0]))
    assert path.read_bytes() == b"after\n"
    items = pending(workspace)
    assert len(items) == 2
    child = next(item for item in items if item["recovery_of"] == operation)
    assert child["state"] == "unchanged"
    monkeypatch.setattr(service, "_stage", original)
    recovery.restore(child["id"], expected_hashes=hashes(child))
    parent = pending(workspace)[0]
    assert parent["id"] == operation and parent["status"] == "prepared"
    recovery.restore(operation, expected_hashes=hashes(parent))
    assert path.read_bytes() == b"before\r\n"


def test_crash_during_explicit_restore_retains_conflicting_content(workspace):
    operation = prepare(workspace)
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"external")
    crash_process(
        workspace,
        """
import os, sys
from koddi.changes.recovery import inspect_pending, restore
original = os.replace
def crash(source, target):
    original(source, target)
    os._exit(23)
os.replace = crash
item = inspect_pending(int(sys.argv[2]))[0]
restore(sys.argv[4], expected_hashes={f['path']: f['current_hash'] for f in item['files']}, allow_conflicts=True)
""",
        operation,
    )
    assert path.read_bytes() == b"before\r\n"
    items = pending(workspace)
    child = next(item for item in items if item["recovery_of"] == operation)
    assert child["state"] == "applied"
    with db() as conn:
        content = conn.execute(
            "SELECT before_content FROM file_operation_entries WHERE operation_id=?", (child["id"],)
        ).fetchone()[0]
    assert content == b"external"
    recovery.finish(child["id"])
    assert pending(workspace) == []


def test_corrupt_journal_is_a_conflict(workspace):
    operation = prepare(workspace)
    with db() as conn:
        conn.execute(
            "UPDATE file_operation_entries SET before_content=? WHERE operation_id=?",
            (b"corrupt", operation),
        )
    assert pending(workspace)[0]["state"] == "conflict"
    with pytest.raises(recovery.WriteRecoveryError, match="integrity"):
        recovery.restore(
            operation, expected_hashes={"note.txt": digest(b"before\r\n")}, allow_conflicts=True
        )
    assert (workspace["root"] / "note.txt").read_bytes() == b"before\r\n"


def test_non_reparse_root_replacement_is_detected(workspace, tmp_path):
    operation = prepare(workspace)
    root = workspace["root"]
    root.rename(tmp_path / "old-workspace")
    root.mkdir()
    (root / "note.txt").write_bytes(b"before\r\n")
    assert pending(workspace)[0]["state"] == "conflict"
    with pytest.raises(recovery.WriteRecoveryError, match="identity"):
        recovery.restore(
            operation, expected_hashes={"note.txt": digest(b"before\r\n")}, allow_conflicts=True
        )


def test_restore_is_not_affected_by_ignore_rules(workspace):
    operation = prepare(workspace)
    before = pending(workspace)[0]["state"]
    (workspace["root"] / ".gitignore").write_text("note.txt\n", encoding="utf-8")
    assert pending(workspace)[0]["state"] == before != "conflict"
    recovery.restore(operation, expected_hashes={"note.txt": digest(b"before\r\n")}, allow_conflicts=True)


@pytest.mark.parametrize("location", ["parent", "root"])
def test_real_junction_blocks_inspection_reads_and_explicit_restore(workspace, tmp_path, location):
    if os.name != "nt":
        pytest.skip("Windows junction")
    operation = prepare(workspace, {"nested/note.txt": (b"before", b"after")})
    root = workspace["root"]
    link = root / "nested" if location == "parent" else root
    original = tmp_path / "original"
    link.rename(original)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(original)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        pytest.skip("junction unavailable")
    assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert pending(workspace)[0]["state"] == "conflict"
    with pytest.raises(PathAccessError):
        recovery.restore(
            operation, expected_hashes={"nested/note.txt": digest(b"before")}, allow_conflicts=True
        )
    assert (root / "nested" / "note.txt").read_bytes() == b"before"


def test_failed_prepare_commit_never_changes_workspace(workspace, monkeypatch):
    storage = importlib.import_module("koddi.storage.db")
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"before")

    @contextmanager
    def fail_commit():
        conn = storage.connect()
        try:
            yield conn
            raise sqlite3.OperationalError("injected commit failure")
        finally:
            conn.close()

    monkeypatch.setattr(recovery, "db", fail_commit)
    with pytest.raises(sqlite3.OperationalError):
        recovery.prepare(**workspace, changes={"note.txt": (b"before", b"after")})
    assert path.read_bytes() == b"before"
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM file_operations").fetchone()[0] == 0


def test_migration_is_repeatable_and_enforces_blob_hash_pair(workspace):
    init_db()
    operation = prepare(workspace)
    with db() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM schema_migrations WHERE version='0011_file_operations.sql'"
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE file_operation_entries SET before_hash=NULL WHERE operation_id=?",
                (operation,),
            )


def test_failed_finish_commit_compensates_files_and_retains_both_journals(workspace, monkeypatch):
    operation = prepare(workspace)
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"after\n")
    original = recovery._transaction
    calls = 0

    @contextmanager
    def fail_second_commit():
        nonlocal calls
        calls += 1
        number = calls
        with original() as conn:
            yield conn
            if number == 2:
                raise sqlite3.OperationalError("injected finish commit failure")

    monkeypatch.setattr(recovery, "_transaction", fail_second_commit)
    with pytest.raises(sqlite3.OperationalError):
        recovery.restore(operation, expected_hashes=hashes(pending(workspace)[0]))
    assert path.read_bytes() == b"after\n"
    items = pending(workspace)
    assert len(items) == 2
    assert next(item for item in items if item["recovery_of"] == operation)["state"] == "unchanged"


def test_finish_reverse_journal_cannot_hide_conflict_in_untouched_parent_file(workspace):
    operation = prepare(workspace, {"a.txt": (b"old a", b"new a"), "b.txt": (b"old b", b"new b")})
    root = workspace["root"]
    (root / "a.txt").write_bytes(b"new a")
    crash_process(
        workspace,
        """
import os, sys
from koddi.changes.recovery import inspect_pending, restore
original = os.replace
def crash(source, target):
    original(source, target)
    os._exit(23)
os.replace = crash
item = inspect_pending(int(sys.argv[2]))[0]
restore(sys.argv[4], expected_hashes={f['path']: f['current_hash'] for f in item['files']})
""",
        operation,
    )
    child = next(item for item in pending(workspace) if item["recovery_of"] == operation)
    assert child["state"] == "applied"
    (root / "b.txt").write_bytes(b"external")
    with pytest.raises(recovery.WriteRecoveryError, match="parent still has conflicts"):
        recovery.finish(child["id"])
    assert len(pending(workspace)) == 2
    assert (root / "b.txt").read_bytes() == b"external"


def test_owner_change_during_inspection_does_not_return_old_owner_metadata(workspace, monkeypatch):
    prepare(workspace)
    original = recovery._current

    def switch_owner(*args):
        result = original(*args)
        set_setting("dokky_user", {"id": "bob"})
        return result

    monkeypatch.setattr(recovery, "_current", switch_owner)
    with pytest.raises(recovery.WriteRecoveryError, match="owner changed"):
        pending(workspace)


def test_projectless_inspection_is_not_a_wildcard(workspace, tmp_path):
    prepare(workspace)
    root = tmp_path / "projectless"
    root.mkdir()
    operation = recovery.prepare(
        owner_id="alice",
        project_id=None,
        turn_id=None,
        root=root,
        changes={"new.txt": (None, b"new")},
    )
    assert len(pending(workspace)) == 1
    assert [item["id"] for item in recovery.inspect_pending(None)] == [operation]


@pytest.mark.parametrize("changes", [{}, {"new.txt": (None, None)}, {"new.txt": (None, "text")}])
def test_empty_and_non_bytes_plans_are_rejected(workspace, changes):
    with pytest.raises(recovery.WriteRecoveryError):
        recovery.prepare(**workspace, changes=changes)
    assert pending(workspace) == []


def test_real_junction_prepare_does_not_read_private_target(workspace, tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows junction")
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.txt").write_bytes(b"private sentinel")
    link = workspace["root"] / "alias"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(private)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        pytest.skip("junction unavailable")

    def deny_read(*args):
        raise AssertionError("private target must not be read")

    monkeypatch.setattr(recovery, "_state", deny_read)
    with pytest.raises(PathAccessError):
        recovery.prepare(**workspace, changes={"alias/secret.txt": (None, b"new")})
    assert pending(workspace) == []


def test_cycle_in_recovery_chain_is_rejected(workspace):
    operation = prepare(workspace)
    path = workspace["root"] / "note.txt"
    path.write_bytes(b"after\n")
    with db() as conn:
        conn.execute("UPDATE file_operations SET recovery_of=id WHERE id=?", (operation,))
    with pytest.raises(recovery.WriteRecoveryError, match="cyclic"):
        recovery.restore(operation, expected_hashes=hashes(pending(workspace)[0]))
    assert path.read_bytes() == b"after\n"
