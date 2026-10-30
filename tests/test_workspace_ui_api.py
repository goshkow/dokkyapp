from pathlib import Path

from fastapi.testclient import TestClient
from koddi import desktop
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


def test_legacy_override_cannot_switch_ui(monkeypatch):
    monkeypatch.setenv("KODDI_UI", "legacy")
    client = TestClient(create_app())
    page = client.get("/ui/")
    assert page.status_code == 200
    assert '<div id="root">' in page.text


def test_missing_react_does_not_fall_back_to_legacy(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("koddi.api.app")
    legacy = tmp_path / "koddi/desktop/web"
    legacy.mkdir(parents=True)
    (legacy / "index.html").write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(module, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "koddi/api/app.py"))
    assert TestClient(module.create_app()).get("/ui/").status_code == 404


def test_ui_assets_are_not_cached():
    client = TestClient(create_app())
    # имя общего чанка сборщик берёт от первого модуля в нём и меняет при правках фронта
    chunk = sorted((Path(desktop.__file__).parent / "web-next").glob("chunk-*.js"))[0].name
    for path in ("/ui/", "/ui/app.js", "/ui/app.css", f"/ui/{chunk}"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
