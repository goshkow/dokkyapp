from fastapi import FastAPI
from fastapi.testclient import TestClient

from koddi.desktop.transport import DesktopAccess, DesktopTransportGuard


def test_desktop_requires_process_key_for_ui_api_and_websocket():
    access = DesktopAccess()
    key = access.enable()
    app = FastAPI()
    app.add_middleware(DesktopTransportGuard, access=access)

    @app.get("/{path:path}")
    def get(path):
        return {"ok": True}

    client = TestClient(app)
    for path in ("/ui/", "/api/health", "/docs", "/openapi.json", "/api/dokky/session"):
        denied = client.get(path)
        assert denied.status_code == 404 and key not in denied.text
        assert client.get(path, headers={"X-Koddi-Desktop-Session": key}).status_code == 200
        assert client.get(path, headers={"X-Koddi-Desktop-Session": "wrong"}).status_code == 404
    assert client.get("/api/dokky/auth/callback").status_code == 200
    assert client.post("/api/dokky/auth/callback").status_code == 404
    assert client.get("/api/dokky/auth/callback/extra").status_code == 404


def test_development_preview_is_explicitly_not_desktop():
    app = FastAPI()
    app.add_middleware(DesktopTransportGuard, access=DesktopAccess())

    @app.get("/api/health")
    def get():
        return {"ok": True}

    assert TestClient(app).get("/api/health").json()["ok"]


def test_session_cookie_of_native_view_is_accepted_only_with_the_right_value():
    access = DesktopAccess()
    key = access.enable()
    app = FastAPI()
    app.add_middleware(DesktopTransportGuard, access=access)

    @app.get("/{path:path}")
    def get(path):
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/health", headers={"Cookie": f"koddi_desktop_session={key}"}).status_code == 200
    assert client.get("/api/health", headers={"Cookie": f"other=1; koddi_desktop_session={key}"}).status_code == 200
    for bad in ("koddi_desktop_session=wrong", "koddi_desktop_session=", "other=" + key, "\x00bad;;;", f"koddi_desktop_session{key}"):
        assert client.get("/api/health", headers={"Cookie": bad}).status_code == 404


def test_foreign_origin_is_refused_even_with_the_key():
    access = DesktopAccess()
    key = access.enable()
    access.origin = "http://127.0.0.1:8765"
    app = FastAPI()
    app.add_middleware(DesktopTransportGuard, access=access)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    def any_path(path):
        return {"ok": True}

    @app.websocket("/api/events/ws")
    async def events(socket):
        await socket.accept()
        await socket.close()

    client = TestClient(app)
    own = {"X-Koddi-Desktop-Session": key, "Origin": "http://127.0.0.1:8765"}
    assert client.post("/api/chats", headers=own).status_code == 200
    cookie = {"Cookie": f"koddi_desktop_session={key}"}
    for site in ("same-origin", "none"):
        assert client.get("/api/health", headers={**cookie, "Sec-Fetch-Site": site}).status_code == 200
    # картинка или скрипт со страницы на другом порту: без Origin, но с кукой окна
    for site in ("same-site", "cross-site"):
        assert client.get("/api/health", headers={**cookie, "Sec-Fetch-Site": site}).status_code == 404
    assert client.get("/api/dokky/auth/callback", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    for origin in ("https://dokky.goshkow.com", "null", "http://127.0.0.1:9999", "http://localhost:8765"):
        headers = {"X-Koddi-Desktop-Session": key, "Origin": origin}
        assert client.post("/api/chats", headers=headers).status_code == 404
        assert client.get("/api/health", headers=headers).status_code == 404
        try:
            with client.websocket_connect("/api/events/ws", headers=headers):
                raise AssertionError("websocket from a foreign origin must be closed")
        except Exception as exc:  # noqa: BLE001
            assert "1008" in repr(exc) or getattr(exc, "code", None) == 1008
