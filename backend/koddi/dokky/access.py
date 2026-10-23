"""Доступ к режиму разработки: решает сервер Докки, приложение показывает причину и не даёт писать без доступа.

Докки Code открыт с оплаченным Plus и выше, тестерам с кредитами и админам. Free, пробный период и Base получают от
сервера 403 `plan_required`. Приложение заранее читает `GET /subscription` (`developer.allowed`, `reason_code`) и
не пускает запись в чат разработки, но это только удобство: настоящее решение всегда за сервером, а любой отказ
`plan_required` (в том числе посреди работы агента) закрывает запись так же. Чтение уже созданных чатов, их
переименование и удаление от тарифа не зависят.
"""

from __future__ import annotations

import time

from fastapi import HTTPException

from koddi.dokky.client import DokkyError, account_identity, dokky

# лимиты не относятся к тарифу: человек остаётся в режиме, ему показывается подсказка про сброс или улучшение
LIMIT_REASONS = frozenset({"window_exhausted", "overall_exhausted", "usage_exhausted", "quota_exceeded"})
OPEN_SECONDS = 60
CLOSED_SECONDS = 15
DEFAULT_MESSAGE = "Докки Code включается с подпиской Plus или выше. Обновите план, чтобы писать агенту."

_cache: dict = {"identity": None, "at": 0.0, "value": None}


def reset() -> None:
    """Забывает прошлый ответ: после оплаты, выхода или смены аккаунта доступ читается заново."""
    _cache.update({"identity": None, "at": 0.0, "value": None})


def evaluate(subscription: object) -> dict:
    """Превращает ответ `/subscription` в решение о доступе; неполный ответ ничего не запрещает."""
    developer = subscription.get("developer") if isinstance(subscription, dict) else None
    developer = developer if isinstance(developer, dict) else {}
    allowed = developer.get("allowed") if isinstance(developer.get("allowed"), bool) else None
    reason = str(developer.get("reason_code") or "")
    locked = allowed is False and reason not in LIMIT_REASONS
    plan = str((subscription or {}).get("plan") or "") if isinstance(subscription, dict) else ""
    return {
        "allowed": None if allowed is None else not locked,
        "locked": locked,
        "reason": reason,
        "message": str(developer.get("reason") or "")[:300] if locked else "",
        "plan": plan,
    }


async def developer_access(*, refresh: bool = False) -> dict:
    """Текущий доступ к Докки Code. Сеть недоступна или ответ странный - `allowed: None`, решает сервер."""
    identity = account_identity()
    now = time.monotonic()
    cached = _cache["value"]
    if (
        not refresh
        and cached is not None
        and _cache["identity"] == identity
        and now - _cache["at"] < (CLOSED_SECONDS if cached["locked"] else OPEN_SECONDS)
    ):
        return cached
    try:
        subscription = (await dokky.request("GET", "/subscription")).json()
    except (DokkyError, ValueError):
        return {"allowed": None, "locked": False, "reason": "", "message": "", "plan": ""}
    value = evaluate(subscription)
    _cache.update({"identity": identity, "at": now, "value": value})
    return value


def mark_denied(message: str = "") -> None:
    """Сервер ответил `plan_required`: закрываем доступ сразу, не дожидаясь следующего опроса."""
    _cache.update({
        "identity": account_identity(),
        "at": time.monotonic(),
        "value": {"allowed": False, "locked": True, "reason": "plus_required", "message": message[:300], "plan": ""},
    })


async def require_developer() -> None:
    """Не даёт начать работу агента без доступа. Устаревший отказ перепроверяется: оплатившего не задерживаем."""
    value = await developer_access()
    if value["locked"]:
        value = await developer_access(refresh=True)
    if value["locked"]:
        raise HTTPException(403, {"code": "plan_required", "message": value["message"] or DEFAULT_MESSAGE, "required_plan": "plus"})
