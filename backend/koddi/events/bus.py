from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    async def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        payload = {"event": event, "data": data or {}}
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
            self._subscribers.discard(queue)

    def revoke(self, event: str = 'dokky.auth_revoked') -> None:
        for queue in tuple(self._subscribers):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({'event':event,'data':{}})
            queue.put_nowait(None)
        self._subscribers.clear()

    async def subscribe(self) -> AsyncIterator[str]:
        async for payload in self.subscribe_payloads():
            yield "event: {event}\ndata: {data}\n\n".format(
                event=payload["event"],
                data=json.dumps(payload["data"], ensure_ascii=False, default=str),
            )

    async def subscribe_payloads(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    return
                yield payload
        finally:
            self._subscribers.discard(queue)


event_bus = EventBus()
