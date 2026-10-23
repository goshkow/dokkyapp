import time

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from koddi.dokky.client import DokkyClient
from test_dokky_client import MemoryStore


class FileStream(httpx.AsyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in (b"first", b"second"):
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def request(headers=()):
    return Request({"type": "http", "method": "GET", "headers": list(headers)})


@pytest.mark.asyncio
async def test_file_is_lazy_range_preserved_and_cancel_closes_upstream(monkeypatch):
    import koddi.dokky.router as routes

    stream = FileStream()

    def server(req):
        assert req.headers["Authorization"] == "Bearer file-token"
        assert req.headers["Range"] == "bytes=0-10"
        return httpx.Response(
            206,
            stream=stream,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 0-10/100",
                "Content-Length": "11",
                "Content-Disposition": 'attachment; filename="example.bin"',
            },
        )

    client = DokkyClient(
        transport=httpx.MockTransport(server),
        store=MemoryStore(
            {
                "access_token": "file-token",
                "expires_at": time.time() + 900,
            }
        ),
    )
    monkeypatch.setattr(routes, "dokky", client)
    response = await routes.file_content("file_example", request([(b"range", b"bytes=0-10")]))
    assert stream.reads == 0
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-10/100"
    assert response.headers["cache-control"] == "private, no-store"
    assert await anext(response.body_iterator) == b"first"
    assert stream.reads == 1
    await response.body_iterator.aclose()
    assert stream.closed


@pytest.mark.asyncio
async def test_file_invalid_id_never_reaches_upstream():
    import koddi.dokky.router as routes

    with pytest.raises(HTTPException) as exc:
        await routes.file_content("../secret", request())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_file_revocation_is_not_returned_as_a_download(monkeypatch):
    import koddi.dokky.router as routes

    store = MemoryStore({"access_token": "revoked", "expires_at": time.time() + 900})
    client = DokkyClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                401, json={"error": {"code": "token_revoked", "message": "Session revoked"}}
            )
        ),
        store=store,
    )
    monkeypatch.setattr(routes, "dokky", client)
    with pytest.raises(HTTPException) as exc:
        await routes.file_content("file_example", request())
    assert exc.value.status_code == 401
