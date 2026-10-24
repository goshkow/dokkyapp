"""Откат чата разработки к сообщению пользователя: как «изменить сообщение» в Claude Code и Codex.

Удаляются само сообщение и всё после него: ответы, ходы, планы, запросы, журнал агента. Серверная сессия
Докки забывает подтверждённую историю, и следующий ход отправляет её целиком. Файлы проекта по желанию
возвращаются через записи изменений этих ходов.
"""

from __future__ import annotations

import json

from fastapi import HTTPException

from koddi.policy.account import require_chat
from koddi.storage.db import db


def _target(conn, chat_id: int, message_id: int):
    row = conn.execute("SELECT * FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Сообщение не найдено")
    if row["role"] != "user":
        raise HTTPException(422, "Изменить можно только своё сообщение")
    metadata = json.loads(row["metadata_json"] or "{}")
    turn = metadata.get("turn_id")
    return row, metadata, int(turn) if isinstance(turn, int) else None


def _change_sets(conn, chat_id: int, turn_id: int | None) -> list[int]:
    if turn_id is None:
        return []
    rows = conn.execute(
        "SELECT cs.id FROM change_sets cs JOIN turns t ON t.id=cs.turn_id "
        "WHERE t.chat_id=? AND cs.turn_id>=? AND cs.status='applied' ORDER BY cs.id DESC",
        (chat_id, turn_id),
    ).fetchall()
    return [int(row[0]) for row in rows]


def preview(chat_id: int, message_id: int) -> dict:
    require_chat(chat_id)
    with db() as conn:
        _row, _metadata, turn_id = _target(conn, chat_id, message_id)
        later = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND id>?", (chat_id, message_id)).fetchone()[0]
        return {"messages_after": int(later), "file_changes": len(_change_sets(conn, chat_id, turn_id))}


def rewind(chat_id: int, message_id: int, *, files: bool = False) -> dict:
    from koddi.agents import agent_runs

    require_chat(chat_id)
    if agent_runs.active(chat_id):
        raise HTTPException(409, "Сначала остановите выполнение")
    with db() as conn:
        row, metadata, turn_id = _target(conn, chat_id, message_id)
        change_sets = _change_sets(conn, chat_id, turn_id) if files else []
    rolled_back, failed = 0, 0
    if change_sets:
        from koddi.changes import rollback_change_set

        # от последнего изменения к первому: каждый откат возвращает файл к состоянию до своего хода
        for change_set in change_sets:
            result = rollback_change_set(change_set)
            if result.get("rolled_back"):
                rolled_back += 1
            else:
                failed += 1
        if failed:
            # чат не трогаем: иначе невернувшиеся изменения пропадут из чата и повторить откат будет нельзя
            raise HTTPException(
                409,
                f"Не удалось вернуть часть файлов ({failed} из {len(change_sets)}). Чат не изменён, "
                "проверьте изменения проекта и повторите или откатите только чат",
            )
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        boundary = None
        if turn_id is not None:
            turn = conn.execute("SELECT history_from FROM turns WHERE id=? AND chat_id=?", (turn_id, chat_id)).fetchone()
            boundary = turn["history_from"] if turn else None
        if boundary is None:
            # ходы до 0.2.1 не помнят границу журнала: берём записи, сделанные не раньше самого сообщения
            first = conn.execute(
                "SELECT MIN(id) FROM agent_history WHERE chat_id=? AND created_at>=?", (chat_id, row["created_at"])
            ).fetchone()[0]
            boundary = int(first) - 1 if first is not None else None
        if boundary is not None:
            conn.execute("DELETE FROM agent_history WHERE chat_id=? AND id>?", (chat_id, boundary))
        if turn_id is not None:
            later = [item[0] for item in conn.execute("SELECT id FROM turns WHERE chat_id=? AND id>=?", (chat_id, turn_id))]
            for table in ("agent_plans", "agent_requests", "tool_calls"):
                conn.executemany(f"DELETE FROM {table} WHERE turn_id=?", [(item,) for item in later])
            conn.execute("DELETE FROM turns WHERE chat_id=? AND id>=?", (chat_id, turn_id))
        conn.execute("DELETE FROM messages WHERE chat_id=? AND id>=?", (chat_id, message_id))
        # сервер Докки помнит прежнее продолжение: следующий ход отправит историю целиком, без дельты
        conn.execute(
            "UPDATE coding_sessions SET messages_json='[]', history_json='null', updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
            (chat_id,),
        )
        conn.execute("DELETE FROM coding_pending WHERE chat_id=?", (chat_id,))
        conn.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
        ids = [int(item) for item in metadata.get("attachment_ids") or [] if isinstance(item, int)]
        attachments = []
        if ids:
            marks = ",".join("?" * len(ids))
            attachments = [
                dict(item)
                for item in conn.execute(
                    f"SELECT * FROM attachments WHERE chat_id=? AND id IN ({marks}) AND status<>'deleted'", (chat_id, *ids)
                )
            ]
    return {
        "ok": True,
        "content": row["content"],
        "attachments": [
            {"id": item["id"], "original_name": item["original_name"], "media_type": item["media_type"], "size": item["size_bytes"]}
            for item in attachments
        ],
        "files_rolled_back": rolled_back,
        "files_failed": failed,
    }
