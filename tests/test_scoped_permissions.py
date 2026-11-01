import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.policy.grants import permission_profile, private_network_allowed
from koddi.tools import ToolRunner


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "grants.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "a"})
    with storage.db() as conn:
        project = conn.execute(
            "INSERT INTO projects(name,path) VALUES (?,?)", ("p", str(tmp_path))
        ).lastrowid
    monkeypatch.delenv("KODDI_INTERNAL_TEST")
    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky.store, "load", lambda: {"access_token": "test-only"})
    return TestClient(create_app()), project, tmp_path


def test_bad_settings_cannot_grant_access(scoped):
    client, project, _ = scoped
    assert (
        client.put(
            "/api/settings",
            json={"project_id": project, "permission_profile": "trusted", "text_size": 0},
        ).status_code
        == 422
    )
    assert permission_profile(project) == "trusted"
    with storage.db() as conn:
        assert conn.execute("SELECT count(*) FROM project_grants").fetchone()[0] == 0
    assert storage.get_setting("permission_profile:a") is None


def test_grants_do_not_cross_account_or_project(scoped):
    client, project, _ = scoped
    assert (
        client.put(
            "/api/settings",
            json={"project_id": project, "permission_profile": "full", "confirm_full_access": True, "private_network": False},
        ).status_code
        == 200
    )
    assert permission_profile(project) == "full"
    assert not private_network_allowed(project)
    # полный доступ остался в проекте и на аккаунт не перешёл
    assert permission_profile(None) == "trusted"
    storage.set_setting("dokky_user", {"id": "b"})
    assert permission_profile(project) == "trusted"
    assert private_network_allowed(project)


def test_manual_edit_requires_exact_approval(scoped):
    client, project, path = scoped
    assert client.put("/api/settings", json={"project_id": project, "permission_profile": "ask"}).status_code == 200
    runner = ToolRunner(path, project)
    result = runner.run("file.create", {"path": "main.py", "content": "print(1)"})
    assert result["approval_required"] and not (path / "main.py").exists()
    assert runner.run("file.create", {"path": "main.py", "content": "print(1)"}, approved=True)[
        "ok"
    ]
    assert runner.run("file.create", {"path": "other.py", "content": "print(2)"})[
        "approval_required"
    ]


def test_cached_account_does_not_authorize_after_logout(scoped, monkeypatch):
    from koddi.dokky.client import dokky
    from koddi.policy.account import local_owner
    from fastapi import HTTPException

    monkeypatch.setattr(dokky.store, "load", lambda: {})
    with pytest.raises(HTTPException) as error:
        local_owner()
    assert error.value.status_code == 401


def test_moved_project_folder_can_be_pointed_at_again(tmp_path, monkeypatch) -> None:
    import shutil

    from fastapi.testclient import TestClient

    from koddi.api import create_app
    from koddi.storage import db as storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "moved.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "a"})
    old, new, other = tmp_path / "old", tmp_path / "new", tmp_path / "other"
    old.mkdir()
    other.mkdir()
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"path": str(old)}).json()
    busy = client.post("/api/projects", json={"path": str(other)}).json()
    with storage.db() as conn:
        chat = conn.execute(
            "INSERT INTO chats(project_id,title,owner_id) VALUES (?,'чат','a')", (project["id"],),
        ).lastrowid
    shutil.move(str(old), str(new))
    listed = {item["id"]: item for item in client.get("/api/projects").json()}
    assert listed[project["id"]]["exists"] is False
    # в никуда и в папку другого проекта нельзя
    assert client.post(f"/api/projects/{project['id']}/relocate", json={"path": str(tmp_path / "нет")}).status_code == 409
    assert client.post(f"/api/projects/{project['id']}/relocate", json={"path": busy["path"]}).status_code == 409
    assert client.post("/api/projects/99999/relocate", json={"path": str(new)}).status_code == 404
    # пока агент работает, папку не меняем
    with storage.db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)).lastrowid
    assert client.post(f"/api/projects/{project['id']}/relocate", json={"path": str(new)}).status_code == 409
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE id=?", (turn,))
    moved = client.post(f"/api/projects/{project['id']}/relocate", json={"path": str(new)}).json()
    assert moved["id"] == project["id"] and moved["path"] == str(new.resolve()) and moved["exists"] is True
    with storage.db() as conn:
        assert conn.execute("SELECT project_id FROM chats WHERE id=?", (chat,)).fetchone()[0] == project["id"]
