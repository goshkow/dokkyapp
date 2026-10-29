from __future__ import annotations

import asyncio
import socket
import ssl
import time
from threading import Event
from dataclasses import replace
from types import SimpleNamespace

import httpcore
import pytest

from koddi.browser import service
from koddi.policy import network


PUBLIC_IP = "93.184.216.34"
SYSTEM_LOOKUP = network._lookup


def dns_records(*addresses):
    return [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM,
         socket.IPPROTO_TCP, "", (ip, 443))
        for ip in addresses
    ]


class WireStream(httpcore.AsyncMockStream):
    def __init__(self, chunks, *, stall=False, invalid_certificate=False):
        super().__init__(chunks)
        self.sent = bytearray()
        self.tls = []
        self.reads = []
        self.closed = False
        self.stall = stall
        self.invalid_certificate = invalid_certificate

    async def write(self, buffer, timeout=None):
        self.sent.extend(buffer)

    async def read(self, max_bytes, timeout=None):
        self.reads.append((max_bytes, timeout))
        if self.stall:
            await asyncio.sleep(10)
        return await super().read(max_bytes, timeout)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((ssl_context, server_hostname))
        if self.invalid_certificate:
            raise httpcore.ConnectError("certificate verify failed")
        return self

    async def aclose(self):
        self.closed = True
        await super().aclose()


@pytest.fixture(autouse=True)
def wire(monkeypatch):
    state = SimpleNamespace(queue=[], connections=[], lookups=[], streams=[])

    async def lookup(host, port):
        state.lookups.append((host, port))
        return dns_records(PUBLIC_IP)

    async def connect(self, host, port, **kwargs):
        state.connections.append((host, port, kwargs))
        if not state.queue:
            raise AssertionError("unexpected network request")
        stream = state.queue.pop(0)
        state.streams.append(stream)
        return stream

    monkeypatch.setattr(network, "_lookup", lookup)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    return state


def reply(body=b"ok", status="200 OK", headers=b"", *, chunks=None):
    if chunks is None:
        chunks = [body]
        headers += f"Content-Length: {len(body)}\r\n".encode()
    return WireStream([f"HTTP/1.1 {status}\r\n".encode() + headers + b"\r\n", *chunks])


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "file:///C:/Windows/win.ini", "ftp://example.com/",
    "data:text/html,hello", "javascript:alert(1)", "ws://example.com/", "//example.com/",
    "http://user:secret@example.com/", "http://user@example.com/", "http://@example.com/",
    "http://example.com\\@127.0.0.1/", "http://example.com/\r\nx:1", " http://example.com/",
    "http://[fe80::1%25eth0]/", "http://example.com:0/", "http://example.com:99999/",
])
def test_invalid_urls_never_connect(url, wire):
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(service.web_fetch(url))
    assert not wire.connections
    assert not wire.lookups


@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.0.1", "192.168.1.1",
    "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255",
    "::1", "::", "fe80::1", "fc00::1", "fec0::1", "ff0e::1", "::ffff:127.0.0.1",
    "64:ff9b::7f00:1", "2002:7f00:1::", "2001:db8::1",
])
def test_non_public_literals_are_denied(address, wire):
    host = f"[{address}]" if ":" in address else address
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(network.fetch_response(f"http://{host}/"))
    assert not wire.connections


@pytest.mark.parametrize("addresses", [
    [PUBLIC_IP, "127.0.0.1"], ["::1", PUBLIC_IP], [], ["invalid"],
])
def test_every_dns_answer_must_be_public(monkeypatch, wire, addresses):
    async def lookup(*args):
        return dns_records(*addresses)

    monkeypatch.setattr(network, "_lookup", lookup)
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(network.fetch_response("https://example.com/"))
    assert not wire.connections


def test_dns_failure_and_deadline(monkeypatch, wire):
    async def failed(*args):
        raise socket.gaierror("private resolver detail")

    monkeypatch.setattr(network, "_lookup", failed)
    with pytest.raises(network.NetworkAccessError, match="DNS resolution"):
        asyncio.run(network.fetch_response("https://example.com/"))

    async def stalled(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(network, "_lookup", stalled)
    with pytest.raises(network.NetworkAccessError, match="DNS resolution"):
        asyncio.run(network.fetch_response(
            "https://example.com/", limits=replace(network.DEFAULT_LIMITS, dns_seconds=0.01),
        ))
    assert not wire.connections


def test_pinning_keeps_host_sni_and_certificate_verification(wire):
    stream = reply()
    wire.queue.append(stream)
    response = asyncio.run(network.fetch_response("https://example.com/path"))
    assert response.text == "ok"
    assert wire.connections[0][:2] == (PUBLIC_IP, 443)
    assert wire.lookups == [("example.com", 443)]
    assert b"Host: example.com\r\n" in stream.sent
    assert b"accept-encoding: identity\r\n" in stream.sent.lower()
    context, hostname = stream.tls[0]
    assert hostname == "example.com"
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert all(max_bytes <= 65536 and timeout == 5 for max_bytes, timeout in stream.reads)
    assert stream.closed


def test_certificate_error_is_not_retried_insecurely(wire):
    stream = WireStream([], invalid_certificate=True)
    wire.queue.append(stream)
    with pytest.raises(httpcore.ConnectError):
        asyncio.run(network.fetch_response("https://example.com/"))
    assert len(wire.connections) == 1
    assert stream.tls[0][0].verify_mode == ssl.CERT_REQUIRED
    assert stream.closed


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
    "file:///etc/passwd", "https://user:secret@example.com/", "ftp://example.com/",
])
def test_redirects_cannot_escape_boundary(wire, location):
    stream = reply(status="302 Found", headers=f"Location: {location}\r\n".encode())
    wire.queue.append(stream)
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(network.fetch_response("https://example.com/"))
    assert len(wire.connections) == 1
    assert stream.closed


def test_redirect_re_resolves_and_rejects_rebinding(monkeypatch, wire):
    answers = iter([PUBLIC_IP, "127.0.0.1"])

    async def lookup(*args):
        return dns_records(next(answers))

    monkeypatch.setattr(network, "_lookup", lookup)
    wire.queue.append(reply(status="302 Found", headers=b"Location: /next\r\n"))
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(network.fetch_response("https://example.com/"))
    assert len(wire.connections) == 1


def test_redirect_count_and_cumulative_bytes(wire):
    for _ in range(3):
        wire.queue.append(reply(status="302 Found", headers=b"Location: /next\r\n"))
    with pytest.raises(network.NetworkAccessError, match="redirect limit"):
        asyncio.run(network.fetch_response(
            "https://example.com/", limits=replace(network.DEFAULT_LIMITS, max_redirects=2),
        ))
    assert len(wire.connections) == 3
    wire.queue.extend([
        reply(b"123", status="302 Found", headers=b"Location: /next\r\n"), reply(b"456"),
    ])
    with pytest.raises(network.NetworkAccessError, match="byte limit"):
        asyncio.run(network.fetch_response(
            "https://example.com/", limits=replace(network.DEFAULT_LIMITS, max_bytes=5),
        ))
    assert all(stream.closed for stream in wire.streams)


@pytest.mark.parametrize("headers,chunks", [
    (b"Content-Length: 9999999\r\n", []),
    (b"Content-Length: 11\r\n", [b"01234567890"]),
    (b"Transfer-Encoding: chunked\r\n", [b"6\r\n123456\r\n", b"6\r\n789012\r\n", b"0\r\n\r\n"]),
    (b"", [b"12345", b"678901"]),
    (b"Content-Encoding: gzip\r\n", [b"compressed"]),
])
def test_stream_bounds_and_compression_fail_closed(wire, headers, chunks):
    stream = reply(headers=headers, chunks=chunks)
    wire.queue.append(stream)
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(network.fetch_response(
            "https://example.com/", limits=replace(network.DEFAULT_LIMITS, max_bytes=10),
        ))
    assert stream.closed


def test_total_deadline_cleans_up_stream(wire):
    stream = WireStream([], stall=True)
    wire.queue.append(stream)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(network.fetch_response(
            "https://example.com/", limits=replace(network.DEFAULT_LIMITS, total_seconds=0.01),
        ))
    assert stream.closed
    assert time.monotonic() - started < 2


def test_read_deadline_cleans_up_stream(wire):
    stream = WireStream([], stall=True)
    wire.queue.append(stream)
    started = time.monotonic()
    with pytest.raises(httpcore.ReadTimeout):
        asyncio.run(network.fetch_response(
            "https://example.com/", limits=replace(network.DEFAULT_LIMITS, read_seconds=0.01),
        ))
    assert stream.closed
    assert time.monotonic() - started < 2


def test_system_dns_timeout_does_not_block_event_loop_shutdown(monkeypatch):
    release = Event()
    finished = Event()
    entered = Event()

    def lookup(*args):
        entered.set()
        try:
            release.wait(2)
            return dns_records(PUBLIC_IP)
        finally:
            finished.set()

    monkeypatch.setattr(network, "_lookup", SYSTEM_LOOKUP)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    started = time.monotonic()
    try:
        with pytest.raises(network.NetworkAccessError, match="DNS resolution"):
            asyncio.run(network.fetch_response(
                "https://example.com/", limits=replace(network.DEFAULT_LIMITS, dns_seconds=0.01),
            ))
        assert time.monotonic() - started < 1
    finally:
        release.set()
        if entered.is_set():
            assert finished.wait(2)


def test_private_access_is_an_explicit_internal_option(wire):
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(service.web_fetch("http://127.0.0.1/"))
    wire.queue.append(reply(b"<title>dev</title>local"))
    result = asyncio.run(service.web_fetch("http://127.0.0.1/", allow_private=True))
    assert result["title"] == "dev"
    assert wire.connections[0][:2] == ("127.0.0.1", 80)


def test_model_args_cannot_enable_private_access(monkeypatch):
    import koddi.tools.runner as runner_module

    calls = []

    async def fetch(url, *, allow_private=False):
        calls.append(allow_private)
        return {"ok": True}

    def open_page(url, project_id=None, *, allow_private=False, **kwargs):
        calls.append(allow_private)
        return {"ok": True}

    monkeypatch.setattr(runner_module, "web_fetch", fetch)
    monkeypatch.setattr(runner_module, "browser_open", open_page)
    runner = runner_module.ToolRunner(workspace=None)
    args = {"url": "http://127.0.0.1/", "allow_private": True}
    assert runner._dispatch("web.fetch", args)["ok"]
    with pytest.raises(runner_module.ToolError, match="localhost"):
        runner._dispatch("browser.open", args)
    assert runner._dispatch("browser.open", {**args, "url": "https://example.com/"})["ok"]
    assert calls == [False, False]


def test_public_redirects_keep_pins_and_drop_cross_origin_cookies(wire, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    first = reply(status="302 Found", headers=b"Location: https://other.example/target\r\n")
    second = reply(b"done")
    wire.queue.extend([first, second])
    response = asyncio.run(network.fetch_response(
        "https://example.com/", headers={"Cookie": "session=private", "Host": "127.0.0.1"},
    ))
    assert str(response.url) == "https://other.example/target"
    assert b"cookie: session=private\r\n" in first.sent.lower()
    assert b"cookie:" not in second.sent.lower()
    assert b"host: other.example\r\n" in second.sent.lower()
    assert all(host == PUBLIC_IP for host, _, _ in wire.connections)


def test_browser_byte_budget_is_applied_before_reading(browser, wire, monkeypatch):
    context, _ = browser
    wire.queue.append(reply(b"12"))
    service.browser_open("https://example.com/")
    monkeypatch.setattr(service, "_MAX_BROWSER_BYTES", 4)
    stream = reply(b"345")
    wire.queue.append(stream)
    route = FakeRoute("https://example.com/asset")
    context.route_handler(route)
    assert route.aborted
    assert stream.closed


def test_fetch_preserves_result_contract_and_bounds_text(wire):
    wire.queue.append(reply(b"<title>Page</title><script>secret()</script><p>Hello</p>"))
    result = asyncio.run(service.web_fetch("https://example.com/", max_chars=53))
    assert result["ok"]
    assert result["title"] == "Page"
    assert "secret" not in result["summary"]
    assert result["truncated"]
    assert len(result["content"]) == 53
    assert result["status_code"] == 200


def test_search_uses_same_bounded_provider_transport(wire):
    wire.queue.append(reply(
        b'<div class="result"><a class="result__a" href="https://example.com/">Page</a>'
        b'<span class="result__snippet">Text</span></div>',
    ))
    result = asyncio.run(service.web_search("test query", 1))
    assert result["results"] == [{"title": "Page", "url": "https://example.com/", "snippet": "Text"}]
    assert wire.lookups == [("html.duckduckgo.com", 443)]
    wire.queue.append(reply(headers=b"Content-Length: 9999999\r\n", chunks=[]))
    with pytest.raises(network.NetworkAccessError):
        asyncio.run(service.web_search("test"))


class FakeRoute:
    def __init__(self, url, *, previous=None, navigation=False):
        self.request = SimpleNamespace(
            url=url, method="GET", redirected_from=previous, post_data_buffer=None,
            all_headers=lambda: {}, is_navigation_request=lambda: navigation, frame="main",
        )
        self.fulfilled = None
        self.aborted = None

    def fulfill(self, **kwargs):
        self.fulfilled = kwargs

    def abort(self, reason):
        self.aborted = reason


class FakePage:
    def __init__(self, context):
        self.context = context
        self.handlers = {}
        self.url = "about:blank"
        self.main_frame = "main"
        self.visited = []

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, **kwargs):
        from playwright.sync_api import Error

        self.url = url
        self.visited.append(url)
        route = FakeRoute(url, navigation=True)
        self.context.route_handler(route)
        if route.aborted or self.context.fail_navigation:
            raise Error("navigation failed")

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000)

    def wait_for_load_state(self, state, **kwargs):
        pass

    def wait_for_url(self, predicate, **kwargs):
        pass

    def title(self):
        return "Page"

    def set_viewport_size(self, size):
        self.viewport = size


class FakeContext:
    def __init__(self):
        self.closed = False
        self.offline = False
        self.fail_navigation = False
        self.handlers = {}
        self.page = FakePage(self)

    def set_offline(self, offline):
        self.offline = offline

    def route(self, pattern, handler):
        self.route_handler = handler

    def route_web_socket(self, pattern, handler):
        self.websocket_handler = handler

    def new_page(self):
        return self.page

    def on(self, event, handler):
        self.handlers[event] = handler

    def close(self):
        self.closed = True
        if "close" in self.page.handlers:
            self.page.handlers["close"]()


@pytest.fixture
def browser(monkeypatch):
    from koddi.storage import init_db

    init_db()
    context = FakeContext()
    options = {}

    def new_context(**kwargs):
        options.update(kwargs)
        return context

    monkeypatch.setattr(service, "_ensure_browser", lambda: SimpleNamespace(new_context=new_context))
    monkeypatch.setattr(service, "_PAGES", {})
    monkeypatch.setattr(service, "_SESSION_EVENTS", {})
    return context, options


def test_browser_routes_public_and_denies_private_subresources(browser, wire):
    context, options = browser
    wire.queue.append(reply())
    result = service.browser_open("https://example.com/")
    assert result["ok"]
    assert context.offline
    assert options == {"service_workers": "block", "accept_downloads": False, "device_scale_factor": 2}
    for url in ["http://127.0.0.1/", "file:///C:/Windows/win.ini", "http://user:p@example.com/"]:
        route = FakeRoute(url)
        context.route_handler(route)
        assert route.aborted == "blockedbyclient"
        assert route.fulfilled is None
    wire.queue.append(reply(b"asset"))
    route = FakeRoute("https://example.com/asset.js")
    context.route_handler(route)
    assert route.fulfilled["body"] == b"asset"
    assert len(wire.connections) == 2
    websocket = SimpleNamespace(close=lambda: setattr(context, "websocket_closed", True))
    context.websocket_handler(websocket)
    assert context.websocket_closed
    context.close()
    assert not service._PAGES and not service._SESSION_EVENTS


def test_browser_page_limit_and_navigation_cleanup(browser, wire, monkeypatch):
    context, _ = browser
    service._PAGES.update({index: object() for index in range(service._MAX_PAGES)})
    with pytest.raises(network.NetworkAccessError, match="page limit"):
        service.browser_open("https://example.com/")
    service._PAGES.clear()
    context.fail_navigation = True
    wire.queue.append(reply())
    with pytest.raises(network.NetworkAccessError, match="страница не открылась"):
        service.browser_open("https://example.com/")
    assert context.closed
    assert not service._PAGES and not service._SESSION_EVENTS


def test_browser_private_requires_explicit_caller_option(browser, wire):
    with pytest.raises(network.NetworkAccessError):
        service.browser_open("http://127.0.0.1/")
    wire.queue.append(reply())
    assert service.browser_open("http://127.0.0.1/", allow_private=True)["ok"]
    assert wire.connections[0][0] == "127.0.0.1"


def test_browser_redirect_and_request_budgets(browser, wire, monkeypatch):
    context, _ = browser
    wire.queue.append(reply())
    service.browser_open("https://example.com/")
    previous = None
    for _ in range(network.DEFAULT_LIMITS.max_redirects + 1):
        previous = SimpleNamespace(redirected_from=previous)
    route = FakeRoute("https://example.com/next", previous=previous)
    context.route_handler(route)
    assert route.aborted
    monkeypatch.setattr(service, "_MAX_BROWSER_REQUESTS", 2)
    route = FakeRoute("https://example.com/asset")
    context.route_handler(route)
    assert route.aborted
    assert len(wire.connections) == 1


def test_browser_http_redirects_fail_closed_and_cleanup(browser, wire):
    context, _ = browser
    wire.queue.append(reply(status="302 Found", headers=b"Location: http://127.0.0.1/\r\n"))
    with pytest.raises(network.NetworkAccessError, match="страница не открылась"):
        service.browser_open("https://example.com/")
    # редирект главной страницы повторяется новым переходом, но приватный адрес режется до сети
    assert context.page.visited == ["https://example.com/", "http://127.0.0.1/"]
    assert len(wire.connections) == 1
    assert context.closed
    assert not service._PAGES and not service._SESSION_EVENTS


def test_browser_follows_public_redirect_through_route(browser, wire):
    context, _ = browser
    wire.queue.append(reply(status="301 Moved", headers=b"Location: https://example.com/next\r\n"))
    wire.queue.append(reply())
    result = service.browser_open("http://example.com/")
    assert result["ok"]
    assert context.page.visited == ["http://example.com/", "https://example.com/next"]
    assert len(wire.connections) == 2


def test_real_chromium_uses_routed_responses_while_offline(wire):
    from koddi.storage import init_db
    from playwright.sync_api import Error

    init_db()
    wire.queue.extend([
        reply(
            b'<title>Public</title><script src="/asset.js"></script>'
            b'<img src="http://127.0.0.1/private">'
            b'<script>fetch("http://169.254.169.254/private").then('
            b'()=>window.privateAllowed=true,()=>window.privateBlocked=true)</script>',
            headers=b"Content-Type: text/html\r\n",
        ),
        reply(b"window.assetLoaded=true;", headers=b"Content-Type: text/javascript\r\n"),
    ])
    try:
        try:
            result = service.browser_open("https://example.com/")
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is not installed")
            raise
        assert result["url"] == "https://example.com/"

        def inspect_page():
            page = service._PAGES[result["session_id"]]
            page.wait_for_function("window.assetLoaded && window.privateBlocked", timeout=5000)
            return page.evaluate("window.privateAllowed === true")

        assert not service._BROWSER_THREAD.submit(inspect_page).result(timeout=10)
        assert len(wire.connections) == 2
        assert all(host == PUBLIC_IP for host, _, _ in wire.connections)
    finally:
        service.browser_close_all()
    assert not service._PAGES and not service._SESSION_EVENTS


def test_tun_fake_ip_answers_are_not_treated_as_private_but_real_private_ranges_still_are():
    from koddi.policy import network

    for fake in ("198.18.0.5", "198.19.255.1", "240.0.2.112"):
        assert network._check_address(fake, allow_private=False) == fake
    for blocked in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "255.255.255.255", "0.0.0.0", "224.0.0.1", "::1", "fe80::1"):
        with pytest.raises(network.NetworkAccessError):
            network._check_address(blocked, allow_private=False)
