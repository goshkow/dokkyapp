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
    assert (tmp_path / "Documents" / "Dokky Projects" / "Demo Project").exists()


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


def test_chat_delete_removes_its_attachment_files_and_document_text() -> None:
    from pathlib import Path

    client = TestClient(create_app())
    first = client.post("/api/chats", json={"title": "С файлами", "mode": "chat", "model": "test"}).json()
    second = client.post("/api/chats", json={"title": "Другой", "mode": "chat", "model": "test"}).json()

    def upload(chat_id: int, name: str, body: bytes) -> dict:
        response = client.post(f"/api/attachments?chat_id={chat_id}", files={"file": (name, body, "text/plain")})
        assert response.status_code == 200
        return response.json()

    own = upload(first["id"], "own.txt", b"private text only in the first chat")
    shared = upload(first["id"], "shared.txt", b"same file in both chats")
    kept = upload(second["id"], "shared.txt", b"same file in both chats")
    assert shared["stored_path"] == kept["stored_path"]

    assert client.delete(f"/api/chats/{first['id']}").status_code == 200
    assert not Path(own["stored_path"]).exists()
    # тот же файл нужен второму чату
    assert Path(kept["stored_path"]).exists()
    with api_module.db() as conn:
        ids = (own["id"], shared["id"])
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE attachment_id IN (?, ?)", ids).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE document_id IN (?, ?)",
            (own["document"]["id"], shared["document"]["id"]),
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE attachment_id=?", (kept["id"],)).fetchone()[0] == 1


def test_chat_with_a_running_turn_of_this_process_is_not_deleted() -> None:
    from koddi.agents.recovery import current_owner

    client = TestClient(create_app())
    chat = client.post("/api/chats", json={"title": "Делегирование", "mode": "chat", "model": "test"}).json()
    with api_module.db() as conn:
        conn.execute(
            "INSERT INTO turns(chat_id,status,owner_pid,owner_token) VALUES (?,'running',?,?)", (chat["id"], *current_owner())
        )
    assert client.delete(f"/api/chats/{chat['id']}").status_code == 409
    with api_module.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE chat_id=?", (chat["id"],))
    assert client.delete(f"/api/chats/{chat['id']}").status_code == 200


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


def test_browser_open_from_chat_link_is_validated(monkeypatch) -> None:
    client = TestClient(create_app())
    assert client.post("/api/browser/open", json={"url": "file:///etc/passwd"}).status_code == 422
    assert client.post("/api/browser/open", json={"url": "javascript:alert(1)"}).status_code == 422
    assert client.post("/api/browser/open", json={"url": "https://example.com", "project_id": "1"}).status_code == 422
    # localhost без запущенного сервера проекта закрыт так же, как для агента
    assert client.post("/api/browser/open", json={"url": "http://localhost:3000/"}).status_code == 403

    opened = {}

    def fake_open(url, project_id, **kwargs):
        opened.update(url=url, project_id=project_id, **kwargs)
        return {"ok": True, "session_id": 7, "url": url, "title": "", "local": False}

    monkeypatch.setattr(api_module, "browser_open", fake_open)
    response = client.post("/api/browser/open", json={"url": "https://example.com/a"})
    assert response.status_code == 200 and response.json()["session_id"] == 7
    assert opened["allow_private"] is False and opened["local_preview"] is False

    def failing_open(url, project_id, **kwargs):
        raise api_module_network.NetworkAccessError("non-public network address")

    monkeypatch.setattr(api_module, "browser_open", failing_open)
    response = client.post("/api/browser/open", json={"url": "https://example.com/b"})
    assert response.status_code == 400 and "non-public" in response.json()["detail"]


api_module_network = import_module("koddi.policy.network")


def test_chat_list_shows_agent_activity(monkeypatch) -> None:
    from koddi.agents.runs import agent_runs
    from koddi.storage.db import db

    client = TestClient(create_app())
    asking = client.post("/api/chats", json={"title": "Вопрос", "mode": "chat", "model": "test"}).json()
    working = client.post("/api/chats", json={"title": "Работа", "mode": "chat", "model": "test"}).json()
    idle = client.post("/api/chats", json={"title": "Тишина", "mode": "chat", "model": "test"}).json()
    with db() as conn:
        for chat in (asking, working):
            conn.execute("INSERT INTO turns(chat_id,status) VALUES (?, 'running')", (chat["id"],))
        turn = conn.execute("SELECT id FROM turns WHERE chat_id=?", (asking["id"],)).fetchone()[0]
        conn.execute("INSERT INTO agent_requests(turn_id,kind,payload_json) VALUES (?, 'question', '{}')", (turn,))
    monkeypatch.setattr(agent_runs, "active", lambda chat_id: chat_id in {asking["id"], working["id"]})
    states = {item["id"]: item["activity"] for item in client.get("/api/chats").json()}
    assert states[asking["id"]] == "question"
    assert states[working["id"]] == "working"
    assert states[idle["id"]] is None


def test_inspector_order_is_saved_and_validated() -> None:
    client = TestClient(create_app())
    order = ["plan", "diff", "environment"]
    assert client.put("/api/settings", json={"inspector_order": order}).json()["inspector_order"] == order
    assert client.get("/api/settings").json()["inspector_order"] == order
    for bad in (["plan", "plan"], ["unknown"], "plan", [1], ["plan"] * 9):
        assert client.put("/api/settings", json={"inspector_order": bad}).status_code == 422
    assert client.get("/api/settings").json()["inspector_order"] == order
    assert client.put("/api/settings", json={"inspector_order": []}).json()["inspector_order"] == []
