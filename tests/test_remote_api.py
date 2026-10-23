from fastapi.testclient import TestClient

from koddi.api import create_app
from koddi.storage.db import get_setting


def test_server_config_models_selection_and_retired_routes():
    client = TestClient(create_app())
    response = client.put(
        "/api/ai/config",
        json={
            "base_url": "https://ai.example/v1",
            "model": "coder",
            "context_tokens": 65536,
            "models": [{"id": "coder"}, {"id": "other"}],
        },
    )
    assert response.status_code == 200
    assert {m["tag"] for m in client.get("/api/models/options").json()} == {"coder", "other"}
    assert client.post("/api/models/select", json={"model": "other"}).json()["ok"]
    assert get_setting("selected_model") == "other"
    assert client.post("/api/models/select", json={"model": "missing"}).status_code == 422
    assert client.get("/api/ollama/models").status_code == 404
    assert client.post("/api/huggingface/downloads", json={}).status_code == 404
    assert client.put("/api/ai/config", json={"base_url": "file:///secret"}).status_code == 422
    assert client.put("/api/ai/config", json={"api_key": "never-persist"}).status_code == 422


def test_chat_model_persists():
    client = TestClient(create_app())
    chat = client.post("/api/chats", json={"title": "Тест", "model": "coder"}).json()
    updated = client.patch(f"/api/chats/{chat['id']}", json={"model": "other"})
    assert updated.json()["model"] == "other"


def test_custom_mode_roundtrip():
    client = TestClient(create_app())
    response = client.put(
        "/api/modes/researcher",
        json={
            "name": "Исследователь",
            "instructions": "Inspect only. Do not edit files.",
            "tools": ["file.read", "web.search"],
        },
    )
    assert response.status_code == 200
    from koddi.tools import tool_schemas_for_mode

    assert {t["function"]["name"] for t in tool_schemas_for_mode("researcher")} == {
        "file.read",
        "web.search",
    }
    assert (
        client.put(
            "/api/modes/invalid", json={"name": "Ошибка", "instructions": "", "tools": ["unknown"]}
        ).status_code
        == 422
    )
    client.delete("/api/modes/researcher")
    assert tool_schemas_for_mode("researcher") == []
