"""Обезличенный журнал сбоев для сервера Докки.

Уходят только код сбоя, класс исключения, этап и числа. Текстов чатов, путей, аргументов
и сообщений об ошибках здесь нет и быть не должно: набор полей закрыт.
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import sys
import threading
import time
from collections import deque

from koddi.storage.db import get_setting

STAGES = frozenset({"compaction", "step", "tool", "review", "network", "setup"})
ENDPOINT = "/app/errors"
SAME_CODE_SECONDS = 300
PER_HOUR = 30
# ожидаемые остановки (лимиты, выход из аккаунта) не сбои приложения
NOT_REPORTED = frozenset({"quota_exceeded", "agent_loop_limit", "session_revoked", "invalid_token", "credentials_unavailable"})

_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_WORD = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_lock = threading.Lock()
_last_sent: dict[str, float] = {}
_sent_at: deque[float] = deque()
_tasks: set[asyncio.Task] = set()


def _app_version() -> str:
    from koddi import __version__

    return __version__


def _platform() -> str:
    return f"{sys.platform} {platform.release()}"[:40]


def _number(value, low: int, high: int) -> int | None:
    return value if type(value) is int and low <= value <= high else None


def build_payload(code, kind, *, stage="step", http_status=None, reasoning_mode=None, step=None, **_ignored) -> dict | None:
    """Закрытый набор полей; всё остальное, что передали, отбрасывается."""
    if not isinstance(code, str) or not _CODE.match(code):
        return None
    if not isinstance(kind, str):
        kind = kind.__name__ if isinstance(kind, type) else type(kind).__name__
    mode = reasoning_mode if isinstance(reasoning_mode, str) and _WORD.match(reasoning_mode) else None
    return {
        "code": code,
        "kind": kind if _WORD.match(kind) else "Exception",
        "stage": stage if stage in STAGES else "step",
        "http_status": _number(http_status, 100, 599),
        "reasoning_mode": mode,
        "step": _number(step, 0, 100_000),
        "app_version": _app_version(),
        "platform": _platform(),
    }


def _allowed(code: str) -> bool:
    now = time.monotonic()
    with _lock:
        while _sent_at and now - _sent_at[0] > 3600:
            _sent_at.popleft()
        if len(_sent_at) >= PER_HOUR or now - _last_sent.get(code, -SAME_CODE_SECONDS) < SAME_CODE_SECONDS:
            return False
        _last_sent[code] = now
        _sent_at.append(now)
        return True


def _enabled() -> bool:
    if os.getenv("KODDI_INTERNAL_TEST") == "1":
        return False
    try:
        return get_setting("diagnostics_reporting", True) is not False
    except Exception:
        return False


async def _post(payload: dict) -> None:
    from koddi.dokky.client import dokky

    try:
        await dokky.request("POST", ENDPOINT, json=payload)
    except Exception:
        # сервер мог ещё не знать этот путь или быть недоступен: отчёт необязателен
        pass


def report_error(code, kind="Exception", **fields) -> None:
    """Фоновая отправка: не ждёт сеть и не бросает. Один код - не чаще раза в 5 минут, не больше 30 в час."""
    try:
        if not _enabled():
            return
        loop = asyncio.get_running_loop()
        payload = build_payload(code, kind, **fields)
        if payload is None or payload["code"] in NOT_REPORTED or not _allowed(payload["code"]):
            return
        task = loop.create_task(_post(payload))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:
        # без работающего цикла или при любом сбое отчёт просто пропускается
        pass
