from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.prompts import compose_coding_context, workspace_note
from koddi.api import create_app
from koddi.projects import add_project, home_project, is_home, list_projects


def test_chat_without_project_works_in_hidden_documents_folder(tmp_path, monkeypatch, _home_folder):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "home.sqlite3")
    storage.init_db()
    with TestClient(create_app()) as client:
        chat = client.post("/api/chats", json={"title": "Без проекта", "mode": "development"}).json()
        project = home_project()
        assert chat["project_id"] == project["id"] and project["hidden"] == 1
        assert _home_folder.is_dir() and project["path"] == str(_home_folder.resolve())
        # в списке проектов папки нет, повторный вызов не создаёт второй проект
        assert all(item["id"] != project["id"] for item in client.get("/api/projects").json())
        assert home_project()["id"] == project["id"]
        assert client.get("/api/chats").json()[0]["id"] == chat["id"]


def test_project_chat_stays_in_its_project(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "project.sqlite3")
    storage.init_db()
    folder = tmp_path / "site"
    project = add_project(str(folder))
    with TestClient(create_app()) as client:
        chat = client.post("/api/chats", json={"title": "Проект", "project_id": project["id"]}).json()
    assert chat["project_id"] == project["id"]
    assert [item["id"] for item in list_projects(include_status=False)] == [project["id"]]


def test_opening_documents_folder_as_project_shows_it(tmp_path, monkeypatch, _home_folder):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "open.sqlite3")
    storage.init_db()
    home = home_project()
    assert add_project(str(_home_folder))["id"] == home["id"]
    assert [item["id"] for item in list_projects(include_status=False)] == [home["id"]]


def test_model_knows_documents_folder_is_not_a_project(tmp_path, _home_folder):
    _home_folder.mkdir(parents=True)
    assert is_home(_home_folder) and not is_home(tmp_path) and not is_home(None)
    assert "not a project" in workspace_note(_home_folder) and workspace_note(tmp_path) is None
    context = compose_coding_context(mode="development", workspace=_home_folder, project_id=None)
    assert "workspace_note" in context["content"] and "Other files on the computer" in context["content"]
