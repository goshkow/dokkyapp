from __future__ import annotations

import secrets
from http.cookies import CookieError, SimpleCookie


class DesktopAccess:
    def __init__(self):
        self.key: str | None = None
        self.origin: str | None = None

    def enable(self) -> str:
        if self.key is None:
            self.key = secrets.token_urlsafe(48)
        return self.key


desktop_access = DesktopAccess()
HEADER = b"x-koddi-desktop-session"
COOKIE = "koddi_desktop_session"


def _cookie_value(raw: bytes) -> bytes:
    """Сессия нативного веб-вида macOS приходит HttpOnly-кукой: заголовок туда подставить нельзя."""
    try:
        jar = SimpleCookie(raw.decode("latin-1"))
    except CookieError:
        return b""
    morsel = jar.get(COOKIE)
    return morsel.value.encode("latin-1") if morsel is not None else b""


class DesktopTransportGuard:
    def __init__(self, app, access=None):
        self.app = app
        self.access = access or desktop_access

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or self.access.key is None:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        value = headers.get(HEADER, b"") or _cookie_value(headers.get(b"cookie", b""))
        expected = self.access.key.encode("ascii")
        callback = (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/api/dokky/auth/callback"
        )
        # Origin шлют POST, WebSocket и запросы из чужих страниц: интерфейс приложения - только со своего адреса,
        # даже если ключ к запросу как-то приложился (iframe сайта внутри окна, у WebSocket нет CORS)
        sender = headers.get(b"origin")
        foreign = sender is not None and self.access.origin is not None and sender != self.access.origin.encode("ascii")
        # GET картинкой или скриптом с другой страницы идёт без Origin, но с кукой окна macOS: такие запросы выдаёт
        # Sec-Fetch-Site (другой порт того же 127.0.0.1 - same-site, тоже чужой)
        foreign = foreign or headers.get(b"sec-fetch-site", b"same-origin") not in {b"same-origin", b"none"}
        if callback or (not foreign and secrets.compare_digest(value, expected)):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": "Недоступно".encode()})
