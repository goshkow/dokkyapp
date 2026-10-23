import asyncio
import threading
import time

import pytest

from koddi.agents.orchestrator import execute_tool_events


@pytest.mark.asyncio
async def test_long_tool_emits_live_status_before_result():
    class Runner:
        def run(self, name, args, *, approved=False):
            time.sleep(1.25)
            return {"ok": True}

    updates = [item async for item in execute_tool_events(Runner(), "test", {}, threading.Event())]
    assert updates[0]["waiting_seconds"] >= 1
    assert updates[-1] == {"result": {"ok": True}}


@pytest.mark.asyncio
async def test_tool_heartbeat_cancellation_waits_for_worker():
    cancel = threading.Event()
    settled = threading.Event()

    class Runner:
        def run(self, name, args, *, approved=False):
            cancel.wait(10)
            settled.set()
            return {"ok": False, "cancelled": True}

    async def consume():
        async for _ in execute_tool_events(Runner(), "test", {}, cancel):
            pass

    pending = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, timeout=2)
    assert cancel.is_set() and settled.is_set()
