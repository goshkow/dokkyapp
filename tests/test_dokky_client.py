import asyncio
import base64
import hashlib
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import koddi.storage.db as storage
from koddi.dokky.client import API, ORIGIN, DokkyClient, DokkyError, dpapi


class MemoryStore:
    def __init__(self, value=None):
        self.value = value or {}

    def load(self):
        return self.value.copy()

    def save(self, value):
        self.value = value.copy()

    def clear(self):
        self.value = {}


def with_par(handler, seen=None):
    """Подставной сервер: /oauth/par отвечает 201 и записывает форму, остальное делает handler."""

    def answer(request):
        form = parse_qs(request.content.decode())
        if seen is not None:
            seen.append(form)
        return httpx.Response(201, json={"request_uri": "urn:ietf:params:oauth:request_uri:abc123", "expires_in": 600})

    if asyncio.iscoroutinefunction(handler):
        async def inner(request):
            return answer(request) if request.url.path == "/oauth/par" else await handler(request)
    else:
        def inner(request):
            return answer(request) if request.url.path == "/oauth/par" else handler(request)
    return httpx.MockTransport(inner)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dokky.sqlite3")
    storage.init_db()


@pytest.mark.asyncio
async def test_pkce_login_exact_callback_and_no_tokens_in_ui(monkeypatch):
    calls = []

    def server(request):
        calls.append(request)
        if request.url.path == "/oauth/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code_verifier"] == [client.pending_verifier]
            return httpx.Response(
                200,
                json={
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "expires_in": 900,
                    "token_type": "Bearer",
                },
            )
        assert request.url == API + "/bootstrap"
        assert request.headers["Authorization"] == "Bearer access-secret"
        return httpx.Response(
            200,
            json={
                "user": {"id": "user_1", "display_name": "Тест"},
                "models": [{"id": "dokky-default", "context_tokens": 32768}],
            },
        )

    pars = []
    client = DokkyClient(transport=with_par(server, pars), store=MemoryStore())
    flow = await client.begin("http://127.0.0.1:8878/api/dokky/auth/callback")
    # в ссылке только идентификатор приложения и короткий request_uri
    link = parse_qs(urlsplit(flow["authorize_url"]).query)
    assert link == {"client_id": ["dokky-app"], "request_uri": ["urn:ietf:params:oauth:request_uri:abc123"]}
    form = pars[0]
    client.pending_verifier = client.pending["verifier"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(client.pending_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert form["code_challenge"] == [expected] and form["code_challenge_method"] == ["S256"] and form["response_type"] == ["code"]
    assert "access_token" not in flow["authorize_url"]
    with pytest.raises(DokkyError) as wrong:
        await client.callback(code="code", state="wrong", issuer=ORIGIN)
    assert wrong.value.status == 404
    assert [call.url.path for call in calls] == ["/oauth/par"] or not [c for c in calls if c.url.path == "/oauth/token"]
    await client.callback(code="code", state=form["state"][0], issuer=ORIGIN)
    assert client.store.load()["access_token"] == "access-secret"
    assert client.login_status()["status"] == "done"
    with pytest.raises(DokkyError) as repeated:
        await client.callback(code="code", state=form["state"][0], issuer=ORIGIN)
    assert repeated.value.status == 404
    import koddi.dokky.router as routes
    from fastapi.testclient import TestClient
    from koddi.api import create_app

    monkeypatch.setattr(routes, "dokky", client)
    response = TestClient(create_app()).get("/api/dokky/session")
    assert response.json()["authenticated"]
    assert "secret" not in response.text and "refresh_token" not in response.text


@pytest.mark.asyncio
async def test_refresh_is_serialized_and_revocation_clears_session():
    count = 0

    async def server(request):
        nonlocal count
        if request.url.path == "/oauth/token":
            count += 1
            await asyncio.sleep(0.02)
            return httpx.Response(
                200,
                json={
                    "access_token": "new",
                    "refresh_token": "rotated",
                    "expires_in": 900,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(401, json={"error": {"code": "session_revoked"}})

    store = MemoryStore({"access_token": "old", "refresh_token": "refresh", "expires_at": 0})
    client = DokkyClient(transport=httpx.MockTransport(server), store=store)
    assert await asyncio.gather(*(client.access_token() for _ in range(5))) == ["new"] * 5
    assert count == 1
    with pytest.raises(DokkyError) as error:
        await client.request("GET", "/me")
    assert error.value.status == 401 and not store.load()


@pytest.mark.parametrize(
    "redirect",
    [
        "https://evil.example/callback",
        "http://localhost:123/api/dokky/auth/callback",
        "http://127.0.0.1:123/wrong",
    ],
)
@pytest.mark.asyncio
async def test_login_rejects_non_loopback_redirects(redirect):
    with pytest.raises(DokkyError):
        await DokkyClient(store=MemoryStore()).begin(redirect)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_windows_storage_is_encrypted():
    raw = b'{"access_token":"token-should-not-be-plain"}'
    encrypted = dpapi(raw)
    assert raw not in encrypted and b"token-should-not-be-plain" not in encrypted
    assert dpapi(encrypted, decrypt=True) == raw


def test_production_config_is_bound_to_dokky(monkeypatch):
    from koddi.remote.client import remote_config, save_remote_config

    monkeypatch.delenv("KODDI_INTERNAL_TEST", raising=False)
    monkeypatch.setenv("KODDI_AI_BASE_URL", "https://evil.example")
    assert remote_config()["base_url"] == API
    storage.set_setting(
        "dokky_models",
        [
            {"id": "dokky-default", "roles": ["chat", "search"]},
            {"id": "dokky-code", "roles": ["developer"], "context_tokens": 200000},
        ],
    )
    storage.set_setting("selected_model", "dokky-default")
    assert remote_config()["model"] == "dokky-code"
    assert remote_config()["context_tokens"] == 200000
    assert [model["id"] for model in remote_config()["models"]] == ["dokky-code"]
    with pytest.raises(ValueError):
        save_remote_config({"base_url": "https://evil.example"})


@pytest.mark.asyncio
async def test_cloud_proxy_and_sse_keep_cloud_out_of_local_history(monkeypatch):
    import koddi.dokky.router as routes
    from koddi.api import create_app
    from fastapi.testclient import TestClient

    seen = []

    def server(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer unified-token"
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                text='id: 1\nevent: message.delta\ndata: {"message_id":"msg_1","content":"Привет","offset":0}\n\nid: 2\nevent: turn.completed\ndata: {}\n\n',
                headers={"Content-Type": "text/event-stream"},
            )
        if request.method == "POST":
            assert request.headers["Idempotency-Key"] == "test-key"
            return httpx.Response(200, json={"run_id": "run_1"})
        return httpx.Response(
            200, json={"items": [{"id": "cloud_1", "title": "Серверный чат"}], "next_cursor": None}
        )

    client = DokkyClient(
        transport=httpx.MockTransport(server),
        store=MemoryStore(
            {"access_token": "unified-token", "refresh_token": "r", "expires_at": time.time() + 900}
        ),
    )
    monkeypatch.setattr(routes, "dokky", client)
    api = TestClient(create_app())
    assert api.get("/api/dokky/cloud/chats?mode=search").json()["items"][0]["id"] == "cloud_1"
    assert (
        api.post(
            "/api/dokky/cloud/chats/cloud_1/messages",
            json={"content": "Запрос"},
            headers={"Idempotency-Key": "test-key"},
        ).json()["run_id"]
        == "run_1"
    )
    assert "message.delta" in api.get("/api/dokky/cloud/runs/run_1/events").text
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert api.get("/api/dokky/cloud/other/admin").status_code == 404
    assert api.get("/api/dokky/cloud/runs/run_1/events?after=wrong").status_code == 422


@pytest.mark.asyncio
async def test_documented_inference_tool_calls_and_revocation(monkeypatch):
    import koddi.dokky.client as account
    from koddi.remote.client import RemoteAIClient

    storage.set_setting("dokky_user", {"id": "test-user"})

    store = MemoryStore(
        {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 900}
    )
    revoked = False

    def server(request):
        assert request.headers["Authorization"] == "Bearer a"
        if request.url.path == "/api/v1/coding/sessions":
            return httpx.Response(
                201,
                json={
                    "id": "cs_" + "a" * 24,
                    "reasoning_mode": "light",
                    "version": 1,
                    "history": {"length": 0, "hash": ""},
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        body = json.loads(request.content)
        assert body["store"] is False and body["model"] == "dokky-code"
        assert body["tools"][0]["function"]["name"] == "file__write"
        if revoked:
            chunks = [{"error": {"code": "session_revoked", "message": "Сессия отозвана"}}]
        else:
            chunks = [
                {"choices": [{"delta": {"role": "assistant", "content": "Создам файл."}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "file__write",
                                            "arguments": '{"path":"test.py","content":"print(1)"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                {
                    "choices": [],
                    "usage": {"completion_tokens": 10},
                    "dokky": {"history": {"length": 3, "hash": "test"}},
                },
            ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )

    client = RemoteAIClient(
        {"provider": "dokky", "base_url": API}, transport=httpx.MockTransport(server)
    )
    monkeypatch.setattr(
        account, "dokky", DokkyClient(store=store, transport=httpx.MockTransport(server))
    )
    options = {
        "model": "dokky-code",
        "messages": [{"role": "user", "content": "Создай файл"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "file.write", "parameters": {"type": "object"}},
            }
        ],
    }
    events = [event async for event in client.stream(**options)]
    assert events[-1]["tool_call"]["name"] == "file.write"
    assert events[-1]["tool_call"]["arguments"] == '{"path":"test.py","content":"print(1)"}'
    revoked = True
    with pytest.raises(DokkyError, match="Сессия отозвана"):
        _ = [event async for event in client.stream(**options)]
    assert not store.load()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [None, "broken", -1, 0])
async def test_invalid_token_expiry_is_not_persisted(expiry):
    store = MemoryStore()
    client = DokkyClient(
        store=store,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "access_token": "a",
                    "refresh_token": "r",
                    "token_type": "Bearer",
                    "expires_in": expiry,
                },
            )
        ),
    )
    with pytest.raises(DokkyError):
        await client.tokens({"grant_type": "authorization_code"})
    assert not store.load()


def test_server_error_message_survives_proxy_without_session_loss():
    store = MemoryStore({"access_token": "a"})
    client = DokkyClient(store=store)
    with pytest.raises(DokkyError) as error:
        client.check(
            httpx.Response(
                402,
                json={"error": {"code": "quota_exceeded", "message": "Лимит пятичасового окна"}},
            )
        )
    assert str(error.value) == "Лимит пятичасового окна"
    assert error.value.code == "quota_exceeded" and store.load()


@pytest.mark.asyncio
async def test_failed_new_account_bootstrap_does_not_replace_previous_credentials():
    old = {
        "access_token": "account-a",
        "refresh_token": "refresh-a",
        "expires_at": time.time() + 900,
    }
    store = MemoryStore(old)
    storage.set_setting("dokky_user", {"id": "account-a"})

    def handler(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "account-b",
                    "refresh_token": "refresh-b",
                    "expires_in": 900,
                    "token_type": "Bearer",
                },
            )
        assert request.headers["Authorization"] == "Bearer account-b"
        return httpx.Response(
            503, json={"error": {"code": "upstream_unavailable", "message": "Сервис недоступен"}}
        )

    client = DokkyClient(transport=with_par(handler), store=store)
    await client.begin("http://127.0.0.1:12345/api/dokky/auth/callback")
    with pytest.raises(DokkyError):
        await client.callback(code="code", state=client.pending["state"], issuer=ORIGIN)
    assert store.load() == old
    assert storage.get_setting("dokky_user") == {"id": "account-a"}


@pytest.mark.asyncio
async def test_failed_credential_write_never_keeps_mixed_account_state():
    class FailingStore(MemoryStore):
        def save(self, value):
            raise OSError("Запись недоступна")

    store = FailingStore(
        {"access_token": "old", "refresh_token": "old-refresh", "expires_at": time.time() + 900}
    )
    storage.set_setting("dokky_user", {"id": "account-a"})

    def handler(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "new",
                    "refresh_token": "new-refresh",
                    "expires_in": 900,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(200, json={"user": {"id": "account-b"}, "models": []})

    client = DokkyClient(store=store, transport=with_par(handler))
    await client.begin("http://127.0.0.1:12345/api/dokky/auth/callback")
    with pytest.raises(DokkyError, match="сохранить вход"):
        await client.callback(code="code", state=client.pending["state"], issuer=ORIGIN)
    assert not store.load()
    assert storage.get_setting("dokky_user") == {}


REDIRECT = "http://127.0.0.1:8878/api/dokky/auth/callback"


@pytest.mark.asyncio
async def test_par_error_is_shown_and_no_attempt_is_started():
    def server(request):
        return httpx.Response(429, json={"error": "rate_limited"})

    client = DokkyClient(transport=httpx.MockTransport(server), store=MemoryStore())
    with pytest.raises(DokkyError) as error:
        await client.begin(REDIRECT)
    assert error.value.status == 429 and "Слишком много попыток" in str(error.value)
    assert client.pending is None and client.login_status()["status"] == "error"


@pytest.mark.asyncio
async def test_login_attempt_expires_after_ten_minutes_and_late_callback_is_404(monkeypatch):
    client = DokkyClient(transport=with_par(lambda request: httpx.Response(500)), store=MemoryStore())
    flow = await client.begin(REDIRECT)
    assert flow["expires_in"] == 600
    state = client.pending["state"]
    client.pending["expires"] = time.time() - 1
    status = client.login_status()
    assert status["status"] == "expired" and status["message"] == "Время на вход вышло" and not status["pending"]
    with pytest.raises(DokkyError) as late:
        await client.callback(code="code", state=state, issuer=ORIGIN)
    assert late.value.status == 404


@pytest.mark.asyncio
async def test_new_attempt_has_new_state_and_cancel_closes_the_listener():
    seen = []
    client = DokkyClient(transport=with_par(lambda request: httpx.Response(500), seen), store=MemoryStore())
    await client.begin(REDIRECT)
    first = client.pending["state"]
    await client.begin(REDIRECT)
    second = client.pending["state"]
    assert first != second and seen[0]["state"] != seen[1]["state"]
    with pytest.raises(DokkyError):
        await client.callback(code="code", state=first, issuer=ORIGIN)
    client.cancel_login()
    assert client.pending is None
    with pytest.raises(DokkyError) as closed:
        await client.callback(code="code", state=second, issuer=ORIGIN)
    assert closed.value.status == 404


@pytest.mark.asyncio
async def test_denied_and_invalid_grant_close_the_attempt_with_clear_messages():
    def server(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = DokkyClient(transport=with_par(server), store=MemoryStore())
    await client.begin(REDIRECT)
    with pytest.raises(DokkyError) as denied:
        await client.callback(code="", state=client.pending["state"], issuer=ORIGIN, error="access_denied")
    assert denied.value.code == "access_denied" and client.pending is None
    assert client.login_status()["status"] == "denied"
    await client.begin(REDIRECT)
    with pytest.raises(DokkyError) as invalid:
        await client.callback(code="spent", state=client.pending["state"], issuer=ORIGIN)
    assert str(invalid.value) == "Попытка входа закончилась, войдите снова" and client.pending is None
    assert client.login_status()["status"] == "invalid"


@pytest.mark.asyncio
async def test_legacy_session_refreshes_under_its_own_client_id():
    seen = []

    def server(request):
        seen.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "next", "expires_in": 900, "token_type": "Bearer"})

    store = MemoryStore()
    store.save({"access_token": "old", "refresh_token": "r", "expires_at": 0})
    client = DokkyClient(transport=httpx.MockTransport(server), store=store)
    assert await client.access_token() == "new"
    assert seen[0]["client_id"] == ["koddi-desktop"] and store.load()["client_id"] == "koddi-desktop"


def test_callback_route_answers_404_to_strangers_and_redirects_only_after_login(monkeypatch):
    import koddi.dokky.router as routes
    from fastapi.testclient import TestClient
    from koddi.api import create_app

    def server(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 900, "token_type": "Bearer"})
        return httpx.Response(200, json={"user": {"id": "user_1", "display_name": "Тест"}, "models": []})

    client = DokkyClient(transport=with_par(server), store=MemoryStore())
    monkeypatch.setattr(routes, "dokky", client)
    web = TestClient(create_app())
    callback = "/api/dokky/auth/callback"

    asyncio.run(client.begin(REDIRECT))
    state = client.pending["state"]
    stranger = web.get(callback, params={"code": "c", "state": "bad", "iss": ORIGIN}, follow_redirects=False)
    assert stranger.status_code == 404 and "location" not in stranger.headers
    # чужой запрос не закрывает настоящую попытку
    assert web.get("/api/dokky/auth/status").json()["pending"] is True
    done = web.get(callback, params={"code": "c", "state": state, "iss": ORIGIN}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == ORIGIN + "/desktop/complete"
    assert web.get(callback, params={"code": "c", "state": state, "iss": ORIGIN}, follow_redirects=False).status_code == 404

    # отказ закрывает попытку и не отправляет на страницу успеха
    asyncio.run(client.begin(REDIRECT))
    denied = web.get(callback, params={"state": client.pending["state"], "iss": ORIGIN, "error": "access_denied"}, follow_redirects=False)
    assert denied.status_code == 400 and "location" not in denied.headers
    assert web.get("/api/dokky/auth/status").json()["status"] == "denied"

    asyncio.run(client.begin(REDIRECT))
    assert web.post("/api/dokky/auth/cancel").json() == {"ok": True}
    assert web.get("/api/dokky/auth/status").json() == {"status": "idle", "message": "", "pending": False}
