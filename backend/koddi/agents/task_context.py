from __future__ import annotations

import json

from koddi.storage.db import db


def save_completed_context(turn_id: int) -> None:
    with db() as conn:
        turn = conn.execute("SELECT chat_id,status FROM turns WHERE id=?", (turn_id,)).fetchone()
        if turn is None or turn["status"] != "completed":
            return
        rows = conn.execute(
            "SELECT id,content,metadata_json FROM messages WHERE chat_id=? AND role='assistant' ORDER BY id DESC",
            (turn["chat_id"],),
        )
        final = ""
        final_message_id = None
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            if meta.get("turn_id") == turn_id and meta.get("channel") == "final":
                final = row["content"] or ""
                final_message_id = row["id"]
                break
        changes = conn.execute(
            "SELECT f.path,f.before_hash,f.after_hash,s.id AS change_set_id,s.status FROM file_changes f JOIN change_sets s ON s.id=f.change_set_id WHERE s.turn_id=? ORDER BY f.id LIMIT 101",
            (turn_id,),
        ).fetchall()
        tools = conn.execute(
            "SELECT id,name,status FROM tool_calls WHERE turn_id=? ORDER BY id LIMIT 201",
            (turn_id,),
        ).fetchall()
        verification = conn.execute(
            "SELECT id,name,status,json_extract(result_json,'$.returncode') AS returncode,"
            "json_extract(result_json,'$.checks') AS checks FROM tool_calls WHERE turn_id=? "
            "AND (name IN ('test.run','build.run','lint.run','verification.run') OR json_type(result_json,'$.checks')='array') "
            "ORDER BY id DESC LIMIT 31",
            (turn_id,),
        ).fetchall()
        checks = []
        remaining = 40
        for row in verification[:30]:
            all_checks = json.loads(row["checks"]) if row["checks"] else []
            selected = all_checks[:remaining]
            compact = []
            for check in selected:
                if isinstance(check, dict):
                    compact.append(
                        {
                            key: (value[:500] if isinstance(value, str) else value)
                            for key, value in check.items()
                            if key in {"path", "ok", "check", "error"}
                            and isinstance(value, (str, bool, int, float, type(None)))
                        }
                    )
            checks.append(
                {
                    **dict(row),
                    "checks": compact,
                    "checks_truncated": len(all_checks) > len(selected),
                }
            )
            remaining -= len(selected)
        value = {
            "turn_id": turn_id,
            "summary": final[:8000],
            "summary_truncated": len(final) > 8000,
            "final_message_id": final_message_id,
            "final_size_chars": len(final),
            "changes": [dict(row) for row in changes[:100]],
            "changes_truncated": len(changes) > 100,
            "actions": [dict(row) for row in tools[:200]],
            "actions_truncated": len(tools) > 200,
            "verification": checks,
            "verification_truncated": len(verification) > 30,
            "history_source": "user_visible_messages_and_agent_history",
        }
        conn.execute(
            "INSERT OR REPLACE INTO turn_contexts(turn_id,context_json) VALUES (?,?)",
            (turn_id, json.dumps(value, ensure_ascii=False)),
        )


def read_completed_context(
    turn_id: int | None,
    source_turn_id: int | None = None,
    *,
    action_id: int | None = None,
    message_id: int | None = None,
    before_action_id: int | None = None,
    offset: int = 0,
    max_chars: int = 6000,
) -> dict:
    from koddi.policy.account import require_chat

    if action_id is not None and message_id is not None:
        raise ValueError("Выберите действие или сообщение")
    if offset < 0 or not 1 <= max_chars <= 8000:
        raise ValueError("Некорректный диапазон архива")
    with db() as conn:
        current = conn.execute("SELECT chat_id,status FROM turns WHERE id=?", (turn_id,)).fetchone()
        if not current:
            raise ValueError("Чтение истории требует активного контекста чата")
        require_chat(current["chat_id"])
        message = None
        if message_id is not None:
            message = conn.execute(
                "SELECT id,chat_id,role,content,metadata_json FROM messages WHERE id=? AND chat_id=?",
                (message_id, current["chat_id"]),
            ).fetchone()
            if not message:
                raise ValueError("Сообщение не найдено в этом чате")
            message_turn = json.loads(message["metadata_json"] or "{}").get("turn_id")
            if (
                message_turn is None
                or source_turn_id is not None
                and source_turn_id != message_turn
            ):
                raise ValueError("Сообщение не связано с выбранным ходом")
            source_turn_id = message_turn
        if source_turn_id is None and action_id is not None:
            action = conn.execute(
                "SELECT turn_id FROM tool_calls WHERE id=?", (action_id,)
            ).fetchone()
            source_turn_id = action["turn_id"] if action else None
            if source_turn_id is None:
                raise ValueError("Действие не найдено")
        if source_turn_id is None:
            source = conn.execute(
                "SELECT c.turn_id,t.chat_id,t.status,c.context_json FROM turn_contexts c JOIN turns t ON t.id=c.turn_id WHERE t.chat_id=? AND t.status='completed' ORDER BY c.turn_id DESC LIMIT 1",
                (current["chat_id"],),
            ).fetchone()
        else:
            source = conn.execute(
                "SELECT t.id AS turn_id,t.chat_id,t.status,c.context_json FROM turns t LEFT JOIN turn_contexts c ON c.turn_id=t.id WHERE t.id=?",
                (source_turn_id,),
            ).fetchone()
        if source is None and source_turn_id is None and action_id is None and message_id is None:
            source = {
                "turn_id": turn_id,
                "chat_id": current["chat_id"],
                "status": current["status"],
                "context_json": None,
            }
        if not source or current["chat_id"] != source["chat_id"]:
            raise ValueError("Результат недоступен в этом чате")
        if message is not None:
            content = message["content"] or ""
            return _fragment(
                content,
                source["turn_id"],
                offset,
                max_chars,
                message_id=message_id,
                role=message["role"],
            )
        if action_id is None:
            actions_turn = source_turn_id if source_turn_id is not None else turn_id
            references = conn.execute(
                "SELECT id,name,status FROM tool_calls WHERE turn_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 51",
                (actions_turn, before_action_id, before_action_id),
            ).fetchall()
            return {
                "ok": True,
                "context": json.loads(source["context_json"]) if source["context_json"] else None,
                "runtime_status": source["status"],
                "actions_turn_id": actions_turn,
                "actions": [dict(row) for row in references[:50]],
                "next_action_before": references[49]["id"] if len(references) > 50 else None,
            }
        action = conn.execute(
            "SELECT id,name,arguments_json,result_json,status FROM tool_calls WHERE id=? AND turn_id=?",
            (action_id, source["turn_id"]),
        ).fetchone()
    if not action:
        raise ValueError("Действие не найдено в выбранном ходе")
    content = json.dumps(
        {
            "id": action["id"],
            "name": action["name"],
            "status": action["status"],
            "arguments": json.loads(action["arguments_json"]),
            "result": json.loads(action["result_json"]),
        },
        ensure_ascii=False,
    )
    return _fragment(content, source["turn_id"], offset, max_chars, action_id=action_id)


def _fragment(content: str, source_turn_id: int, offset: int, max_chars: int, **reference) -> dict:
    fragment = content[offset : offset + max_chars]
    return {
        "ok": True,
        "source_turn_id": source_turn_id,
        **reference,
        "offset": offset,
        "content": fragment,
        "next_offset": offset + len(fragment) if offset + len(fragment) < len(content) else None,
        "eof": offset + len(fragment) >= len(content),
    }
