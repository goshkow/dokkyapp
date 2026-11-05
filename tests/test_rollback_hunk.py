"""Откат одного куска diff: сверка с текущим файлом, отмена отката, отказ без перезаписи."""

from __future__ import annotations

from pathlib import Path

import pytest

import koddi.storage.db as storage
from koddi.changes.service import create_change_set, rollback_change_set, rollback_hunk, unified_diff

OWNER = "__test__"
BEFORE = "".join(f"строка {number}\n" for number in range(1, 31))


def edit(text: str, *pairs: tuple[str, str]) -> str:
    for old, new in pairs:
        text = text.replace(old, new)
    return text


AFTER = edit(BEFORE, ("строка 3\n", "строка три\n"), ("строка 25\n", "строка двадцать пять\n"))


def headers(before: str, after: str) -> list[str]:
    return [line.strip() for line in unified_diff("a.txt", before, after).splitlines() if line.startswith("@@")]


@pytest.fixture
def change(tmp_path, monkeypatch):
    from koddi.projects import add_project

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "hunk.sqlite3")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: OWNER)
    storage.init_db()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text(AFTER, encoding="utf-8", newline="\n")
    project = add_project(str(root))
    change_id = create_change_set(project_id=project["id"], turn_id=None, workspace=root, relative_path="a.txt",
                                  before=BEFORE, after=AFTER, description="правка")
    return change_id, root.resolve() / "a.txt"


def test_hunks_roll_back_one_by_one_and_can_be_undone(change):
    change_id, path = change
    first, second = headers(BEFORE, AFTER)
    result = rollback_hunk(change_id, "a.txt", second)
    assert result["rolled_back"], result
    assert path.read_text(encoding="utf-8") == edit(BEFORE, ("строка 3\n", "строка три\n"))
    # файл уже не равен «после правки», но второй кусок находится по содержимому
    assert rollback_hunk(change_id, "a.txt", first)["rolled_back"]
    assert path.read_text(encoding="utf-8") == BEFORE
    # откат куска - обычный набор изменений: его можно отменить
    assert rollback_change_set(result["change_set_id"] + 1)["rolled_back"]
    assert path.read_text(encoding="utf-8") == edit(BEFORE, ("строка 3\n", "строка три\n"))


def test_shifted_hunk_is_found_by_content(change):
    change_id, path = change
    path.write_text("новая шапка\n\n" + AFTER, encoding="utf-8", newline="\n")
    assert rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[1])["rolled_back"]
    assert path.read_text(encoding="utf-8").startswith("новая шапка\n\n") and "строка 25\n" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("damage, reason", [
    (lambda text: text.replace("строка 25\n", "строка 25 от человека\n").replace("строка двадцать пять\n", "другое\n"), "conflict"),
    (lambda text: text.replace("строка 26\n", "поправил контекст\n"), "conflict"),
])
def test_changed_place_is_a_clear_refusal_and_file_is_untouched(change, damage, reason):
    change_id, path = change
    changed = damage(AFTER)
    path.write_text(changed, encoding="utf-8", newline="\n")
    result = rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[1])
    assert not result["rolled_back"] and result["reason"] == reason and "не тронут" in result["error"]
    assert path.read_text(encoding="utf-8") == changed


def test_second_rollback_of_same_hunk_says_already_done(change):
    change_id, path = change
    header = headers(BEFORE, AFTER)[1]
    assert rollback_hunk(change_id, "a.txt", header)["rolled_back"]
    state = path.read_text(encoding="utf-8")
    result = rollback_hunk(change_id, "a.txt", header)
    assert result["reason"] == "already_rolled_back" and path.read_text(encoding="utf-8") == state


def test_ambiguous_place_is_refused(tmp_path, change):
    change_id, path = change
    block = "".join(f"строка {number}\n" for number in range(22, 29)).replace("строка 25\n", "строка двадцать пять\n")
    path.write_text("повтор\n" + block + "между\n" + AFTER.replace("строка 1\n", ""), encoding="utf-8", newline="\n")
    result = rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[1])
    assert not result["rolled_back"] and "нескольких местах" in result["error"]


def test_same_text_far_from_its_place_is_refused(change):
    """Настоящее место кусок потерял, а такой же текст случайно стоит далеко: откат не должен вписать правку туда."""
    change_id, path = change
    head = "".join(f"строка {number}\n" for number in range(1, 7)).replace("строка 3\n", "строка три\n")
    tail = AFTER[len(head):]
    damaged = "другое начало\n" + "".join(f"другая {number}\n" for number in range(5)) + tail + head
    # длина файла почти та же: сдвиг объяснить нечем
    path.write_text(damaged, encoding="utf-8", newline="\n")
    result = rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[0])
    assert not result["rolled_back"] and "не тронут" in result["error"] and path.read_text(encoding="utf-8") == damaged


def test_unknown_hunk_foreign_path_and_whole_file(change, monkeypatch):
    change_id, path = change
    assert rollback_hunk(change_id, "a.txt", "@@ -1,1 +1,1 @@")["reason"] == "not_found"
    assert rollback_hunk(change_id, "../../etc/passwd", "@@ -1 +1 @@")["reason"] == "not_found"
    assert rollback_hunk(999, "a.txt", "@@ -1 +1 @@")["reason"] == "not_found"
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: "другой")
    assert rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[0])["reason"] == "not_found"
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: OWNER)
    root = Path(path).parent
    (root / "new.txt").write_text("x\n", encoding="utf-8")
    from koddi.projects import list_projects

    created = create_change_set(project_id=list_projects()[0]["id"], turn_id=None, workspace=root, relative_path="new.txt",
                                before=None, after="x\n", description="создан")
    assert rollback_hunk(created, "new.txt", "@@ -0,0 +1 @@")["reason"] == "whole_file"


def test_crlf_and_binary_current_file(change):
    change_id, path = change
    path.write_bytes(AFTER.replace("\n", "\r\n").encode("utf-8"))
    assert rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[0])["reason"] == "conflict"
    path.write_bytes(b"\xff\xfe\x00")
    assert "UTF-8" in rollback_hunk(change_id, "a.txt", headers(BEFORE, AFTER)[0])["error"]
