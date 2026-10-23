"""Деньги расхода не попадают ни в клиентский бэкенд, ни в интерфейс.

Сервер Докки присылает цены и списания (`charged_rub`, `usd` в планах смены режима и т.п.). Приложение их не
считает, не хранит и не показывает: пользователь видит только остаток лимита в процентах и размер контекста
в токенах. Эти ключи вырезаются сразу при разборе ответа сервера.

Исключение - цена, которую человек сам платит за план или сброс использования: сервер отдаёт её готовой в
`price` и `checkout_price` каталога планов, и только эти два ключа в ответах `/billing/*` и `/subscription`
остаются (`PAID_PRICE_KEYS`). Себестоимость и списания по-прежнему вырезаются везде.
"""

from __future__ import annotations

import json
import re

_MONEY = re.compile(r"(^|_)(rub|usd|price|prices|cost|costs|charged|money)(_|$)", re.IGNORECASE)
PAID_PRICE_KEYS = frozenset({"price", "checkout_price"})


def scrub_money(value, keep=frozenset()):
    """Возвращает копию без ключей с деньгами (кроме перечисленных в `keep`); остальное не меняется."""
    if isinstance(value, dict):
        return {
            key: scrub_money(item, keep)
            for key, item in value.items()
            if not (isinstance(key, str) and key not in keep and _MONEY.search(key))
        }
    if isinstance(value, list):
        return [scrub_money(item, keep) for item in value]
    return value


def scrub_response(response, keep=frozenset()):
    """Подменяет тело JSON-ответа httpx на очищенное: дальше .json() и .content не содержат денег расхода."""
    try:
        if "json" not in response.headers.get("content-type", "").lower():
            return response
        clean = scrub_money(response.json(), keep)
    except (ValueError, UnicodeDecodeError):
        return response
    response._content = json.dumps(clean, ensure_ascii=False).encode("utf-8")
    response.headers.pop("content-length", None)
    return response


def scrub_event_line(line: str) -> str:
    """Строка SSE `data: {...}` без денег; остальные строки возвращаются как есть."""
    if not line.startswith("data:"):
        return line
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return line
    try:
        return "data: " + json.dumps(scrub_money(json.loads(raw)), ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        return line
