"""web.search/web.fetch агента: сервер Докки при входе, локально без входа, при ошибке и для непубличных адресов."""

import asyncio
from types import SimpleNamespace

import pytest

from koddi.dokky import access
from koddi.dokky import web
from koddi.dokky.client import DokkyError


class Response:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


@pytest.fixture
def server(monkeypatch):
    state = SimpleNamespace(credentials={"access_token": "t"}, calls=[], reply={}, error=None,
                            allowed=True, local=[], addresses=("93.184.216.34",))

    async def request(method, path, *, json=None, **kwargs):
        state.calls.append((method, path, json))
        if state.error:
            raise state.error
        return Response(state.reply)

    async def local_search(query, limit):
        state.local.append(("search", query, limit))
        return {"ok": True, "query": query, "results": [], "source": "DuckDuckGo"}

    async def local_fetch(url, max_chars=120_000, *, allow_private=False):
        state.local.append(("fetch", url, allow_private))
        return {"ok": True, "url": url, "content": "local"}

    async def developer_access(**kwargs):
        return {"allowed": state.allowed}

    async def resolve(url, **kwargs):
        return state.addresses

    monkeypatch.setattr(web, "dokky", SimpleNamespace(store=SimpleNamespace(load=lambda: state.credentials),
                                                      request=request))
    monkeypatch.setattr(web, "local_search", local_search)
    monkeypatch.setattr(web, "local_fetch", local_fetch)
    monkeypatch.setattr(web, "resolve_target", resolve)
    monkeypatch.setattr(access, "developer_access", developer_access)
    return state


def test_search_goes_through_server_when_signed_in(server):
    server.reply = {"results": [{"title": "T", "url": "https://example.org/", "snippet": "S", "date": "2026",
                                 "extra": "x"}, {"title": "без ссылки"}]}
    result = asyncio.run(web.web_search("вопрос", 50))
    assert server.calls == [("POST", "/coding/web/search", {"query": "вопрос", "limit": 20})]
    assert result == {"ok": True, "query": "вопрос", "source": "Докки",
                      "results": [{"title": "T", "url": "https://example.org/", "snippet": "S", "date": "2026"}]}
    assert server.local == []


@pytest.mark.parametrize("case", ["signed_out", "denied", "error", "empty"])
def test_search_falls_back_to_local(server, case):
    if case == "signed_out":
        server.credentials = {}
    elif case == "denied":
        server.allowed = False
    elif case == "error":
        server.error = DokkyError("нет", 402, "quota_exceeded")
    else:
        server.reply = {"results": []}
    assert asyncio.run(web.web_search("вопрос", 5))["source"] == "DuckDuckGo"
    assert server.local == [("search", "вопрос", 5)]
    if case in {"signed_out", "denied"}:
        assert server.calls == []


def test_fetch_reads_public_https_on_server(server):
    server.reply = {"title": "Заголовок", "content": "# " + "т" * 100, "truncated": False}
    result = asyncio.run(web.web_fetch("https://example.org/p", 10))
    assert server.calls == [("POST", "/coding/web/fetch", {"url": "https://example.org/p"})]
    assert result["content"] == ("# " + "т" * 100)[:10] and result["truncated"] and result["source"] == "Докки"
    assert server.local == []


@pytest.mark.parametrize("url,addresses", [
    ("http://example.org/p", ("93.184.216.34",)),
    ("https://127.0.0.1/p", ("127.0.0.1",)),
    ("https://intranet.example/p", ("198.18.0.7",)),
])
def test_non_public_addresses_never_leave_the_computer(server, monkeypatch, url, addresses):
    server.addresses = addresses
    if addresses == ("127.0.0.1",):
        async def refuse(*args, **kwargs):
            raise web.NetworkAccessError("non-public network address")
        monkeypatch.setattr(web, "resolve_target", refuse)
    assert asyncio.run(web.web_fetch(url, allow_private=True))["content"] == "local"
    assert server.calls == []
    assert server.local == [("fetch", url, True)]


def test_fetch_error_falls_back_to_local(server):
    server.error = DokkyError("нет текста", 404, "not_found")
    assert asyncio.run(web.web_fetch("https://example.org/p"))["content"] == "local"
    assert len(server.calls) == 1
