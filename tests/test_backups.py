from pathlib import Path

import pytest

from koddi.changes import (
    create_backup,
    create_change_set,
    list_backups,
    rollback_change_set,
    restore_backup,
)
from koddi.storage.db import db, init_db


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    import importlib

    storage = importlib.import_module("koddi.storage.db")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "profile" / "test.sqlite3")
    init_db()


def test_backup_create_list_and_restore(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "note.txt"
    source.write_text("before", encoding="utf-8")
    backup = create_backup(workspace, None, "test", retention=2)
    assert backup["ok"] is True
    source.write_text("after", encoding="utf-8")
    restored = restore_backup(backup["id"])
    assert restored["ok"] is True
    assert source.read_text(encoding="utf-8") == "before"
    assert any(item["id"] == backup["id"] for item in list_backups())


def test_selected_rollback_detects_conflicts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "note.txt"
    path.write_text("after", encoding="utf-8")
    change_id = create_change_set(
        project_id=None,
        turn_id=None,
        workspace=workspace,
        relative_path="note.txt",
        before="before",
        after="after",
        description="test",
    )
    path.write_text("external", encoding="utf-8")
    conflicted = rollback_change_set(change_id)
    assert conflicted["rolled_back"] is False
    assert conflicted["conflicts"] == ["note.txt"]
    forced = rollback_change_set(change_id, force=True)
    assert forced["rolled_back"] is True
    assert path.read_text(encoding="utf-8") == "before"


def test_backups_created_in_same_timestamp_are_independent(tmp_path, monkeypatch):
    import koddi.changes.service as service

    class FixedClock:
        @staticmethod
        def now(zone):
            from datetime import datetime

            return datetime(2026, 10, 15, tzinfo=zone)

    monkeypatch.setattr(service, "datetime", FixedClock)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "note.txt"
    path.write_bytes(b"first")
    first = create_backup(workspace, None, "first", retention=2)
    path.write_bytes(b"second")
    second = create_backup(workspace, None, "second", retention=2)
    assert first["backup_path"] != second["backup_path"]
    assert restore_backup(first["id"])["ok"]
    assert path.read_bytes() == b"first"
    assert restore_backup(second["id"])["ok"]
    assert path.read_bytes() == b"second"


def test_retention_accepts_toolrunner_directory_and_keeps_other_workspace(tmp_path):
    from koddi.tools import ToolRunner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_bytes(b"original")
    (tmp_path / "profile" / "backups").mkdir()
    snapshot = ToolRunner(workspace).workspace_snapshot({})
    directory = Path(snapshot["backup_path"])
    other = tmp_path / "other"
    other.mkdir()
    unrelated = create_backup(other, None, "other")
    backup = create_backup(workspace, None, "replace", retention=1)
    assert backup["ok"]
    assert not directory.exists()
    assert Path(unrelated["backup_path"]).is_file()
    assert {row["id"] for row in list_backups()} == {backup["id"], unrelated["id"]}


@pytest.mark.parametrize("retention", [0, -1, True, "2"])
def test_invalid_retention_does_not_create_backup(tmp_path, retention):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert not create_backup(workspace, None, "invalid", retention=retention)["ok"]
    assert list_backups() == []


def test_retention_does_not_delete_unmanaged_backup_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "unmanaged"
    external.mkdir()
    (external / "keep.txt").write_bytes(b"keep")
    with db() as conn:
        conn.execute(
            "INSERT INTO backups(path, backup_path, reason) VALUES (?, ?, 'legacy')",
            (str(workspace), str(external)),
        )
    assert create_backup(workspace, None, "new", retention=1)["ok"]
    assert (external / "keep.txt").read_bytes() == b"keep"
