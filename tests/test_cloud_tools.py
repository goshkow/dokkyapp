import asyncio
import json
import threading
import time

import httpx
import pytest

from koddi.dokky import cloud
from koddi.dokky.client import DokkyClient
from test_dokky_client import MemoryStore

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class Server:
    """Подставной /api/v1: файлы, задачи и выдача результата."""

    def __init__(self, result=None, statuses=("running", "completed"), error=None):
        self.calls = []
        self.uploads = []
        self.result = result if result is not None else {"files": [{"id": "file_out1", "name": "picture.png", "media_type": "image/png", "size": len(PNG)}], "mode": "generate"}
        self.statuses = list(statuses)
        self.error = error
        self.created = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        self.calls.append((request.method, path))
        if request.method == "POST" and path == "/files":
            self.uploads.append(request.content)
            return httpx.Response(201, json={"id": f"file_up{len(self.uploads)}", "name": "x", "size": 1})
        if request.method == "POST" and path == "/coding/cloud/tasks":
            self.created = json.loads(request.content)
            assert request.headers.get("Idempotency-Key", "").startswith("ctask-")
            return httpx.Response(202, json={"id": "ctask_1", "status": "running"})
        if request.method == "GET" and path == "/coding/cloud/tasks/ctask_1":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            body = {"id": "ctask_1", "status": status}
            if status == "completed":
                body["result"] = self.result
            if status == "failed":
                body["error"] = self.error
            return httpx.Response(200, json=body)
        if request.method == "POST" and path == "/coding/cloud/tasks/ctask_1/cancel":
            return httpx.Response(200, json={"ok": True, "status": "cancelled"})
        if request.method == "GET" and path == "/files/file_out1/content":
            return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
        return httpx.Response(404, json={"code": "not_found", "message": "нет"})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import koddi.storage.db as storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "cloud.sqlite3")
    storage.init_db()

    def make(server: Server):
        client = DokkyClient(transport=httpx.MockTransport(server),
                             store=MemoryStore({"access_token": "t", "expires_at": time.time() + 900}))
        monkeypatch.setattr(cloud, "dokky", client)
        monkeypatch.setattr(cloud, "POLL_SECONDS", 0.01)

        async def access():
            return {"allowed": True}

        import koddi.dokky.access as access_module
        monkeypatch.setattr(access_module, "developer_access", access)
        return server
    return make


def run(kind, args, workspace, **kwargs):
    return asyncio.run(cloud.run(kind, args, workspace, **kwargs))


def test_image_uploads_only_listed_files_and_saves_without_overwriting(setup, tmp_path):
    server = setup(Server())
    (tmp_path / "logo.png").write_bytes(PNG)
    folder = tmp_path / "dokky-cloud"
    folder.mkdir()
    (folder / "picture.png").write_bytes(b"old")
    result = run("image", {"prompt": "Кот в шляпе", "references": ["logo.png"]}, tmp_path)
    assert result["ok"], result
    assert server.created == {"kind": "image", "prompt": "Кот в шляпе", "reference_ids": ["file_up1"]}
    assert result["files"][0]["path"] == "dokky-cloud/picture-2.png"
    assert (folder / "picture.png").read_bytes() == b"old"
    assert (folder / "picture-2.png").read_bytes() == PNG
    assert not list(folder.glob("*.part"))


@pytest.mark.parametrize("references,code", [
    (["../outside.png"], "invalid_args"),
    ([".env"], "invalid_args"),
    (["secret.pem"], "invalid_args"),
    (["script.exe"], "unsupported_file"),
    (["missing.png"], "invalid_args"),
    (["a.png", "b.png", "c.png", "d.png", "e.png"], "invalid_args"),
])
def test_image_refuses_bad_reference_files_before_any_request(setup, tmp_path, references, code):
    server = setup(Server())
    for name in ("a.png", "b.png", "c.png", "d.png", "e.png", "script.exe", "secret.pem", ".env"):
        (tmp_path / name).write_bytes(PNG)
    (tmp_path.parent / "outside.png").write_bytes(PNG)
    result = run("image", {"prompt": "x", "references": references}, tmp_path)
    assert not result["ok"] and result["error_code"] == code
    assert server.calls == []


def test_too_large_file_is_refused(setup, tmp_path, monkeypatch):
    server = setup(Server())
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 10)
    (tmp_path / "big.png").write_bytes(PNG)
    result = run("image", {"prompt": "x", "references": ["big.png"]}, tmp_path)
    assert result["error_code"] == "file_too_large" and server.calls == []


def test_save_to_must_stay_inside_the_project(setup, tmp_path):
    setup(Server())
    result = run("image", {"prompt": "x", "save_to": "../elsewhere"}, tmp_path)
    assert result["error_code"] == "invalid_args"


def test_document_requires_known_format_and_returns_draft_hint(setup, tmp_path):
    server = setup(Server(result={"files": [{"id": "file_out1", "name": "deck.pptx"}], "summary": "10 слайдов"}))
    assert run("document", {"instruction": "x", "format": "exe"}, tmp_path)["error_code"] == "unsupported_file"
    (tmp_path / "notes.md").write_text("# План")
    result = run("document", {"instruction": "Презентация по плану", "format": "PPTX", "inputs": ["notes.md"]}, tmp_path)
    assert result["ok"] and result["summary"] == "10 слайдов" and "локально" in result["next"]
    assert server.created["format"] == "pptx" and server.created["input_ids"] == ["file_up1"]
    assert (tmp_path / "dokky-cloud" / "deck.pptx").exists()


@pytest.mark.parametrize("url", [
    "http://youtube.com/watch?v=1", "https://evil.com/watch?v=1", "https://user@youtube.com/x",
    "https://youtube.com:8443/x", "file:///etc/passwd",
])
def test_video_accepts_only_https_youtube(setup, url):
    server = setup(Server())
    assert run("video", {"url": url}, None)["error_code"] == "invalid_args"
    assert server.calls == []


def test_video_returns_answer_text(setup):
    server = setup(Server(result={"answer": "## Итог\n00:10 - начало"}))
    result = run("video", {"url": "https://youtu.be/abc", "question": "О чём?", "scope": "relevant"}, None)
    assert result["ok"] and result["answer"].startswith("## Итог")
    assert server.created == {"kind": "video", "url": "https://youtu.be/abc", "question": "О чём?", "scope": "relevant"}


def test_server_failure_is_reported_with_its_message(setup, tmp_path):
    setup(Server(statuses=("failed",), error={"code": "quota_exceeded", "message": "Лимит исчерпан.", "retryable": False}))
    result = run("image", {"prompt": "x"}, tmp_path)
    assert result == {"ok": False, "error": "Лимит исчерпан.", "error_code": "quota_exceeded", "retryable": False}


def test_cancel_stops_task_on_server(setup, tmp_path):
    server = setup(Server(statuses=("running",)))
    stop = threading.Event()
    stop.set()
    result = run("image", {"prompt": "x"}, tmp_path, cancel_event=stop)
    assert result["error_code"] == "cancelled"
    assert ("POST", "/coding/cloud/tasks/ctask_1/cancel") in server.calls


def test_not_signed_in_runs_nothing(setup, tmp_path, monkeypatch):
    server = setup(Server())
    monkeypatch.setattr(cloud.dokky, "store", MemoryStore(None))
    result = run("image", {"prompt": "x"}, tmp_path)
    assert result["error_code"] == "not_signed_in" and server.calls == []


def test_policy_and_schemas(setup, tmp_path):
    from koddi.policy.engine import decide
    from koddi.policy.scopes import action_scopes
    from koddi.tools.schemas import TOOL_SCHEMAS_BY_NAME, validate_tool_arguments

    image = TOOL_SCHEMAS_BY_NAME["cloud.image"]["function"]["parameters"]
    assert image["required"] == ["prompt"]
    document = TOOL_SCHEMAS_BY_NAME["cloud.document"]["function"]["parameters"]
    assert set(document["required"]) == {"instruction", "format"}
    assert validate_tool_arguments("cloud.document", {"instruction": "x", "format": "exe"}) is not None
    # одно «да» на вид задачи, но не когда уходят файлы проекта
    assert [scope.key for scope in action_scopes("cloud.image", {"prompt": "x"}, tmp_path)] == ["cloud.image:dokky-cloud"]
    assert [scope.key for scope in action_scopes("cloud.image", {"prompt": "x", "save_to": "assets/"}, tmp_path)] == ["cloud.image:assets"]
    assert action_scopes("cloud.image", {"prompt": "x", "references": ["a.png"]}, tmp_path) == []
    import koddi.storage.db as storage

    # платное: спрашивается по режиму, в «Разрешить всё» - без вопроса, в режиме чтения - нельзя
    for profile, action in (("ask", "ask"), ("trusted", "review"), ("full", "allow"), ("read-only", "block")):
        storage.set_setting("permission_profile", profile)
        storage.set_setting("permission_profile:__test__", profile)
        assert decide("cloud.video", {"url": "https://youtu.be/a"}, tmp_path).action == action, profile
        assert decide("cloud.image", {"prompt": "x"}, tmp_path).action == action, profile


def test_hidden_folders_and_foreign_extensions_are_refused(setup, tmp_path):
    server = setup(Server(result={"files": [{"id": "file_out1", "name": "run.sh"}]}))
    assert run("image", {"prompt": "x", "save_to": ".github/workflows"}, tmp_path)["error_code"] == "invalid_args"
    assert server.calls == []
    result = run("image", {"prompt": "x"}, tmp_path)
    assert result["ok"] and result["files"][0]["path"] == "dokky-cloud/run.png"


def test_timeout_cancels_task_on_server(setup, tmp_path, monkeypatch):
    server = setup(Server(statuses=("running",)))
    monkeypatch.setitem(cloud.DEADLINES, "image", 0)
    result = run("image", {"prompt": "x"}, tmp_path)
    assert result["error_code"] == "timeout"
    assert ("POST", "/coding/cloud/tasks/ctask_1/cancel") in server.calls
