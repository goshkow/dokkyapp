"""Git в окне: ветка, коммит выбранных файлов и отправка только с совпавшими веткой и remote."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from koddi.projects import gitpanel


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Тест")
    git(root, "config", "user.email", "test@example.com")
    (root / "a.txt").write_text("один\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-m", "первый")
    return root


@pytest.fixture
def linked(repo, tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-u", "origin", "main")
    return repo, bare


def test_not_a_repo(tmp_path):
    assert gitpanel.state(tmp_path) == {"repo": False}


def test_state_lists_files_and_preselects_safe_ones(repo):
    (repo / "a.txt").write_text("два\n", encoding="utf-8")
    (repo / "чужой новый.txt").write_text("x\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    state = gitpanel.state(repo)
    files = {item["path"]: item for item in state["files"]}
    assert state["branch"] == "main" and state["remote"] is None
    assert files["a.txt"]["status"] == "modified" and files["a.txt"]["suggested"]
    assert files["чужой новый.txt"]["status"] == "new" and not files["чужой новый.txt"]["suggested"]
    assert not files[".env"]["suggested"]


def test_commit_only_selected_files_and_keeps_foreign_index(repo):
    (repo / "a.txt").write_text("два\n", encoding="utf-8")
    (repo / "new.txt").write_text("новый\n", encoding="utf-8")
    (repo / "human.txt").write_text("человек\n", encoding="utf-8")
    git(repo, "add", "human.txt")
    result = gitpanel.commit(repo, "Правка\n\nподробности", ["a.txt", "new.txt"])
    assert result["ok"], result
    assert git(repo, "show", "--name-only", "--format=%s%n%b", "HEAD").split() == ["Правка", "подробности", "a.txt", "new.txt"]
    assert "A  human.txt" in git(repo, "status", "--porcelain")


@pytest.mark.parametrize("message, paths, text", [("", ["a.txt"], "сообщение"), ("x", [], "файлы"), ("x", ["."], "конкретные"), ("x", ["../z"], "вне рабочей")])
def test_commit_refusals(repo, message, paths, text):
    result = gitpanel.commit(repo, message, paths)
    assert not result["ok"] and text in result["error"]


def test_commit_hook_refusal_is_shown(repo):
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho 'тесты упали' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    (repo / "a.txt").write_text("два\n", encoding="utf-8")
    result = gitpanel.commit(repo, "Правка", ["a.txt"])
    assert not result["ok"] and "тесты упали" in result["error"]


def test_switch_refuses_to_overwrite_and_bad_names(repo):
    assert gitpanel.switch(repo, "feature", create=True)["ok"]
    (repo / "a.txt").write_text("в ветке\n", encoding="utf-8")
    git(repo, "commit", "-am", "в ветке")
    (repo / "a.txt").write_text("незакоммичено\n", encoding="utf-8")
    result = gitpanel.switch(repo, "main")
    assert not result["ok"] and "a.txt" in result["error"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "незакоммичено\n" and git(repo, "branch", "--show-current") == "feature"
    for name in ("-f", "--orphan", "a b", "x..y"):
        assert not gitpanel.switch(repo, name)["ok"]


def test_remote_branch_is_offered_and_switch_creates_local(linked, tmp_path):
    repo, bare = linked
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(bare), str(other)], check=True, capture_output=True)
    git(other, "config", "user.name", "Т")
    git(other, "config", "user.email", "t@example.com")
    git(other, "checkout", "-b", "from-remote")
    git(other, "commit", "--allow-empty", "-m", "x")
    git(other, "push", "-u", "origin", "from-remote")
    git(repo, "fetch")
    assert "from-remote" in gitpanel.state(repo)["branches"]
    assert gitpanel.switch(repo, "from-remote")["ok"]


def test_push_sends_only_current_branch_after_check(linked):
    repo, bare = linked
    git(repo, "commit", "--allow-empty", "-m", "второй")
    state = gitpanel.state(repo)
    assert state["ahead"] == 1 and state["behind"] == 0 and state["upstream"] == "main"
    assert not gitpanel.push(repo, "другая", "origin")["ok"]
    assert not gitpanel.push(repo, "main", "чужой")["ok"]
    assert git(bare, "log", "-1", "--format=%s", "main") == "первый"
    assert gitpanel.push(repo, "main", "origin")["ok"]
    assert git(bare, "log", "-1", "--format=%s", "main") == "второй"
    git(repo, "tag", "v1")
    git(repo, "checkout", "-b", "новая")
    assert gitpanel.state(repo)["upstream"] is None
    assert gitpanel.push(repo, "новая", "origin")["target"] == "новая"
    # отслеживание настроено, теги не уехали
    assert gitpanel.state(repo)["upstream"] == "новая" and not git(bare, "tag")


def test_branch_tracking_main_does_not_push_into_main(linked):
    repo, bare = linked
    git(repo, "checkout", "-b", "feature/x", "--track", "origin/main")
    git(repo, "commit", "--allow-empty", "-m", "в ветке")
    state = gitpanel.state(repo)
    assert state["upstream"] is None
    assert gitpanel.push(repo, "feature/x", "origin")["target"] == "feature/x"
    assert git(bare, "log", "-1", "--format=%s", "main") == "первый"
    assert git(bare, "log", "-1", "--format=%s", "feature/x") == "в ветке"


def test_own_chat_files_are_preselected_instead_of_all_tracked(repo, monkeypatch):
    (repo / "a.txt").write_text("правка человека\n", encoding="utf-8")
    (repo / "agent.txt").write_text("от агента\n", encoding="utf-8")
    monkeypatch.setattr(gitpanel, "chat_own_paths", lambda chat_id, project_id: {"agent.txt"})
    monkeypatch.setattr(gitpanel, "suggestion", lambda chat_id: "")
    files = {item["path"]: item["suggested"] for item in gitpanel.state(repo, chat_id=1, project_id=1)["files"]}
    assert files == {"a.txt": False, "agent.txt": True}


def test_rejected_push_stays_rejected_without_force(linked, tmp_path):
    repo, bare = linked
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(bare), str(other)], check=True, capture_output=True)
    git(other, "config", "user.name", "Т")
    git(other, "config", "user.email", "t@example.com")
    git(other, "commit", "--allow-empty", "-m", "чужой")
    git(other, "push")
    git(repo, "commit", "--allow-empty", "-m", "мой")
    result = gitpanel.push(repo, "main", "origin")
    assert not result["ok"] and git(bare, "log", "-1", "--format=%s", "main") == "чужой"


def test_token_in_url_never_reaches_interface(repo):
    git(repo, "remote", "add", "origin", "https://user:secret-token@example.com/x.git")
    assert "secret-token" not in gitpanel.state(repo)["remote_url"]
    assert "secret-token" not in gitpanel.clean_output("fatal: https://user:secret-token@example.com/x.git не найден")


def test_suggestion_prefers_agent_message():
    assert gitpanel.first_sentence("## Итог\n**Исправил** `парсер` дат. Ещё детали") == "Исправил парсер дат"
    assert gitpanel.first_sentence("Исправил парсер дат. Ещё детали") == "Исправил парсер дат"
    assert gitpanel.first_sentence("x" * 200) == ""


def test_suggestion_skips_status_and_keeps_the_change():
    # настоящий итог модели Light из платной проверки: первая фраза про тест, сама правка - в списке
    report = ("Тест проходит.\n\n## Что изменено\n- `pricing.py` — в `apply_discount` процент теперь делится на 100: "
              "`round(price - price * percent / 100, 2)`.\n\n## Причина падения\nФункция принимала долю.")
    assert gitpanel.first_sentence(report).startswith("pricing.py - в apply_discount процент теперь делится на 100")
    second = ("Тест проходит. Причина была в формуле скидки.\n\n## Что изменено\n- `pricing.py`, функция `apply_discount`: "
              "`percent` — это проценты (10 → 10%), а код вычитал `price * percent`, то есть 10 «цен» вместо 10 %.\n")
    assert gitpanel.first_sentence(second) == "pricing.py, функция apply_discount"
    assert gitpanel.first_sentence("Готово. Поправил формулу скидки.") == "Поправил формулу скидки"
    for status in ("Готово.", "Все тесты проходят.", "Тесты прошли!", "Done.", "All tests pass."):
        assert gitpanel.first_sentence(status) == ""
    assert gitpanel.first_sentence("Тест проходит теперь и на пустой корзине, поправил total") == \
        "Тест проходит теперь и на пустой корзине, поправил total"


@pytest.fixture
def client(tmp_path, monkeypatch, repo):
    from fastapi.testclient import TestClient

    import koddi.storage.db as storage
    from koddi.api.app import create_app
    from koddi.projects import add_project

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "git.sqlite3")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: "__test__")
    monkeypatch.setattr("koddi.api.app.local_owner", lambda: "__test__", raising=False)
    storage.init_db()
    project = add_project(str(repo))
    return TestClient(create_app(), base_url="http://127.0.0.1"), project["id"]


def test_api_commit_refuses_while_agent_works_and_push_needs_confirmation(client, repo, monkeypatch):
    import koddi.storage.db as storage

    # имя koddi.api.app в пакете бывает занято самим приложением FastAPI, поэтому модуль - из sys.modules
    app_module = sys.modules["koddi.api.app"]

    api, project_id = client
    (repo / "a.txt").write_text("два\n", encoding="utf-8")
    assert api.get(f"/api/projects/{project_id}/git").json()["branch"] == "main"
    assert api.post(f"/api/projects/{project_id}/git/push", json={"branch": "main", "remote": "origin"}).status_code == 422
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,mode,project_id,owner_id) VALUES ('x','development',?,'__test__')", (project_id,)).lastrowid
        conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,))
    # подмена на самом объекте приложения: класс мог быть перезагружен другими тестами
    monkeypatch.setattr(app_module.agent_runs, "active", lambda chat_id: chat_id == chat)
    body = {"message": "Правка", "paths": ["a.txt"]}
    assert api.post(f"/api/projects/{project_id}/git/commit", json=body).status_code == 409
    assert api.post(f"/api/projects/{project_id}/git/switch", json={"branch": "x", "create": True}).status_code == 409
    with storage.db() as conn:
        change = conn.execute("INSERT INTO change_sets(project_id,description,diff,owner_id) VALUES (?,'d','','__test__')", (project_id,)).lastrowid
    # откат из другого чата того же проекта тоже ждёт конца хода
    assert api.post(f"/api/changes/{change}/rollback-hunk", json={"path": "a.txt", "hunk": "@@ -1 +1 @@"}).status_code == 409
    assert api.post(f"/api/changes/{change}/rollback", json={}).status_code == 409
    monkeypatch.setattr(app_module.agent_runs, "active", lambda chat_id: False)
    assert api.post(f"/api/projects/{project_id}/git/commit", json=body).json()["ok"]


def test_api_suggests_agent_commit_message_and_chat_search(client, repo):
    import koddi.storage.db as storage
    from koddi.quality import gate

    api, project_id = client
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,mode,project_id,owner_id) VALUES ('x','development',?,'__test__')", (project_id,)).lastrowid
        foreign = conn.execute("INSERT INTO chats(title,mode,owner_id) VALUES ('y','development','другой')").lastrowid
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)).lastrowid
        conn.execute("INSERT INTO change_sets(project_id,turn_id,description,diff,owner_id) VALUES (?,?,'d','','__test__')", (project_id, turn))
        meta = json.dumps({"turn_id": turn})
        conn.execute("INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'assistant','Починил Парсер дат. Детали',?)", (chat, meta))
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user','парсер ПАРСЕР')", (chat,))
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'tool','парсер в выводе')", (chat,))
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user','парсер')", (foreign,))
    assert api.get(f"/api/projects/{project_id}/git", params={"chat_id": chat}).json()["suggestion"] == "Починил Парсер дат"
    # итог только про проверку: вместо него называем файлы хода
    with storage.db() as conn:
        conn.execute("UPDATE messages SET content='Тест проходит.' WHERE chat_id=? AND role='assistant'", (chat,))
        change_set = conn.execute("SELECT id FROM change_sets WHERE turn_id=?", (turn,)).fetchone()[0]
        conn.execute("INSERT INTO file_changes(change_set_id,path) VALUES (?,'pricing.py')", (change_set,))
    assert api.get(f"/api/projects/{project_id}/git", params={"chat_id": chat}).json()["suggestion"] == "Правка pricing.py"
    with storage.db() as conn:
        conn.execute("UPDATE messages SET content='Починил Парсер дат. Детали' WHERE chat_id=? AND role='assistant'", (chat,))
    gate.save(turn, commit_message="Парсер: чинит даты")
    assert api.get(f"/api/projects/{project_id}/git", params={"chat_id": chat}).json()["suggestion"] == "Парсер: чинит даты"
    found = api.get(f"/api/chats/{chat}/messages/search", params={"q": "парсер"}).json()
    assert len(found["messages"]) == 2 and found["total"] == 3
    assert api.get(f"/api/chats/{foreign}/messages/search", params={"q": "парсер"}).status_code == 404
    # подсказка не берётся из чужого чата
    assert api.get(f"/api/projects/{project_id}/git", params={"chat_id": foreign}).json()["suggestion"] == ""
