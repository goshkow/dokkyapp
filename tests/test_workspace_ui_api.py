from fastapi.testclient import TestClient
from koddi.api import create_app


def test_file_browser_editor_conflicts_and_path_safety(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"path": str(root)}).json()
    base = f"/api/projects/{project['id']}"
    listing = client.get(base + "/files").json()
    assert any(item["path"] == "main.py" for item in listing["items"])
    value = client.get(base + "/file", params={"path": "main.py"}).json()
    client.put("/api/settings", json={"permission_profile": "ask"})
    response = client.put(
        base + "/file", json={"path": "main.py", "content": "x = 2\n", "hash": value["hash"]}
    )
    assert response.status_code == 200
    assert (root / "main.py").read_text() == "x = 2\n"
    assert (
        client.put(
            base + "/file", json={"path": "main.py", "content": "x = 3", "hash": value["hash"]}
        ).status_code
        == 409
    )
    assert client.get(base + "/file", params={"path": "../outside.txt"}).status_code == 422
    client.put("/api/settings", json={"permission_profile": "read-only"})
    assert (
        client.put(
            base + "/file",
            json={"path": "main.py", "content": "x = 4", "hash": response.json()["hash"]},
        ).status_code
        == 403
    )
    client.put("/api/settings", json={"permission_profile": "ask"})
    assert client.delete(base).status_code == 200
    assert (root / "main.py").is_file()


def test_test_dokky_config_and_chat_workspace_assignment(tmp_path):
    client = TestClient(create_app(), base_url="http://127.0.0.1:8765")
    response = client.post("/api/ai/test-enable")
    assert response.json()["config"]["provider"] == "dokky-test"
    assert client.get("/api/models/options").json()[0]["source"] == "test"
    assert client.get("/api/test-dokky/v1/models").json()["data"][0]["test_only"]
    project = client.post("/api/projects", json={"path": str(tmp_path)}).json()
    chat = client.post("/api/chats", json={"title": "Тест"}).json()
    assert (
        client.patch(f"/api/chats/{chat['id']}", json={"project_id": project["id"]}).json()[
            "project_id"
        ]
        == project["id"]
    )
    assert client.get(f"/api/chats/{chat['id']}/export").status_code == 200
