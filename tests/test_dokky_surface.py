import httpx
import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.dokky.client import ORIGIN, DokkyError, dokky
from koddi.remote_control import remote_control


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "surface.sqlite3")
    storage.init_db()


@pytest.fixture
def proxy(monkeypatch):
    """Сервер приложения; запросы к Докки записываются, ответ задаёт тест."""
    sent = []
    state = {"answer": httpx.Response(200, json={}), "signed_in": True}

    async def request(method, path, **kwargs):
        sent.append((method, path))
        answer = state["answer"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(dokky, "request", request)
    monkeypatch.setattr(dokky.store, "load", lambda: {"access_token": "a"} if state["signed_in"] else {})
    return TestClient(create_app()), sent, state


def test_surface_is_offered_only_when_the_site_supports_it(proxy):
    client, sent, state = proxy
    remote_control.config()
    with storage.db() as conn:
        conn.execute("UPDATE remote_control SET host_id='host-1' WHERE id=1")
    state["answer"] = httpx.Response(200, json={"protocol": 1, "path": "/app-surface", "release": "r1"})
    assert client.get("/api/dokky/surface").json() == {
        "available": True, "url": ORIGIN + "/app-surface", "origin": ORIGIN, "release": "r1", "host_id": "host-1",
    }
    assert sent == [("GET", "/app/surface")]
    # сервер без поддержки или с другим протоколом - приложение остаётся на своём чате
    state["answer"] = DokkyError("нет", 404, "not_found")
    assert client.get("/api/dokky/surface").json() == {"available": False}
    for body in ({"protocol": 2, "path": "/app-surface"}, {"protocol": 1, "path": "//evil.example/x"}):
        state["answer"] = httpx.Response(200, json=body)
        assert client.get("/api/dokky/surface").json() == {"available": False}
    sent.clear()
    state["signed_in"] = False
    assert client.get("/api/dokky/surface").json() == {"available": False} and sent == []


def test_page_token_comes_from_the_site_and_is_checked(proxy):
    client, sent, state = proxy
    state["answer"] = httpx.Response(200, json={"token": "dks1.abc", "expires_in": 900, "path": "/app-surface"})
    assert client.post("/api/dokky/surface/session").json() == {"token": "dks1.abc", "expires_in": 900}
    assert sent == [("POST", "/app/surface/session")]
    state["answer"] = httpx.Response(200, json={"token": "dks1.abc", "expires_in": 10 ** 9})
    assert client.post("/api/dokky/surface/session").json()["expires_in"] == 900
    for token in ("dka_bearer", "", None, "dks1." + "x" * 3000):
        state["answer"] = httpx.Response(200, json={"token": token})
        answer = client.post("/api/dokky/surface/session")
        assert answer.status_code == 502 and answer.json()["detail"]["code"] == "bad_surface_token"
    state["answer"] = DokkyError("Сессия завершена", 401, "session_revoked")
    assert client.post("/api/dokky/surface/session").status_code == 401
