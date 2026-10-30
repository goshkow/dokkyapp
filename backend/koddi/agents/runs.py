from __future__ import annotations

import asyncio
import json
import threading
from time import monotonic
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from koddi.logs import log_event
from koddi.storage.db import db


@dataclass
class ActiveRun:
    chat_id: int
    owner_id: str | None = None
    events: deque[str] = field(default_factory=lambda: deque(maxlen=2048))
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    completed: bool = False
    task: asyncio.Task[None] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    sequence: int = 0
    terminal: bool = False
    live_message: dict | None = None
    revoked: bool = False
    finished_at: float = 0
    event_bytes: int = 0
    turn_id: int | None = None
    last_status: str = ""
    sequenced_messages: set[int] = field(default_factory=set)

    @property
    def first_sequence(self) -> int:
        return self.sequence - len(self.events) + 1

    def reset_event(self, after: int) -> str:
        return (
            "event: stream.reset\ndata: "
            + json.dumps(
                {
                    "chat_id": self.chat_id,
                    "requested_after": after,
                    "first_available": self.first_sequence,
                    "last_sequence": self.sequence,
                    "reason": "cursor_unavailable",
                    "live_message": self.live_message,
                }
            )
            + "\n\n"
        )

    async def publish(self, item: str) -> None:
        if self.completed or self.revoked:
            return
        kind = next((line[7:] for line in item.splitlines() if line.startswith('event: ')), '')
        line = next((line[6:] for line in item.splitlines() if line.startswith('data: ')), '{}')
        payload = json.loads(line)
        if kind == 'turn.started':
            self.turn_id = payload.get('turn_id')
        if self.turn_id and kind in {'agent.status', 'agent.step', 'agent.input_required', 'context.compacting', 'context.compacted', 'tool.progress'}:
            text = payload.get('text') or ('Ожидаю подтверждение' if kind == 'agent.input_required' else 'Думаю…' if kind == 'agent.step' else 'Подготавливаю действие…' if kind == 'tool.progress' else 'Контекст сохранён')
            if text != self.last_status:
                self.last_status = text
                with db() as conn:
                    conn.execute(
                        'INSERT OR IGNORE INTO turn_events(turn_id,sequence,kind,payload_json,created_at) VALUES (?,?,?,?,?)',
                        (self.turn_id, self.sequence + 1, 'agent.status' if kind == 'tool.progress' else kind, json.dumps(payload if kind.startswith('context.') else {'text':text}, ensure_ascii=False), datetime.now(timezone.utc).isoformat(timespec='milliseconds')),
                    )
        message_id = payload.get('message_id')
        if kind in {'message.delta', 'tool.call'} and type(message_id) is int and message_id not in self.sequenced_messages:
            self.sequenced_messages.add(message_id)
            self.last_status = ''
            with db() as conn:
                row = conn.execute('SELECT metadata_json FROM messages WHERE id=? AND chat_id=?', (message_id,self.chat_id)).fetchone()
                if row:
                    metadata = json.loads(row[0] or '{}')
                    metadata['sequence'] = self.sequence + 1
                    conn.execute('UPDATE messages SET metadata_json=? WHERE id=?', (json.dumps(metadata,ensure_ascii=False),message_id))
        if "event: message.reset\n" in item:
            line = next((line[6:] for line in item.splitlines() if line.startswith("data: ")), None)
            payload = json.loads(line) if line else {}
            if payload.get("message_id") is not None:
                self.live_message = {"id": payload["message_id"], "content": ""}
        if "event: message.delta\n" in item:
            data_line = next(
                (line[6:] for line in item.splitlines() if line.startswith("data: ")), None
            )
            if data_line:
                payload = json.loads(data_line)
                if payload.get("message_id") is not None:
                    if (
                        self.live_message is None
                        or self.live_message["id"] != payload["message_id"]
                    ):
                        self.live_message = {"id": payload["message_id"], "content": ""}
                    self.live_message["content"] += payload.get("content", "")
                    if len(self.live_message["content"]) > 2_000_000:
                        self.live_message = None
        self.sequence += 1
        item = "\n".join(line for line in item.split("\n") if not line.startswith("id:"))
        item = f"id: {self.sequence}\n" + item
        self.terminal |= any(
            line in {"event: turn.completed", "event: turn.failed", "event: turn.cancelled"}
            for line in item.splitlines()
        )
        if self.events.maxlen and len(self.events) == self.events.maxlen:
            self.event_bytes -= len(self.events.popleft().encode("utf-8"))
        self.events.append(item)
        self.event_bytes += len(item.encode("utf-8"))
        while len(self.events) > 1 and self.event_bytes > 2_000_000:
            self.event_bytes -= len(self.events.popleft().encode("utf-8"))
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # медленный интерфейс не должен копить весь вывод модели в памяти
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(self.reset_event(0))
                queue.put_nowait(None)
                self.subscribers.discard(queue)

    async def subscribe(self, after: int = 0) -> AsyncIterator[str]:
        if self.revoked:
            return
        if type(after) is not int or after < 0:
            raise ValueError("Курсор событий должен быть неотрицательным целым числом")
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
        reset = after < self.first_sequence - 1 or after > self.sequence
        replay = [
            item
            for sequence, item in enumerate(self.events, self.first_sequence)
            if sequence > after
        ]
        if reset:
            # клиент перечитает сохранённый снимок и подключится заново с last_sequence
            replay = [self.reset_event(after)]
        replay_completed = self.completed
        if not replay_completed:
            self.subscribers.add(queue)
        try:
            for item in replay:
                if self.revoked:
                    return
                yield item
            if replay_completed:
                return
            while True:
                item = await queue.get()
                if item is None or self.revoked:
                    return
                yield item
        finally:
            self.subscribers.discard(queue)

    async def finish(self) -> None:
        if self.completed:
            return
        self.completed = True
        self.finished_at = monotonic()
        for queue in tuple(self.subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(self.reset_event(0))
            queue.put_nowait(None)

    def revoke(self) -> None:
        self.revoked = True
        self.events.clear()
        self.live_message = None
        self.sequenced_messages.clear()
        self.event_bytes = 0
        for queue in tuple(self.subscribers):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self.subscribers.clear()


async def _announce(chat_id: int, active: bool) -> None:
    """Меню чатов показывает, кто работает: окно узнаёт о старте и конце хода из общей шины."""
    from koddi.events import event_bus

    try:
        await event_bus.publish("agent.run.state", {"chat_id": chat_id, "active": active})
    except Exception as exc:  # noqa: BLE001 - уведомление не должно мешать ходу
        log_event("WARNING", "agent.run.announce", type(exc).__name__, {"chat_id": chat_id})


class AgentRunRegistry:
    """Держит ход агента живым, даже если браузер или WebView отключились."""

    def __init__(self) -> None:
        self._runs: dict[int, ActiveRun] = {}
        self._lock = asyncio.Lock()
        # слушатели нового хода (доступ с телефона повторяет ход в вебе); вызываются в цикле событий
        self.listeners: list[Callable[[ActiveRun], None]] = []

    async def start(
        self,
        chat_id: int,
        stream: AsyncIterator[str],
        cancel_event: threading.Event | None = None,
        owner_id: str | None = None,
    ) -> ActiveRun:
        async with self._lock:
            current = self._runs.get(chat_id)
            if current and not current.completed:
                raise RuntimeError("agent turn is already running")
            self.prune()
            run = ActiveRun(chat_id=chat_id, owner_id=owner_id, cancel_event=cancel_event or threading.Event())
            self._runs[chat_id] = run

            async def worker() -> None:
                try:
                    async for item in stream:
                        await run.publish(item)
                except asyncio.CancelledError:
                    run.cancel_event.set()
                    if not run.terminal:
                        await run.publish(
                            "event: turn.cancelled\ndata: "
                            + json.dumps({"chat_id": chat_id}, ensure_ascii=False)
                            + "\n\n"
                        )
                    raise
                except Exception as exc:
                    log_event(
                        "ERROR",
                        "agent.run.failed",
                        str(exc),
                        {"chat_id": chat_id},
                    )
                    await run.publish(
                        "event: turn.failed\ndata: "
                        + json.dumps({"chat_id": chat_id, "error": str(exc)}, ensure_ascii=False)
                        + "\n\n"
                    )
                    self._persist_failed(chat_id, str(exc))
                finally:
                    try:
                        await stream.aclose()
                    finally:
                        await run.finish()
                        await _announce(chat_id, False)

            run.task = asyncio.create_task(worker(), name=f"koddi-agent-{chat_id}")
            await _announce(chat_id, True)
            for listener in tuple(self.listeners):
                try:
                    listener(run)
                except Exception as exc:  # noqa: BLE001 - слушатель не должен мешать ходу
                    log_event("WARNING", "agent.run.listener", type(exc).__name__, {"chat_id": chat_id})
            return run

    def active(self, chat_id: int) -> bool:
        run = self._runs.get(chat_id)
        return bool(run and not run.completed)

    def active_ids(self) -> list[int]:
        return [chat_id for chat_id in self._runs if self.active(chat_id)]

    def prune(self) -> None:
        completed = sorted(
            ((key, run) for key, run in self._runs.items() if run.completed),
            key=lambda item: item[1].finished_at,
            reverse=True,
        )
        for index, (key, run) in enumerate(completed):
            if index >= 32 or monotonic() - run.finished_at > 180:
                run.revoke()
                self._runs.pop(key, None)

    def get(self, chat_id: int, *, owner_id: str | None = None) -> ActiveRun | None:
        self.prune()
        run = self._runs.get(chat_id)
        if run and owner_id is not None and run.owner_id != owner_id:
            return None
        return run

    def forget(self, chat_id: int) -> None:
        run = self._runs.pop(chat_id, None)
        if run:
            run.revoke()

    def forget_completed(self) -> None:
        for chat_id, run in list(self._runs.items()):
            if run.completed:
                self.forget(chat_id)

    async def cancel(self, chat_id: int) -> bool:
        async with self._lock:
            run = self._runs.get(chat_id)
            if not run or run.completed or run.terminal:
                return False
            run.cancel_event.set()
            task = run.task
            if task and not task.done():
                task.cancel()
        if task and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        if not run.terminal:
            await run.publish(
                "event: turn.cancelled\ndata: "
                + json.dumps({"chat_id": chat_id}, ensure_ascii=False)
                + "\n\n"
            )
        await run.finish()
        self._persist_cancelled(chat_id)
        return True

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.cancel(chat_id) for chat_id in self.active_ids()))
        self.forget_completed()

    @staticmethod
    def _persist_cancelled(chat_id: int) -> None:
        with db() as conn:
            turn = conn.execute(
                "SELECT id FROM turns WHERE chat_id=? AND status='running' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if turn:
                conn.execute(
                    "UPDATE turns SET status='cancelled', summary=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    ("Остановлено пользователем", turn["id"]),
                )
            rows = conn.execute(
                "SELECT id, content, metadata_json FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 4",
                (chat_id,),
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except ValueError:
                    continue
                if metadata.get("status") != "running":
                    continue
                metadata["status"] = "cancelled"
                content = row["content"] or ""
                is_activity = "events" in metadata or "thinking" in metadata
                conn.execute(
                    "UPDATE messages SET content=?, metadata_json=? WHERE id=?",
                    (
                        "" if is_activity else content or "Выполнение остановлено пользователем.",
                        json.dumps(metadata, ensure_ascii=False),
                        row["id"],
                    ),
                )

    @staticmethod
    def _persist_failed(chat_id: int, error: str) -> None:
        with db() as conn:
            turn = conn.execute(
                "SELECT id FROM turns WHERE chat_id=? AND status='running' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if turn:
                conn.execute(
                    "UPDATE turns SET status='failed', summary=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (error[:1000], turn["id"]),
                )
            rows = conn.execute(
                "SELECT id, metadata_json FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 4",
                (chat_id,),
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except ValueError:
                    continue
                if metadata.get("status") != "running":
                    continue
                metadata["status"] = "failed"
                is_activity = "events" in metadata or "thinking" in metadata
                conn.execute(
                    "UPDATE messages SET content=?, metadata_json=? WHERE id=?",
                    (
                        "" if is_activity else f"Ошибка агента: {error}",
                        json.dumps(metadata, ensure_ascii=False),
                        row["id"],
                    ),
                )


agent_runs = AgentRunRegistry()
