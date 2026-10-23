"""HTTP к `/api/v1/remote` сервера Докки с токеном аккаунта (`koddi.dokky.client`).

Токен обновляет общий клиент; здесь только перевод ответа в `RemoteFailure` с кодом и паузой повтора.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from koddi.dokky.client import ORIGIN, DokkyError, dokky


@dataclass
class RemoteFailure(Exception):
    status: int
    code: str
    message: str
    retry_after: float = 0

    def __str__(self) -> str:
        return self.message

    @property
    def network(self) -> bool:
        # сеть, 5xx и временная недоступность сервера повторяются с паузой, ничего не теряя
        return self.status >= 500 or self.code in {"upstream_unavailable", "service_unavailable"}


def dokky_origin() -> str:
    return ORIGIN


def _path(*parts: str) -> str:
    return "/remote/" + "/".join(quote(str(part), safe="") for part in parts)


async def call(method: str, *parts: str, json: Any = None, params: dict | None = None) -> dict:
    try:
        response = await dokky.request(method, _path(*parts), json=json, params=params)
    except DokkyError as exc:
        raise RemoteFailure(exc.status, exc.code, str(exc), float(exc.retry_after or 0)) from exc
    try:
        value = response.json()
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


async def register(body: dict) -> dict:
    return await call("POST", "hosts", json=body)


async def heartbeat(host_id: str, state: dict) -> dict:
    return await call("POST", "hosts", host_id, "heartbeat", json={"state": state})


async def commands(host_id: str, wait: int) -> list[dict]:
    value = await call("GET", "hosts", host_id, "commands", params={"wait": wait})
    items = value.get("commands")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


async def ack(host_id: str, command_id: str, status: str, error: str = "") -> None:
    body = {"status": status, **({"error": error[:500]} if error else {})}
    await call("POST", "hosts", host_id, "commands", command_id, json=body)


async def put_session(host_id: str, key: str, body: dict) -> dict:
    return await call("PUT", "hosts", host_id, "sessions", key, json=body)


async def close_session(host_id: str, key: str) -> None:
    await call("DELETE", "hosts", host_id, "sessions", key)


async def message(host_id: str, key: str, body: dict) -> dict:
    return await call("POST", "hosts", host_id, "sessions", key, "messages", json=body)


async def events(host_id: str, key: str, items: list[dict]) -> dict:
    return await call("POST", "hosts", host_id, "sessions", key, "events", json={"events": items})
