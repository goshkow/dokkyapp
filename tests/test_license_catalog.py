import json

import pytest
from fastapi.testclient import TestClient

import koddi.licenses as licenses
import koddi.storage.db as storage
from koddi.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "licenses.sqlite3")
    return TestClient(create_app())


def test_catalog_serves_collected_notices_and_marks_unknowns(client):
    result = client.get("/api/licenses")
    assert result.status_code == 200
    value = result.json()
    assert value["limitations"]
    assert any(
        item["id"] == "qt-chromium" and item["status"] != "notice_collected"
        for item in value["components"]
    )
    component = next(item for item in value["components"] if item["id"] == "npm-react")
    notice = component["notices"][0]
    assert "path" not in notice
    response = client.get(f"/api/licenses/{component['id']}/{notice['id']}")
    assert response.status_code == 200
    assert "Permission is hereby granted" in response.json()["text"]


def test_notice_ids_do_not_allow_arbitrary_local_file_read(client):
    assert client.get("/api/licenses/not-a-component/nonexistent").status_code == 404
    assert client.get("/api/licenses/dokky-notices/..%2F..%2FPROJECT_CONTEXT.md").status_code == 404


def test_changed_or_linked_notice_is_not_served(client, tmp_path, monkeypatch):
    original = licenses.catalog()
    component = next(item for item in original["components"] if item["notices"])
    notice = component["notices"][0]
    monkeypatch.setattr(licenses, "root", lambda: tmp_path)
    (tmp_path / "catalog.json").write_text(json.dumps(original))
    path = tmp_path / notice["path"]
    path.parent.mkdir(parents=True)
    path.write_text("changed")
    route = f"/api/licenses/{component['id']}/{notice['id']}"
    assert client.get(route).status_code == 409
    path.unlink()
    try:
        path.symlink_to(tmp_path / "catalog.json")
    except OSError:
        pytest.skip("symlinks require privileges on this Windows installation")
    assert client.get(route).status_code == 404
