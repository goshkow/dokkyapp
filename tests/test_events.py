from __future__ import annotations

import asyncio

from koddi.events.bus import EventBus


def test_event_bus_payload_and_sse_contracts() -> None:
    async def scenario() -> None:
        bus = EventBus()
        payloads = bus.subscribe_payloads()
        first = asyncio.create_task(payloads.__anext__())
        await asyncio.sleep(0)
        await bus.publish("model.download", {"progress": 0.5})
        assert await first == {"event": "model.download", "data": {"progress": 0.5}}
        await payloads.aclose()

        sse = bus.subscribe()
        line = asyncio.create_task(sse.__anext__())
        await asyncio.sleep(0)
        await bus.publish("health", {"ok": True})
        assert "event: health" in await line
        await sse.aclose()

    asyncio.run(scenario())


def test_revocation_discards_private_pending_events_and_ends_subscription():
    async def scenario():
        bus = EventBus()
        subscription = bus.subscribe_payloads()
        first = asyncio.create_task(anext(subscription))
        await asyncio.sleep(0)
        await bus.publish("ready", {})
        await first
        await bus.publish("private", {"text": "old account"})
        bus.revoke()
        assert await anext(subscription) == {"event": "dokky.auth_revoked", "data": {}}
        assert [event async for event in subscription] == []
        assert not bus._subscribers

    asyncio.run(scenario())
