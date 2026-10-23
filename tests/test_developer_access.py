import importlib

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.dokky import access
from koddi.dokky.client import DokkyError, dokky

FREE = {"plan": "free", "developer": {"allowed": False, "reason_code": "plus_required", "reason": "Нужен Plus"}}
PLUS = {"plan": "super", "developer": {"allowed": True, "reason_code": None, "reason": None}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "access.sqlite3")
    storage.init_db()
    monkeypatch.setattr(access, "account_identity", lambda: "user-1")
    access.reset()
    yield
    access.reset()


def serve(monkeypatch, answers):
    """Подставной /subscription: ответы выдаются по очереди, последний повторяется."""
    calls = []

    async def request(method, path, **_):
        calls.append(path)
        value = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(value, Exception):
            raise value
        return httpx.Response(200, json=value)

    monkeypatch.setattr(dokky, "request", request)
    return calls


def test_plan_lock_is_read_from_the_server_decision():
    assert access.evaluate(FREE)["locked"] is True
    assert access.evaluate(FREE)["message"] == "Нужен Plus"
    assert access.evaluate(PLUS) == {"allowed": True, "locked": False, "reason": "", "message": "", "plan": "super"}


def test_exhausted_limits_do_not_lock_the_mode():
    for reason in ("window_exhausted", "overall_exhausted"):
        value = access.evaluate({"plan": "super", "developer": {"allowed": False, "reason_code": reason}})
        assert value["locked"] is False and value["reason"] == reason


def test_unknown_or_broken_answers_never_lock():
    for value in (None, {}, {"developer": None}, {"developer": {"allowed": "no"}}, []):
        assert access.evaluate(value)["locked"] is False


@pytest.mark.asyncio
async def test_unreachable_server_does_not_lock_the_user(monkeypatch):
    serve(monkeypatch, [DokkyError("нет сети")])
    value = await access.developer_access()
    assert value["allowed"] is None and value["locked"] is False


@pytest.mark.asyncio
async def test_answers_are_cached_and_refresh_forces_a_new_request(monkeypatch):
    calls = serve(monkeypatch, [PLUS, FREE])
    assert (await access.developer_access())["locked"] is False
    assert (await access.developer_access())["locked"] is False
    assert len(calls) == 1
    assert (await access.developer_access(refresh=True))["locked"] is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_plan_required_error_from_the_server_closes_access_at_once(monkeypatch):
    serve(monkeypatch, [PLUS])
    assert (await access.developer_access())["locked"] is False
    DokkyError("Нужен Plus", 403, "plan_required")
    assert access._cache["value"]["locked"] is True


@pytest.mark.asyncio
async def test_require_developer_rechecks_a_stale_denial_so_a_paying_user_is_not_held_back(monkeypatch):
    serve(monkeypatch, [FREE, PLUS])
    await access.developer_access()
    await access.require_developer()


@pytest.mark.asyncio
async def test_require_developer_raises_plan_required(monkeypatch):
    serve(monkeypatch, [FREE])
    with pytest.raises(HTTPException) as caught:
        await access.require_developer()
    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "plan_required" and caught.value.detail["message"] == "Нужен Plus"


def locked_client(monkeypatch, answers):
    """Приложение с уже созданным чатом; после этого включается провайдер Докки с заданными ответами подписки."""
    client = TestClient(create_app())
    chat_id = client.post("/api/chats", json={"title": "Старый", "mode": "development", "model": "dokky-code"}).json()["id"]
    app_module = importlib.import_module("koddi.api.app")
    monkeypatch.setattr(app_module, "remote_config", lambda: {"provider": "dokky"})
    return client, serve(monkeypatch, answers), chat_id


def count_chats():
    with storage.db() as conn:
        return conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]


def test_locked_user_cannot_create_chats_or_send_messages_or_queue(monkeypatch):
    client, _, chat_id = locked_client(monkeypatch, [FREE])
    before = count_chats()
    created = client.post("/api/chats", json={"title": "Новый чат", "mode": "development", "model": "dokky-code"})
    assert created.status_code == 403 and created.json()["detail"]["code"] == "plan_required"
    assert count_chats() == before
    for path, body in (
        (f"/api/chats/{chat_id}/messages", {"content": "привет", "mode": "development", "model": "dokky-code"}),
        (f"/api/chats/{chat_id}/queue", {"content": "привет", "mode": "development", "request_id": "r1"}),
        (f"/api/chats/{chat_id}/queue/resume", {}),
        (f"/api/chats/{chat_id}/queue/abc/promote?version=1", {}),
    ):
        answer = client.post(path, json=body)
        assert answer.status_code == 403, path
        assert answer.json()["detail"]["code"] == "plan_required", path


def test_locked_user_still_reads_and_manages_existing_chats(monkeypatch):
    client, _, chat_id = locked_client(monkeypatch, [FREE])
    assert client.get("/api/chats").status_code == 200
    assert client.get(f"/api/chats/{chat_id}/messages").status_code == 200
    assert client.get(f"/api/chats/{chat_id}/queue").status_code == 200
    renamed = client.patch(f"/api/chats/{chat_id}", json={"title": "Переименован"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Переименован"


def test_access_endpoint_reports_the_reason(monkeypatch):
    client, calls, _ = locked_client(monkeypatch, [FREE, PLUS])
    first = client.get("/api/dokky/access").json()
    assert first["locked"] is True and first["reason"] == "plus_required"
    assert client.get("/api/dokky/access").json()["locked"] is True and len(calls) == 1
    assert client.get("/api/dokky/access?refresh=true").json()["locked"] is False


def test_without_the_dokky_provider_nothing_is_checked(monkeypatch):
    calls = serve(monkeypatch, [FREE])
    client = TestClient(create_app())
    assert client.get("/api/chats").status_code == 200
    assert client.post("/api/chats", json={"title": "Чат", "mode": "development", "model": "dokky-code"}).status_code == 200
    assert calls == []
