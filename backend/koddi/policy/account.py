from __future__ import annotations

import os

from fastapi import HTTPException

from koddi.storage.db import db, get_setting


def local_owner() -> str:
    if os.getenv('KODDI_INTERNAL_TEST') != '1':
        from koddi.dokky.client import dokky

        if not dokky.store.load():
            raise HTTPException(401,'Войдите с помощью Докки')
    user = get_setting("dokky_user", {})
    value = user.get("id") or user.get("user_id")
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        return str(value)
    if os.getenv("KODDI_INTERNAL_TEST") == "1":
        return "__test__"
    raise HTTPException(401, "Войдите с помощью Докки")


def owns(owner: str, expected: str) -> bool:
    # старые фикстуры без владельца разрешены только в явно тестовом процессе
    return owner == expected or (
        not owner and os.getenv("KODDI_INTERNAL_TEST") == "1"
    )


def require_chat(chat_id: int) -> dict:
    owner = local_owner()
    with db() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if row is None or not owns(row["owner_id"], owner):
        raise HTTPException(404, "Чат не найден")
    return dict(row)
