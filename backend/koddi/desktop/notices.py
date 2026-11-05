"""Уведомление о работе агента, пока окно Докки не на переднем плане: агент закончил ход или ждёт ответа человека."""

from __future__ import annotations

import json
from dataclasses import dataclass

APP_NAME = "Докки"

# виды, которые присылает страница
PAGE_KINDS = ("done", "question", "approval", "stalled", "limit")
BODIES = {
    "done": "Агент закончил работу",
    "question": "Докки задал вопрос",
    "approval": "Нужно подтвердить действие",
    "stalled": "Докки временно не отвечает - работа на паузе",
    "limit": "Лимит восстановился - можно продолжить работу",
    # служебное, только от хоста: страница его прислать не может
    "tray": "Докки работает в трее - агент продолжит задачу. Открыть или выйти можно из значка",
}
# о чём человека стоит оповестить звуком и подпрыгиванием: агент ждёт его
URGENT = frozenset({"question", "approval"})
TITLE_LIMIT = 120


@dataclass(frozen=True)
class Notice:
    chat_id: int
    kind: str
    chat_title: str

    @property
    def title(self) -> str:
        return self.chat_title or APP_NAME

    @property
    def body(self) -> str:
        return BODIES[self.kind]


def parse(payload: str) -> Notice | None:
    """Страница присылает JSON; всё, что не похоже на уведомление о своём чате, отбрасывается."""
    if not isinstance(payload, str) or len(payload) > 4096:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    chat_id, kind, title = data.get("chat_id"), data.get("kind"), data.get("title", "")
    if type(chat_id) is not int or chat_id <= 0 or kind not in PAGE_KINDS or not isinstance(title, str):
        return None
    # переводы строк и управляющие символы в заголовке системного уведомления не нужны
    title = " ".join("".join(ch if ch.isprintable() else " " for ch in title).split())
    if len(title) > TITLE_LIMIT:
        title = title[: TITLE_LIMIT - 1].rstrip() + "…"
    return Notice(chat_id, kind, title)
