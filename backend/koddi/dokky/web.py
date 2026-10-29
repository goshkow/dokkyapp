"""web.search и web.fetch агента через сервер Докки, без входа и при любой ошибке сервера - локально.

Сервер ищет общим маршрутом (бесплатные поисковики, затем DeepSeek Search) и читает страницы через DeepSeek Fetch;
ключи остаются на сервере, расход входит в лимит режима разработки. На сервер уходят только публичные
https-адреса: локальные, частные и адреса за VPN читаются на этом компьютере и наружу не передаются.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from koddi.browser.service import web_fetch as local_fetch
from koddi.browser.service import web_search as local_search
from koddi.dokky.client import dokky
from koddi.policy.network import NetworkAccessError, resolve_target, validate_url

LOG = logging.getLogger(__name__)
SEARCH_SECONDS = 40
FETCH_SECONDS = 70
SUMMARY_CHARS = 4000
# адреса TUN-прокси (Clash, sing-box) - это не публичный хост, а подмена DNS: такой адрес на сервер не отдаём
_TUN_FAKE_IP = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("240.0.0.0/4"))


async def _server_ready() -> bool:
    try:
        if not dokky.store.load():
            return False
        from koddi.dokky.access import developer_access

        return (await developer_access())["allowed"] is not False
    except Exception as exc:  # noqa: BLE001 - проверка входа не должна ломать поиск, он уйдёт локально
        LOG.info("server access check failed: %s", type(exc).__name__)
        return False


def _body(response) -> dict:
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("unexpected server response")
    return body


async def _public_https(url: str) -> bool:
    try:
        parsed = validate_url(url)
        if parsed.scheme != "https":
            return False
        addresses = await resolve_target(parsed)
    except NetworkAccessError:
        return False
    return not any(ipaddress.ip_address(item) in net for item in addresses for net in _TUN_FAKE_IP)


async def web_search(query: str, limit: int = 10) -> dict:
    limit = min(max(int(limit), 1), 20)
    if query.strip() and len(query) <= 400 and await _server_ready():
        try:
            response = await asyncio.wait_for(
                dokky.request("POST", "/coding/web/search", json={"query": query, "limit": limit}), SEARCH_SECONDS)
            body = _body(response)
            results = [
                {key: str(item[key]) for key in ("title", "url", "snippet", "date") if item.get(key)}
                for item in body.get("results") or [] if isinstance(item, dict) and item.get("url")
            ]
            if results:
                return {"ok": True, "query": query, "results": results[:limit], "source": "Докки"}
        except Exception as exc:  # noqa: BLE001 - любой сбой сервера (сеть, формат ответа) - ищем локально
            LOG.info("server web search unavailable: %s", getattr(exc, "code", type(exc).__name__))
    return await local_search(query, limit)


async def web_fetch(url: str, max_chars: int = 120_000, *, allow_private: bool = False) -> dict:
    if await _server_ready() and await _public_https(url):
        try:
            response = await asyncio.wait_for(dokky.request("POST", "/coding/web/fetch", json={"url": url}), FETCH_SECONDS)
            body = _body(response)
            content = str(body.get("content") or "")
            if content.strip():
                return {
                    "ok": True, "url": url, "title": str(body.get("title") or ""), "status_code": 200,
                    "content_type": "text/markdown", "summary": content[:SUMMARY_CHARS],
                    "content": content[:max_chars],
                    "truncated": bool(body.get("truncated")) or len(content) > max_chars, "source": "Докки",
                }
        except Exception as exc:  # noqa: BLE001 - любой сбой сервера (сеть, формат ответа) - читаем локально
            LOG.info("server web fetch unavailable: %s", getattr(exc, "code", type(exc).__name__))
    return await local_fetch(url, max_chars, allow_private=allow_private)
