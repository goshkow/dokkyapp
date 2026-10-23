import httpx
import pytest
from fastapi.testclient import TestClient

import koddi.dokky.router as router
import koddi.storage.db as storage
from koddi.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "media.sqlite3")
    return TestClient(create_app())


def stub(monkeypatch, *, status=200, kind="image/png", body=b"\x89PNG"):
    seen = []

    async def fetch(url, **kwargs):
        seen.append((url, kwargs))
        return httpx.Response(status, headers={"content-type": kind}, content=body)

    monkeypatch.setattr(router, "fetch_response", fetch)
    return seen


def test_public_image_is_proxied_with_safe_headers(client, monkeypatch):
    seen = stub(monkeypatch)
    response = client.get("/api/dokky/media", params={"url": "https://example.org/a.png"})
    assert response.status_code == 200 and response.content == b"\x89PNG"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert seen[0][1]["allow_private"] is False


@pytest.mark.parametrize("kind", ["image/svg+xml", "text/html", "application/octet-stream"])
def test_only_raster_images_are_returned(client, monkeypatch, kind):
    stub(monkeypatch, kind=kind)
    assert client.get("/api/dokky/media", params={"url": "https://example.org/a"}).status_code == 415


def test_error_status_is_not_returned_as_image(client, monkeypatch):
    stub(monkeypatch, status=404)
    assert client.get("/api/dokky/media", params={"url": "https://example.org/a.png"}).status_code == 415


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8878/api/health", "http://localhost/a.png", "http://169.254.169.254/latest",
    "http://[::1]/a.png", "file:///etc/passwd", "https://user:pw@example.org/a.png", "ftp://example.org/a.png",
])
def test_private_and_unsafe_addresses_are_rejected_before_any_request(client, url):
    response = client.get("/api/dokky/media", params={"url": url})
    assert response.status_code == 422
