import hashlib

import pytest

from koddi.storage import init_db
from koddi.storage.db import set_setting
from koddi.tools import ToolRunner


@pytest.fixture
def runner(tmp_path, monkeypatch):
    import koddi.storage.db as storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    init_db()
    set_setting("permission_profile", "ask")
    return ToolRunner(tmp_path, explicit_write_consent=True)


def test_exact_edit_preserves_unrelated_content_and_hash_guard(runner, tmp_path):
    path = tmp_path / "main.py"
    before = "# keep\nvalue = 1\n# tail\n"
    path.write_text(before, encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    result = runner.run(
        "file.edit",
        {
            "path": "main.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
            "expected_sha256": expected,
        },
    )
    assert result["ok"], result
    assert path.read_text() == "# keep\nvalue = 2\n# tail\n"
    stale = runner.run(
        "file.edit",
        {
            "path": "main.py",
            "old_text": "value = 2",
            "new_text": "value = 3",
            "expected_sha256": expected,
        },
    )
    assert stale["ok"] is False
    assert "value = 2" in path.read_text()


def test_ambiguous_edit_and_create_existing_file_are_not_success(runner, tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("same\nsame\n")
    assert (
        runner.run("file.edit", {"path": "x.txt", "old_text": "same", "new_text": "new"})["ok"]
        is False
    )
    assert runner.run("file.create", {"path": "x.txt", "content": "overwrite"})["ok"] is False
    assert path.read_text() == "same\nsame\n"


def test_multifile_patch_applies_and_bad_context_is_atomic(runner, tmp_path):
    (tmp_path / "one.py").write_text("a = 1\n")
    patch = "*** Begin Patch\n*** Update File: one.py\n@@\n-a = 1\n+a = 2\n*** Add File: two.py\n+b = 3\n*** End Patch"
    result = runner.run("patch.apply", {"patch": patch})
    assert result["ok"], result
    assert (tmp_path / "one.py").read_text() == "a = 2\n"
    assert (tmp_path / "two.py").read_text() == "b = 3\n"
    bad = "*** Begin Patch\n*** Update File: one.py\n@@\n-a = 2\n+a = 4\n*** Update File: two.py\n@@\n-missing\n+b = 4\n*** End Patch"
    assert runner.run("patch.apply", {"patch": bad})["ok"] is False
    assert (tmp_path / "one.py").read_text() == "a = 2\n"


def test_everything_inside_the_project_is_open_but_not_outside(runner, tmp_path):
    (tmp_path / ".gitignore").write_text("private.txt\n")
    (tmp_path / "private.txt").write_text("private")
    (tmp_path / ".env").write_text("MODE=dev")
    assert runner.run("file.read", {"path": "private.txt"})["ok"] is True
    assert runner.run("file.read", {"path": ".env"})["ok"] is True
    assert runner.run("file.read", {"path": ".gitignore"})["ok"] is True
    assert runner.run("file.create", {"path": "../escape.txt", "content": "bad"})["ok"] is False


def test_readonly_never_overridden_by_approval(runner, tmp_path):
    set_setting("permission_profile", "read-only")
    assert (
        runner.run("file.create", {"path": "new.py", "content": "x=1"}, approved=True)["ok"]
        is False
    )
    assert not (tmp_path / "new.py").exists()
    set_setting("permission_profile", "ask")
