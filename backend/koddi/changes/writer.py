"""Один писатель на рабочую папку: два хода агента не правят одни и те же файлы одновременно.

Замок живёт в памяти процесса сервера (сервер один) и привязан к ходу. Освобождается в конце хода; если ход
держателя уже не идёт (сбой, перезапуск), замок считается брошенным и забирается. Чтение не блокируется,
прямые действия человека из интерфейса (без хода) - тоже: это его папка.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from koddi.storage.db import db

_lock = threading.Lock()
_holders: dict[str, "Holder"] = {}


@dataclass
class Holder:
    turn_id: int
    since: float


class WriterBusy(RuntimeError):
    def __init__(self, holder: Holder):
        super().__init__(f"папку сейчас меняет другой ход агента ({holder.turn_id})")
        self.holder = holder


def _key(workspace: Path) -> str:
    return os.path.normcase(str(workspace.resolve()))


def _running(turn_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT status FROM turns WHERE id=?", (turn_id,)).fetchone()
    return row is not None and row["status"] == "running"


def claim(workspace: Path | None, turn_id: int | None, *, _retry: bool = True) -> None:
    """Берёт папку для записи ходом или поднимает WriterBusy, если её держит другой идущий ход."""
    if workspace is None or turn_id is None:
        return
    key = _key(workspace)
    with _lock:
        current = _holders.get(key)
        if current is None or current.turn_id == turn_id:
            _holders[key] = current or Holder(turn_id, time.time())
            return
    # статус хода читается вне замка; брошенный замок (ход упал или сервер перезапускался) забирается
    if _running(current.turn_id):
        raise WriterBusy(current)
    with _lock:
        if _holders.get(key) is current:
            _holders[key] = Holder(turn_id, time.time())
            return
    if not _retry:
        raise WriterBusy(_holders.get(key) or current)
    claim(workspace, turn_id, _retry=False)


def release(turn_id: int | None) -> None:
    if turn_id is None:
        return
    with _lock:
        for key in [key for key, holder in _holders.items() if holder.turn_id == turn_id]:
            _holders.pop(key, None)


def holder(workspace: Path) -> Holder | None:
    with _lock:
        return _holders.get(_key(workspace))


def active_keys() -> set[str]:
    """Папки, которые сейчас держат идущие ходы (в формате путей сравнения)."""
    with _lock:
        return set(_holders)
