from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("KODDI_DATA_DIR", os.path.abspath(".test-data-tools"))
os.environ.setdefault("koddi_PORTABLE", "1")

from koddi.changes import latest_change_set
from koddi.storage import init_db
from koddi.storage.db import set_setting
from koddi.tools import ToolRunner
from koddi.policy import decide
import koddi.tools.runner as runner_module


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    import importlib

    storage = importlib.import_module("koddi.storage.db")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "profile" / "test.sqlite3")
    init_db()


def test_write_patch_records_change_and_rollback(tmp_path: Path) -> None:
    init_db()
    set_setting("permission_profile", "trusted")
    target = tmp_path / "hello.txt"
    target.write_text("before\n", encoding="utf-8")

    runner = ToolRunner(workspace=tmp_path)
    result = runner.run(
        "file.write_patch",
        {"path": "hello.txt", "content": "after\n", "description": "test update"},
    )
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "after\n"
    assert latest_change_set()["diff"]

    rollback = runner.run("changes.rollback", {})
    assert rollback["ok"] is True
    assert rollback["rolled_back"] is True
    assert target.read_text(encoding="utf-8") == "before\n"


def test_read_only_policy_allows_tree(tmp_path: Path) -> None:
    init_db()
    set_setting("permission_profile", "read-only")
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    result = ToolRunner(workspace=tmp_path).run("directory.tree", {"limit": 20})
    assert result["ok"] is True
    assert any(item["path"] == "a.txt" for item in result["items"])


def test_policy_allows_read_only_web_search_without_workspace() -> None:
    set_setting("permission_profile", "read-only")
    assert decide("web.search", {"query": "Koddi"}, None).action == "allow"


def test_policy_asks_before_opening_workspace_without_active_workspace() -> None:
    set_setting("permission_profile", "ask")
    assert decide("workspace.open", {"path": "C:/project"}, None).action == "ask"


def test_async_web_tool_runs_inside_agent_event_loop(monkeypatch) -> None:
    import asyncio

    async def fake_search(query: str, limit: int):
        return {"ok": True, "query": query, "limit": limit}

    monkeypatch.setattr(runner_module, "web_search", fake_search)
    set_setting("permission_profile", "read-only")

    async def scenario():
        return ToolRunner(workspace=None).run("web.search", {"query": "test", "limit": 2})

    result = asyncio.run(scenario())
    assert result == {"ok": True, "query": "test", "limit": 2}


def test_file_create_rejects_missing_required_path_before_dispatch(tmp_path: Path) -> None:
    init_db()
    set_setting("permission_profile", "trusted")
    result = ToolRunner(workspace=tmp_path).run("file.create", {"content": "print('hello')\n"})
    assert result["ok"] is False
    assert result["error_code"] == "missing_required_arguments"
    assert result["missing"] == ["path"]
    assert result["retryable"] is True


def test_verification_run_compiles_changed_python_files(tmp_path: Path) -> None:
    init_db()
    set_setting("permission_profile", "trusted")
    (tmp_path / "valid.py").write_text("print('ok')\n", encoding="utf-8")
    valid = ToolRunner(workspace=tmp_path).run("verification.run", {"paths": ["valid.py"]})
    assert valid["ok"] is True
    assert valid["checks"][0]["check"] == "python_compile"

    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    broken = ToolRunner(workspace=tmp_path).run("verification.run", {"paths": ["broken.py"]})
    assert broken["ok"] is False
    assert broken["checks"][0]["ok"] is False


def test_toolrunner_directory_snapshot_is_listed_and_restored(tmp_path):
    from koddi.changes import list_backups, restore_backup

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = b"\x00\xff\r\n" + b"full file\r\n" * 40000
    target = workspace / "nested" / "binary.dat"
    target.parent.mkdir()
    target.write_bytes(original)
    (tmp_path / "profile" / "backups").mkdir()
    set_setting("permission_profile", "trusted")
    snapshot = ToolRunner(workspace).run("workspace.snapshot", {"reason": "test"})
    assert snapshot["ok"]
    backup = next(item for item in list_backups() if item["backup_path"] == snapshot["backup_path"])
    assert backup["available"]
    target.write_bytes(b"changed")
    extra = workspace / "extra.txt"
    extra.write_bytes(b"keep")
    assert restore_backup(backup["id"])["ok"]
    assert target.read_bytes() == original
    assert extra.read_bytes() == b"keep"


def test_toolrunner_rollback_keeps_crlf_and_entire_large_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "large.txt"
    before = ("original\r\n" * 50000).encode("utf-8")
    target.write_bytes(before)
    set_setting("permission_profile", "trusted")
    runner = ToolRunner(workspace)
    assert runner.run("file.write_patch", {"path": "large.txt", "content": "after\r\n"})["ok"]
    assert runner.run("changes.rollback", {})["rolled_back"]
    assert target.read_bytes() == before


def test_toolrunner_deleted_recreated_file_is_not_overwritten(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "note.txt"
    path.write_bytes(b"before")
    set_setting("permission_profile", "trusted")
    runner = ToolRunner(workspace)
    assert runner.run("file.delete", {"path": "note.txt"})["ok"]
    path.write_bytes(b"new file")
    result = runner.run("changes.rollback", {})
    assert not result["rolled_back"]
    assert path.read_bytes() == b"new file"
