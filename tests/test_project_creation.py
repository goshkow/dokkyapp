from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app


@pytest.fixture
def project_client(tmp_path, monkeypatch):
    api = import_module("koddi.api.app")
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "projects.sqlite3")
    monkeypatch.setattr(api, "project_directory", lambda: tmp_path / "projects")
    return TestClient(create_app()), tmp_path / "projects"


@pytest.mark.parametrize(
    "name", [".", "..", " ... ", "CON", "con.txt", "LPT1", "COM².log", "name. ", "../../outside"]
)
def test_project_names_stay_inside_project_directory(project_client, name):
    client, base = project_client
    response = client.post("/api/projects/create-empty", json={"name": name})
    assert response.status_code == 200, response.text
    project = response.json()
    path = Path(project["path"])
    assert path.parent == base.resolve() and path.is_dir()
    assert path.name not in {".", ".."} and not path.name.endswith((".", " "))
    assert project["name"] == path.name


def test_existing_file_and_empty_folder_are_never_reused(project_client):
    client, base = project_client
    base.mkdir()
    (base / "Project").write_text("do not change", encoding="utf-8")
    (base / "Project 2").mkdir()
    response = client.post("/api/projects/create-empty", json={"name": "Project"})
    assert response.status_code == 200, response.text
    assert Path(response.json()["path"]).name == "Project 3"
    assert (base / "Project").read_text(encoding="utf-8") == "do not change"
    assert not list((base / "Project 2").iterdir())


def test_simultaneous_creations_get_distinct_directories(project_client):
    client, base = project_client

    def create(_):
        response = client.post("/api/projects/create-empty", json={"name": "Concurrent"})
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert len({item["path"] for item in results}) == 4
    assert len({item["id"] for item in results}) == 4
    assert all(Path(item["path"]).parent == base.resolve() for item in results)
