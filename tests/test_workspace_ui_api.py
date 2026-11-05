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


def test_ui_assets_are_revalidated_on_every_load():
    client = TestClient(create_app())
    # имя общего чанка сборщик берёт от первого модуля в нём и меняет при правках фронта
    chunk = sorted((Path(desktop.__file__).parent / "web-next").glob("chunk-*.js"))[0].name
    for path in ("/ui/", "/ui/app.js", "/ui/app.css", f"/ui/{chunk}"):
        response = client.get(path)
        assert response.status_code == 200
        # браузер обязан сверить файл при каждом запуске: после обновления старый чанк не останется
        assert response.headers["cache-control"] == "no-cache"
        assert "etag" in response.headers
        repeated = client.get(path, headers={"If-None-Match": response.headers["etag"]})
        assert repeated.status_code == 304


def test_open_in_editor_only_opens_project_files(tmp_path, monkeypatch):
    import importlib

    # koddi.api.app - и модуль, и объект приложения: берём модуль
    api_app = importlib.import_module("koddi.api.app")
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "Tool.app").mkdir()
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    opened = []
    monkeypatch.setattr(api_app, "open_file", lambda path, line=None: opened.append((path, line)) or {"ok": True})
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"path": str(root)}).json()
    url = f"/api/projects/{project['id']}/open-in-editor"
    assert client.post(url, json={"path": "src/main.py", "line": 3}).json() == {"ok": True}
    assert opened == [(str((root / "src" / "main.py").resolve()), 3)]
    for bad in ({"path": "../secret.txt"}, {"path": "Tool.app"}, {"path": str(tmp_path / "secret.txt")}):
        assert client.post(url, json=bad).status_code == 404
    for bad in ({}, {"path": ""}, {"path": "src/main.py\0"}, {"path": "src/main.py", "line": 0}, {"path": "src/main.py", "line": True}):
        assert client.post(url, json=bad).status_code == 422
    assert len(opened) == 1
    monkeypatch.setattr(api_app, "open_file", lambda path, line=None: {"ok": False, "errors": ["нет"]})
    assert client.post(url, json={"path": "src/main.py"}).status_code == 409


def test_editor_rejects_symlink_outside_project(tmp_path, monkeypatch):
    import importlib
    import pytest

    api_app = importlib.import_module("koddi.api.app")
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("s", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows не разрешает создавать символьные ссылки")
        raise
    monkeypatch.setattr(api_app, "open_file", lambda *_args, **_kwargs: pytest.fail("внешний файл открыт"))
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"path": str(root)}).json()
    assert client.post(f"/api/projects/{project['id']}/open-in-editor", json={"path": "link.txt"}).status_code == 404


def test_editor_fallback_never_launches_the_file(monkeypatch, tmp_path):
    from koddi.integrations import service

    monkeypatch.setattr(service.sys, "platform", "darwin")
    commands = service._file_commands(tmp_path / "run.command", None)
    assert commands[-1] == ["open", "-t", str(tmp_path / "run.command")]
    assert all(command[:2] != ["open", str(tmp_path / "run.command")] for command in commands)
    assert service.open_file(str(tmp_path / "missing.txt")) == {"ok": False, "errors": ["файл не найден"]}


def test_voice_recording_is_transcribed_and_deleted_and_paths_are_confined(tmp_path, monkeypatch):
    import importlib

    from koddi.storage.db import runtime_path

    api_app = importlib.import_module("koddi.api.app")
    heard = []

    def transcribe(path):
        heard.append(Path(path))
        assert Path(path).read_bytes() == b"webm-bytes"
        return {"ok": True, "text": "привет"}

    monkeypatch.setattr(api_app, "transcribe_audio", transcribe)
    client = TestClient(create_app(), base_url="http://127.0.0.1:8765")
    answer = client.post("/api/speech/recording", files={"file": ("voice.webm", b"webm-bytes", "audio/webm")})
    assert answer.json()["text"] == "привет"
    # запись не остаётся на диске и не попадает во вложения
    assert not heard[0].exists() and heard[0].parent.name == "speech"
    assert client.post("/api/speech/recording", files={"file": ("voice.exe", b"x", "audio/webm")}).status_code == 422
    # старый путь распознаёт только файлы вложений
    inside = runtime_path("attachments", "voice.wav")
    inside.write_bytes(b"webm-bytes")
    assert client.post("/api/speech/transcribe", json={"path": str(inside)}).json()["text"] == "привет"
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"x")
    for bad in ({"path": str(outside)}, {"path": ""}, {}, {"path": 5}, {"path": str(inside) + "\0"}, {"path": str(inside) + "/../../../etc/hosts"}):
        assert client.post("/api/speech/transcribe", json=bad).status_code == 422
    assert len(heard) == 2


def test_saved_classic_surface_becomes_dark_menu():
    from koddi.storage.db import set_setting

    client = TestClient(create_app())
    set_setting("ui_surface", "classic")
    assert client.get("/api/settings").json()["ui_surface"] == "side-dark"
    assert client.put("/api/settings", json={"ui_surface": "one-color"}).status_code == 200
    assert client.get("/api/settings").json()["ui_surface"] == "one-color"
    assert client.put("/api/settings", json={"ui_surface": "classic"}).status_code == 422
    set_setting("ui_surface", "side-dark")
