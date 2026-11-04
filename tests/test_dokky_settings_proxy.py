import httpx
import pytest
from fastapi.testclient import TestClient

from koddi.api.app import create_app
from koddi.dokky.client import ORIGIN, dokky


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path))
    sent = []

    async def request(method, path, **kwargs):
        sent.append((method, path, kwargs.get("json")))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(dokky, "request", request)
    return TestClient(create_app()), sent


@pytest.mark.parametrize("method,path", [
    ("GET", "memory"), ("DELETE", "memory"), ("POST", "memory/import/parse"), ("POST", "memory/import/apply"),
    ("GET", "voices"), ("PUT", "voices/choice"), ("GET", "sync"), ("PUT", "sync"),
])
def test_settings_routes_reach_the_site(client, method, path):
    app, sent = client
    assert app.request(method, f"/api/dokky/cloud/{path}", json={} if method in {"POST", "PUT"} else None).status_code == 200
    assert sent[-1][:2] == (method, "/" + path)


@pytest.mark.parametrize("method,path", [
    ("DELETE", "voices"), ("PUT", "memory"), ("GET", "memory/import/parse"), ("POST", "sync"), ("GET", "voices/admin/module"),
])
def test_other_routes_stay_closed(client, method, path):
    app, sent = client
    assert app.request(method, f"/api/dokky/cloud/{path}", json={} if method in {"POST", "PUT"} else None).status_code == 404
    assert sent == []


def test_voice_assets_only_by_exact_name(client, monkeypatch):
    app, _ = client
    asked = []

    class Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            asked.append(url)
            return httpx.Response(200, content=b"ID3")

    monkeypatch.setattr(dokky, "http", Http)
    answer = app.get("/api/dokky/voice-assets/abc-1.mp3")
    assert answer.status_code == 200 and answer.content == b"ID3" and answer.headers["content-type"] == "audio/mpeg"
    assert asked == [f"{ORIGIN}/assets/voices/abc-1.mp3"]
    for bad in ("x.svg", "a.b.mp3", "%2e%2e%2fsecret.mp3", "x.mp3%00.webp"):
        assert app.get(f"/api/dokky/voice-assets/{bad}").status_code == 404, bad
    assert len(asked) == 1
