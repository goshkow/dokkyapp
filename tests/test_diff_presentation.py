import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.changes.service import unified_diff
from koddi.tools import ToolRunner
from koddi.tools.runner import _line_changes


@pytest.mark.parametrize(
    "before,after,markers",
    [
        ("old", "new", 2),
        ("old\n", "new", 1),
        ("old", "new\n", 1),
        ("old\n", "new\n", 0),
    ],
)
def test_unified_diff_keeps_unterminated_lines_separate(before, after, markers):
    result = unified_diff("example.py", before, after)
    assert "-old\n" in result and "+new\n" in result
    assert result.count("\\ No newline at end of file") == markers
    assert "-old+new" not in result


def test_blank_added_and_removed_lines_are_explicit():
    assert "\n+\n" in unified_diff("blank.txt", None, "\n")
    assert "\n-\n" in unified_diff("blank.txt", "\n", None)


def test_action_line_counts_agree_with_eof_only_diff():
    assert _line_changes("line", "line\n") == (1, 1)
    assert _line_changes("same\n", "same\n") == (0, 0)


def test_empty_file_change_keeps_path_metadata_in_both_apis(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "diff.sqlite3")
    app = create_app()
    with storage.db() as conn:
        project = conn.execute(
            "INSERT INTO projects(name,path) VALUES ('Diff',?)", (str(tmp_path),)
        ).lastrowid
        chat = conn.execute(
            "INSERT INTO chats(title,project_id) VALUES ('Diff',?)", (project,)
        ).lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)
        ).lastrowid
    result = ToolRunner(tmp_path, project, turn, True).run(
        "file.create", {"path": "empty.py", "content": ""}
    )
    assert result["ok"], result
    client = TestClient(app)
    chat_changes = client.get(f"/api/chats/{chat}/changes").json()
    all_changes = client.get("/api/changes").json()
    for changes in (chat_changes, all_changes):
        assert changes[0]["diff"] == ""
        assert changes[0]["files"][0]["path"] == "empty.py"
        assert changes[0]["files"][0]["before_hash"] is None
        assert changes[0]["files"][0]["after_hash"]
        assert "after_text" not in changes[0]["files"][0]
