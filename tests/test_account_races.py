import asyncio
import time
from urllib.parse import parse_qs

import httpx
import pytest

import koddi.storage.db as storage
from koddi.dokky.client import API, DokkyClient, DokkyError


class Store:
    def __init__(self, token="account-a"):
        self.value = {
            "access_token": token,
            "refresh_token": token + "-refresh",
            "expires_at": time.time() + 900,
        }

    def load(self):
        return self.value.copy()

    def save(self, value):
        self.value = value.copy()

    def clear(self):
        self.value = {}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "accounts.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "a"})


def test_old_revocation_cannot_clear_current_token():
    store = Store("account-b")
    client = DokkyClient(store=store)
    request = httpx.Request("GET", API + "/events", headers={"Authorization": "Bearer account-a"})
    with pytest.raises(DokkyError) as error:
        client.check(
            httpx.Response(401, request=request, json={"error": {"code": "session_revoked"}})
        )
    assert error.value.code == "session_changed"
    assert store.load()["access_token"] == "account-b"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401])
async def test_old_request_is_not_returned_or_retried_under_new_account(status):
    store = Store()
    calls = []

    def handler(request):
        calls.append(request)
        storage.set_setting("dokky_user", {"id": "b"})
        store.value["access_token"] = "account-b"
        return httpx.Response(status, json={"private": "a", "error": {"code": "invalid_token"}})

    client = DokkyClient(store=store, transport=httpx.MockTransport(handler))
    with pytest.raises(DokkyError) as error:
        await client.request("GET", "/chats")
    assert error.value.code == "session_changed" and len(calls) == 1
    assert store.load()["access_token"] == "account-b"


@pytest.mark.asyncio
async def test_old_logout_revokes_only_expected_session_and_keeps_new_history():
    store = Store("account-b")
    with storage.db() as conn:
        conn.execute("INSERT INTO chats(title) VALUES ('История B')")

    def handler(request):
        assert parse_qs(request.content.decode())["token"] == ["account-a-refresh"]
        return httpx.Response(200, json={"ok": True})

    client = DokkyClient(store=store, transport=httpx.MockTransport(handler))
    assert not await client.logout(
        expected={"access_token": "account-a", "refresh_token": "account-a-refresh"}
    )
    assert store.load()["access_token"] == "account-b"
    with storage.db() as conn:
        assert conn.execute("SELECT count(*) FROM chats").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_logout_cleans_metadata_when_server_already_revoked_token():
    store = Store()

    def handler(request):
        store.clear()
        return httpx.Response(200, json={"ok": True})

    client = DokkyClient(store=store, transport=httpx.MockTransport(handler))
    assert await client.logout()
    assert storage.get_setting("dokky_user") == {}


@pytest.mark.asyncio
async def test_stale_event_stream_cannot_revoke_new_account(monkeypatch):
    import koddi.dokky.monitor as monitor

    store = Store()
    revoked = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            store.value["access_token"] = "account-b"
            yield b"event: auth.revoked\ndata: {}\n\n"

    client = DokkyClient(
        store=store,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())),
    )

    async def on_revoked():
        revoked.append(True)

    async def finish(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor.asyncio, "sleep", finish)
    with pytest.raises(asyncio.CancelledError):
        await monitor.watch_account(client, on_revoked)
    assert not revoked and store.load()["access_token"] == "account-b"


@pytest.mark.asyncio
async def test_cleanup_rechecks_identity_after_cancelling_old_turn(monkeypatch):
    import koddi.dokky.monitor as monitor
    from koddi.agents import agent_runs

    store = Store()
    client = DokkyClient(store=store)
    monkeypatch.setattr(monitor, "dokky", client)
    monkeypatch.setattr(agent_runs, "active_ids", lambda: [11])

    async def cancel(_chat):
        store.value["access_token"] = "account-b"
        with storage.db() as conn:
            conn.execute("INSERT INTO chats(title) VALUES ('Новая история B')")

    monkeypatch.setattr(agent_runs, "cancel", cancel)
    assert not await monitor.stop_local_runs(expected_session={"access_token": "account-a"})
    with storage.db() as conn:
        assert conn.execute("SELECT count(*) FROM chats").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_account_activation_does_not_emit_logout_event(monkeypatch):
    from koddi.events import event_bus

    events = []

    async def publish(kind, data):
        events.append(kind)

    monkeypatch.setattr(event_bus, "publish", publish)
    client = DokkyClient(
        store=Store(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"user": {"id": "b"}, "models": []})
        ),
    )
    await client.bootstrap(token="account-b")
    assert "dokky.auth_revoked" not in events
    assert storage.get_setting("dokky_user") == {"id": "b"}
