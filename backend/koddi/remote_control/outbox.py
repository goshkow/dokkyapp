"""Надёжная очередь на сервер: всё, что ещё не дошло, лежит в SQLite и переживает обрыв сети и перезапуск.

Номер события (`client_seq`) выдаётся при постановке в очередь в той же транзакции, поэтому он растёт внутри
сессии и не сбрасывается между запусками; сервер отбрасывает уже принятые номера, повтор пачки не даёт дублей.
"""

from __future__ import annotations

import json
from typing import Any

from koddi.storage.db import db

MAX_BATCH = 100
PENDING_LIMIT = 5000
BOUND_EVERY = 200
SEEN_LIMIT = 10000


def session_key(chat_id: int) -> str:
    return f"chat-{int(chat_id)}"


def session(chat_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM remote_sessions WHERE chat_id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


def session_by_key(key: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM remote_sessions WHERE session_key=?", (key,)).fetchone()
    return dict(row) if row else None


def shared_key(chat_id: int) -> str | None:
    row = session(chat_id)
    return row["session_key"] if row and row["shared"] else None


def ensure_session(chat_id: int) -> dict:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO remote_sessions(chat_id, session_key) VALUES (?, ?)",
            (chat_id, session_key(chat_id)),
        )
        return dict(conn.execute("SELECT * FROM remote_sessions WHERE chat_id=?", (chat_id,)).fetchone())


def event(key: str, kind: str, text: str = "", data: dict | None = None) -> int | None:
    """Событие хода с новым номером; None - сессия не делится (событие не нужно)."""
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "UPDATE remote_sessions SET last_client_seq=last_client_seq+1 WHERE session_key=? AND shared=1 "
            "RETURNING last_client_seq",
            (key,),
        ).fetchone()
        if not row:
            return None
        seq = int(row[0])
        item: dict[str, Any] = {"client_seq": seq, "kind": kind}
        if text:
            item["text"] = text
        if data:
            item["data"] = data
        conn.execute(
            "INSERT INTO remote_outbox(session_key, kind, body) VALUES (?, 'event', ?)",
            (key, json.dumps(item, ensure_ascii=False)),
        )
    if seq % BOUND_EVERY == 0:
        # без сети с самого запуска уборка моста не идёт: предел держит сама очередь
        bound()
    return seq


def put(key: str, kind: str, body: dict) -> bool:
    """Сессия (`session`), сообщение истории (`message`) или закрытие (`close`) по порядку с событиями."""
    with db() as conn:
        if kind != "close" and not conn.execute(
            "SELECT 1 FROM remote_sessions WHERE session_key=? AND shared=1", (key,)
        ).fetchone():
            return False
        conn.execute(
            "INSERT INTO remote_outbox(session_key, kind, body) VALUES (?, ?, ?)",
            (key, kind, json.dumps(body, ensure_ascii=False)),
        )
    return True


def pending(limit: int = 300) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT id, session_key, kind, body FROM remote_outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [{"id": row["id"], "key": row["session_key"], "kind": row["kind"], "body": json.loads(row["body"])} for row in rows]


def count() -> int:
    with db() as conn:
        return int(conn.execute("SELECT count(*) FROM remote_outbox").fetchone()[0])


def batches(rows: list[dict]) -> list[list[dict]]:
    """Подряд идущие события одной сессии - одна пачка (до 100); остальное по одному, порядок сохраняется."""
    groups: list[list[dict]] = []
    for row in rows:
        last = groups[-1] if groups else None
        if (
            last
            and row["kind"] == "event"
            and last[0]["kind"] == "event"
            and last[0]["key"] == row["key"]
            and len(last) < MAX_BATCH
        ):
            last.append(row)
        else:
            groups.append([row])
    return groups


def done(ids: list[int]) -> None:
    if not ids:
        return
    with db() as conn:
        conn.execute(f"DELETE FROM remote_outbox WHERE id IN ({','.join('?' for _ in ids)})", ids)


def drop_session(key: str, *, keep_close: bool = False) -> None:
    """Сессию больше не показываем: недошедшее для неё не нужно."""
    with db() as conn:
        conn.execute(
            "DELETE FROM remote_outbox WHERE session_key=?" + (" AND kind<>'close'" if keep_close else ""),
            (key,),
        )


def pending_sessions() -> list[str]:
    with db() as conn:
        return [row[0] for row in conn.execute("SELECT DISTINCT session_key FROM remote_outbox").fetchall()]


def trim_noise(key: str) -> int:
    """Сервер упёрся в предел событий хода: выкинуть мелкое (дельты, статусы, обновления), оставить итоги."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, body FROM remote_outbox WHERE session_key=? AND kind='event'", (key,)
        ).fetchall()
        noise = []
        for row in rows:
            body = json.loads(row["body"])
            kind = body.get("kind")
            if kind in {"delta", "status", "tool", "commentary", "reset"} or (
                kind == "action" and (body.get("data") or {}).get("state") == "running"
            ):
                noise.append(row["id"])
        if noise:
            conn.execute(f"DELETE FROM remote_outbox WHERE id IN ({','.join('?' for _ in noise)})", noise)
    return len(noise)


def raise_seq(key: str, server_seq: int) -> None:
    """После потери локальной базы номер продолжается с принятого сервером."""
    with db() as conn:
        conn.execute(
            "UPDATE remote_sessions SET last_client_seq=? WHERE session_key=? AND last_client_seq<?",
            (int(server_seq), key, int(server_seq)),
        )


def seen(command_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT status, error FROM remote_commands_seen WHERE id=?", (command_id,)).fetchone()
    return dict(row) if row else None


def mark_seen(command_id: str, status: str, error: str = "") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO remote_commands_seen(id, status, error) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, error=excluded.error",
            (command_id, status, error[:500]),
        )


def bound() -> None:
    """Долго без сети очередь не растёт без конца: сверх предела выкидываем мелкое, итоги остаются."""
    if count() > PENDING_LIMIT:
        for key in pending_sessions():
            trim_noise(key)


def cleanup() -> None:
    # отметки живут месяц: старая команда, выданная повторно, не исполнится второй раз
    with db() as conn:
        conn.execute("DELETE FROM remote_commands_seen WHERE seen_at < datetime('now', '-30 day')")
        conn.execute(
            "DELETE FROM remote_commands_seen WHERE id NOT IN "
            "(SELECT id FROM remote_commands_seen ORDER BY seen_at DESC LIMIT ?)",
            (SEEN_LIMIT,),
        )
