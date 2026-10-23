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
