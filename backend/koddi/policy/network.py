from __future__ import annotations

import asyncio
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import BoundedSemaphore
from typing import Iterable
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx


class NetworkAccessError(ValueError):
    pass


@dataclass(frozen=True)
class NetworkLimits:
    max_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 5
    total_seconds: float = 30
    read_seconds: float = 5
    connect_seconds: float = 5
    dns_seconds: float = 5


DEFAULT_LIMITS = NetworkLimits()
MAX_READ_BYTES = 64 * 1024
_DNS_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="koddi-dns")
_DNS_SLOTS = BoundedSemaphore(4)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_FORWARDED_HEADERS = {
    "accept", "accept-language", "content-type", "cookie", "origin", "referer", "user-agent",
}


def validate_url(value: str) -> httpx.URL:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or "\\" in value
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise NetworkAccessError("invalid network URL")
    try:
        url = httpx.URL(value)
        authority = urlsplit(value).netloc
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or "@" in authority
            or "%" in url.host
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise NetworkAccessError("only HTTP(S) URLs without credentials are allowed")
        return url.copy_with(fragment=None)
    except (ValueError, httpx.InvalidURL) as exc:
        raise NetworkAccessError("invalid network URL") from exc


_TUN_FAKE_IP = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("240.0.0.0/4"))


def _check_address(value: str, *, allow_private: bool) -> str:
    if "%" in value:
        raise NetworkAccessError("scoped addresses are not allowed")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise NetworkAccessError("invalid DNS address") from exc
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            _check_address(str(address.ipv4_mapped), allow_private=allow_private)
            return str(address)
        # переходные IPv6-адреса могут скрывать частный IPv4 за публичным адресом
        if (
            address.sixtofour is not None
            or address.teredo is not None
            or address in ipaddress.ip_network("64:ff9b::/96")
        ):
            raise NetworkAccessError("transition addresses are not allowed")
    # VPN и прокси в режиме TUN (Clash, sing-box, AdGuard VPN) отвечают на DNS «поддельными» адресами из этих диапазонов:
    # они не ведут ни в локальную сеть, ни на этот компьютер, а без них у macOS не работают ни web.fetch, ни браузер агента
    if isinstance(address, ipaddress.IPv4Address) and address != ipaddress.IPv4Address("255.255.255.255") and any(address in net for net in _TUN_FAKE_IP):
        return str(address)
    if address.is_multicast or address.is_unspecified or address.is_reserved:
        raise NetworkAccessError("non-public network address")
    if not allow_private and (
        not address.is_global or address.is_loopback or address.is_link_local
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    ):
        raise NetworkAccessError("non-public network address")
    return str(address)


async def _lookup(host: str, port: int) -> list:
    # зависший системный DNS не должен удерживать asyncio.run при закрытии цикла
    if not _DNS_SLOTS.acquire(blocking=False):
        raise NetworkAccessError("DNS resolver capacity exceeded")
    try:
        future = _DNS_WORKERS.submit(
            socket.getaddrinfo, host, port, socket.AF_UNSPEC, socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
    except BaseException:
        _DNS_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    return await asyncio.wrap_future(future)


async def resolve_target(
    url: httpx.URL, *, allow_private: bool = False, timeout: float = 5,
) -> tuple[str, ...]:
    host = url.raw_host.decode("ascii")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return (_check_address(str(literal), allow_private=allow_private),)
    try:
        records = await asyncio.wait_for(
            _lookup(host, url.port or (443 if url.scheme == "https" else 80)), timeout,
        )
    except (OSError, TimeoutError) as exc:
        raise NetworkAccessError("DNS resolution failed or timed out") from exc
    if not records or len(records) > 64:
        raise NetworkAccessError("invalid DNS answer")
    addresses = []
    for family, _, _, _, sockaddr in records:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise NetworkAccessError("unsupported DNS address family")
        addresses.append(_check_address(sockaddr[0], allow_private=allow_private))
    return tuple(dict.fromkeys(addresses))


class BoundedStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream: httpcore.AsyncNetworkStream):
        self.stream = stream

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            with anyio.fail_after(timeout):
                return await self.stream.read(min(max_bytes, MAX_READ_BYTES), timeout=timeout)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout("HTTP read timed out") from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self.stream.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self.stream.aclose()

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            self.stream = await self.stream.start_tls(
                ssl_context, server_hostname=server_hostname, timeout=timeout,
            )
        except BaseException:
            with anyio.CancelScope(shield=True):
                await self.stream.aclose()
            raise
        return self

    def get_extra_info(self, info: str):
        return self.stream.get_extra_info(info)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, host: str, port: int, addresses: tuple[str, ...]):
        self.host = host
        self.port = port
        self.addresses = addresses
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None,
        local_address: str | None = None, socket_options: Iterable | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self.host or port != self.port or not self.addresses:
            raise NetworkAccessError("connection target does not match DNS pin")
        # меняется только TCP-адрес; httpcore сохраняет исходные Host, SNI и TLS verification
        stream = await self.backend.connect_tcp(
            self.addresses[0], port, timeout=timeout,
            local_address=local_address, socket_options=socket_options,
        )
        return BoundedStream(stream)

    async def connect_unix_socket(self, *args, **kwargs):
        raise NetworkAccessError("Unix sockets are not allowed")


async def _read_response(response: httpcore.Response, max_bytes: int) -> bytes:
    headers = httpx.Headers(response.headers)
    if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
        # иначе декомпрессия на клиенте может обойти лимит байтов
        raise NetworkAccessError("compressed responses are not allowed")
    length = headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal() or len(length) > 16:
            raise NetworkAccessError("invalid response size")
        if int(length) > max_bytes:
            raise NetworkAccessError("response exceeds byte limit")
    body = bytearray()
    async for chunk in response.aiter_stream():
        if len(chunk) > max_bytes - len(body):
            raise NetworkAccessError("response exceeds byte limit")
        body.extend(chunk)
    return bytes(body)


async def fetch_response(
    value: str, *, allow_private: bool = False, limits: NetworkLimits = DEFAULT_LIMITS,
    method: str = "GET", headers: dict[str, str] | None = None,
    content: bytes | None = None, follow_redirects: bool = True,
) -> httpx.Response:
    if not isinstance(allow_private, bool):
        raise NetworkAccessError("allow_private must be an explicit boolean")
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        raise NetworkAccessError("unsupported HTTP method")
    if content is not None and len(content) > DEFAULT_LIMITS.max_bytes:
        raise NetworkAccessError("request exceeds byte limit")
    url = validate_url(value)
    forwarded = {
        name.lower(): value for name, value in (headers or {}).items()
        if name.lower() in _FORWARDED_HEADERS
    }
    forwarded.update({"accept-encoding": "identity", "user-agent": "Koddi/1.0"})
    remaining_bytes = limits.max_bytes
    with anyio.fail_after(limits.total_seconds):
        for redirect_count in range(limits.max_redirects + 1):
            addresses = await resolve_target(
                url, allow_private=allow_private, timeout=limits.dns_seconds,
            )
            backend = PinnedBackend(
                url.raw_host.decode("ascii"), url.port or (443 if url.scheme == "https" else 80),
                addresses,
            )
            request = httpx.Request(method, url, headers=forwarded, content=content)
            # отдельный pool на переход исключает повторное использование чужого DNS pin
            async with httpcore.AsyncConnectionPool(
                network_backend=backend, max_connections=1, max_keepalive_connections=0,
            ) as pool:
                async with pool.stream(
                    request.method, str(url), headers=request.headers.raw, content=content,
                    extensions={"timeout": {
                        "connect": limits.connect_seconds, "read": limits.read_seconds,
                        "write": limits.read_seconds, "pool": limits.connect_seconds,
                    }},
                ) as raw:
                    body = await _read_response(raw, remaining_bytes)
                    remaining_bytes -= len(body)
                    response = httpx.Response(
                        raw.status, headers=raw.headers, content=body, request=request,
                    )
            if response.status_code not in REDIRECT_STATUSES or "location" not in response.headers:
                return response
            target = validate_url(str(url.join(response.headers["location"])))
            if not follow_redirects:
                return response
            if redirect_count == limits.max_redirects:
                raise NetworkAccessError("redirect limit exceeded")
            if (url.scheme, url.host, url.port) != (target.scheme, target.host, target.port):
                forwarded.pop("cookie", None)
                forwarded.pop("origin", None)
                forwarded.pop("referer", None)
            if (response.status_code == 303 and method != "HEAD") or (
                response.status_code in {301, 302} and method == "POST"
            ):
                method, content = "GET", None
                forwarded.pop("content-type", None)
            url = target
    raise NetworkAccessError("redirect limit exceeded")
