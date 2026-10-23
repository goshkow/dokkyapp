from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import HTTPException

from koddi.policy.account import local_owner, require_chat
from koddi.storage.db import db


class RequestQueue:
    def __init__(self):
        self.launch = None
        self.active = lambda _chat: False
        self.last_status = lambda _chat: "completed"
        self.workers: dict[int, asyncio.Task] = {}
        # чаты, где пользователь сам прервал задачу ради запроса из очереди: остановка не ставит очередь на паузу
        self.interrupted: set[int] = set()

    def snapshot(self, chat_id: int) -> dict:
        require_chat(chat_id)
        with db() as conn:
            state = conn.execute("SELECT paused FROM queue_state WHERE chat_id=?", (chat_id,)).fetchone()
            rows = conn.execute(
                "SELECT * FROM queued_requests WHERE chat_id=? AND owner_id=? AND status IN ('queued','running','failed') ORDER BY sequence LIMIT 100",
                (chat_id, local_owner()),
            ).fetchall()
        return {"paused": bool(state and state[0]), "items": [
            {"id": row["id"], "version": row["version"], "status": row["status"],
             "content": json.loads(row["payload_json"])["content"], "created_at": row["created_at"]}
            for row in rows
        ]}

    def add(self, chat_id: int, payload: dict, key: str) -> dict:
        chat = require_chat(chat_id)
        try:
            uuid.UUID(key)
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(422, "Нужен UUID запроса") from exc
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM queued_requests WHERE id=?", (key,)).fetchone()
            if old:
                if old["owner_id"] != local_owner() or old["chat_id"] != chat_id or old["payload_json"] != encoded:
                    raise HTTPException(409, "Ключ уже использован для другого запроса")
            else:
                count = conn.execute("SELECT count(*) FROM queued_requests WHERE chat_id=? AND status='queued'", (chat_id,)).fetchone()[0]
                if count >= 100:
                    raise HTTPException(409, "В очереди уже 100 запросов")
                conn.execute(
                    "INSERT INTO queued_requests(id,owner_id,chat_id,project_id,payload_json) VALUES (?,?,?,?,?)",
                    (key, local_owner(), chat_id, chat["project_id"], encoded),
                )
                conn.execute("INSERT OR IGNORE INTO queue_state(chat_id) VALUES (?)", (chat_id,))
        self.wake(chat_id)
        return self.snapshot(chat_id)

    def edit(self, chat_id: int, key: str, version: int, content: str | None) -> dict:
        require_chat(chat_id)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM queued_requests WHERE id=? AND chat_id=? AND owner_id=?", (key, chat_id, local_owner())).fetchone()
            if not row:
                raise HTTPException(404, "Запрос не найден")
            if row["status"] not in {"queued", "failed"} or row["version"] != version:
                raise HTTPException(409, "Запрос изменился или уже выполняется")
            if content is None:
                conn.execute("UPDATE queued_requests SET status='cancelled',version=version+1 WHERE id=?", (key,))
            else:
                if row["status"] != "queued":
                    raise HTTPException(409, "Неопределённый результат нельзя повторить без новой задачи")
                if not content.strip() or len(content) > 200000:
                    raise HTTPException(422, "Недопустимый текст")
                payload = json.loads(row["payload_json"])
                payload["content"] = content
                conn.execute("UPDATE queued_requests SET payload_json=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(payload, ensure_ascii=False, sort_keys=True), key))
        return self.snapshot(chat_id)

    def promote(self, chat_id: int, key: str, version: int) -> dict:
        """Запрос встаёт первым: после остановки текущей задачи пользователем он запускается следующим."""
        require_chat(chat_id)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM queued_requests WHERE id=? AND chat_id=? AND owner_id=?", (key, chat_id, local_owner())).fetchone()
            if not row:
                raise HTTPException(404, "Запрос не найден")
            if row["status"] != "queued" or row["version"] != version:
                raise HTTPException(409, "Запрос изменился или уже выполняется")
            first = conn.execute(
                "SELECT MIN(sequence) FROM queued_requests WHERE chat_id=? AND owner_id=? AND status='queued'", (chat_id, local_owner())
            ).fetchone()[0]
            if first is not None and first != row["sequence"]:
                conn.execute("UPDATE queued_requests SET sequence=?,version=version+1 WHERE id=?", (first - 1, key))
        self.interrupted.add(chat_id)
        return self.snapshot(chat_id)

    def pause(self, chat_id: int) -> None:
        with db() as conn:
            conn.execute("INSERT INTO queue_state(chat_id,paused) VALUES (?,1) ON CONFLICT(chat_id) DO UPDATE SET paused=1", (chat_id,))

    def resume(self, chat_id: int) -> dict:
        require_chat(chat_id)
        with db() as conn:
            conn.execute("UPDATE queue_state SET paused=0 WHERE chat_id=?", (chat_id,))
        self.wake(chat_id)
        return self.snapshot(chat_id)

    def recover(self) -> None:
        with db() as conn:
            conn.execute("UPDATE queue_state SET paused=1")
            conn.execute("UPDATE queued_requests SET status='failed',error='Приложение было закрыто; результат нужно проверить' WHERE status='running'")

    def wake(self, chat_id: int) -> None:
        if self.launch is None or (chat_id in self.workers and not self.workers[chat_id].done()):
            return
        task = asyncio.create_task(self.drain(chat_id), name=f"dokky-queue-{chat_id}")
        self.workers[chat_id] = task
        task.add_done_callback(lambda done: self.workers.pop(chat_id, None) if self.workers.get(chat_id) is done else None)

    async def drain(self, chat_id: int) -> None:
        claimed = None
        try:
            owner = local_owner()
            waiting = self.active(chat_id)
            while True:
                if local_owner() != owner or self.snapshot(chat_id)["paused"]:
                    return
                if self.active(chat_id):
                    await asyncio.sleep(0.2)
                    continue
                if waiting and self.last_status(chat_id) != "completed" and chat_id not in self.interrupted:
                    self.pause(chat_id)
                    return
                with db() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT * FROM queued_requests WHERE chat_id=? AND owner_id=? AND status='queued' ORDER BY sequence LIMIT 1", (chat_id, owner)).fetchone()
                    if not row:
                        return
                    chat = require_chat(chat_id)
                    if chat["project_id"] != row["project_id"]:
                        conn.execute("UPDATE queue_state SET paused=1 WHERE chat_id=?", (chat_id,))
                        return
                    conn.execute("UPDATE queued_requests SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'", (row["id"],))
                    claimed = row['id']
                self.interrupted.discard(chat_id)
                status = await self.launch(chat_id, json.loads(row["payload_json"]), row["id"])
                with db() as conn:
                    conn.execute("UPDATE queued_requests SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", ("completed" if status == "completed" else "failed", row["id"]))
                if status != "completed":
                    self.pause(chat_id)
                    return
                waiting = False
                claimed = None
        except asyncio.CancelledError:
            if claimed:
                with db() as conn:
                    conn.execute("UPDATE queued_requests SET status='failed',error='Выполнение прервано; проверьте результат' WHERE id=? AND status='running'",(claimed,))
            raise
        except Exception:
            # ошибка очереди не должна запускать следующий платный запрос
            with db() as conn:
                if claimed:
                    conn.execute("UPDATE queued_requests SET status='failed',error='Не удалось подтвердить результат' WHERE id=? AND status='running'",(claimed,))
                conn.execute('UPDATE queue_state SET paused=1 WHERE chat_id=?',(chat_id,))

    async def shutdown(self) -> None:
        tasks = list(self.workers.values())
        with db() as conn:
            conn.execute("UPDATE queue_state SET paused=1")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.workers.clear()


request_queue = RequestQueue()
