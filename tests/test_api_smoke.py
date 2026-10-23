from __future__ import annotations

import os
from importlib import import_module

os.environ.setdefault("KODDI_DATA_DIR", os.path.abspath(".test-data-api"))
os.environ.setdefault("koddi_PORTABLE", "1")

from fastapi.testclient import TestClient

from koddi.api import create_app

api_module = import_module("koddi.api.app")


def test_health_and_tool_schema() -> None:
    client = TestClient(create_app())
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    schema = client.get("/api/tools/schema")
    assert schema.status_code == 200
    names = {item["function"]["name"] for item in schema.json()}
    assert {"file.read", "file.write_patch", "changes.rollback"}.issubset(names)


def test_settings_roundtrip() -> None:
    client = TestClient(create_app())
    response = client.put(
        "/api/settings",
        json={
            "theme": "light",
            "locale": "en",
            "permission_profile": "trusted",
            "left_panel_collapsed": True,
            "right_panel_collapsed": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "light"
    assert data["locale"] == "en"
    assert data["permission_profile"] == "trusted"
    assert data["left_panel_collapsed"] is True
    assert data["right_panel_collapsed"] is False


def test_create_empty_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module.Path, "home", lambda: tmp_path)
    client = TestClient(create_app())

    response = client.post("/api/projects/create-empty", json={"name": "Demo: Project?"})
    assert response.status_code == 200
    project = response.json()
    assert project["name"] == "Demo Project"
    assert (tmp_path / "Documents" / "Koddi Projects" / "Demo Project").exists()


def test_browser_sessions_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/api/browser/sessions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_attachment_upload_indexes_document() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/attachments",
        files={"file": ("notes.txt", b"Koddi attachment test", "text/plain")},
    )
    assert response.status_code == 200
    item = response.json()
    assert item["status"] == "stored"
    assert item["document"]["status"] == "indexed"
    assert item["document"]["id"]


def test_message_edit_and_delete() -> None:
    client = TestClient(create_app())
    chat = client.post(
        "/api/chats", json={"title": "Editable", "mode": "chat", "model": "test"}
    ).json()
    with api_module.db() as conn:
        message_id = int(
            conn.execute(
                "INSERT INTO messages(chat_id, role, content) VALUES (?, 'user', 'before')",
                (chat["id"],),
            ).lastrowid
        )
    updated = client.put(
        f"/api/chats/{chat['id']}/messages/{message_id}", json={"content": "after"}
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "after"
    deleted = client.delete(f"/api/chats/{chat['id']}/messages/{message_id}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_chat_can_be_renamed_and_deleted() -> None:
    client = TestClient(create_app())
    chat = client.post(
        "/api/chats", json={"title": "Temporary", "mode": "chat", "model": "test"}
    ).json()
    renamed = client.patch(f"/api/chats/{chat['id']}", json={"title": "Renamed chat"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed chat"
    removed = client.delete(f"/api/chats/{chat['id']}")
    assert removed.status_code == 200
    assert removed.json() == {"ok": True, "chat_id": chat["id"]}


def test_log_search_and_export() -> None:
    client = TestClient(create_app())
    found = client.get("/api/logs/search", params={"query": "settings.update"})
    assert found.status_code == 200
    exported = client.get("/api/logs/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
