from __future__ import annotations

import threading
import time


IDLE_SECONDS = 5 * 60
_lock = threading.Lock()
_active: dict[tuple[str, int], float] = {}


def mark(owner: str, chat_id: int) -> None:
    now = time.monotonic()
    with _lock:
        expired = [key for key, seen in _active.items() if now - seen > IDLE_SECONDS]
        for key in expired:
            del _active[key]
        _active[(owner, chat_id)] = now


def recent(owner: str, chat_id: int) -> bool:
    with _lock:
        seen = _active.get((owner, chat_id))
    return seen is not None and time.monotonic() - seen <= IDLE_SECONDS
