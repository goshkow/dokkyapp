import asyncio

import pytest

from koddi.agents.runs import ActiveRun, AgentRunRegistry


@pytest.mark.asyncio
async def test_retried_message_replaces_live_reconnect_snapshot():
    run = ActiveRun(chat_id=777)
    await run.publish('event: message.delta\ndata: {"message_id":5,"content":"Старое"}\n\n')
    await run.publish('event: message.reset\ndata: {"message_id":5}\n\n')
    await run.publish('event: message.delta\ndata: {"message_id":5,"content":"Новое"}\n\n')
    assert run.live_message == {"id": 5, "content": "Новое"}


@pytest.mark.asyncio
async def test_replay_cursor_and_closed_subscriber_do_not_cancel_run():
    registry = AgentRunRegistry()
    gate = asyncio.Event()

    async def stream():
        yield 'event: message.delta\ndata: {"content":"first"}\n\n'
        await gate.wait()
        yield "event: turn.completed\ndata: {}\n\n"

    run = await registry.start(919191, stream())
    await asyncio.sleep(0)
    subscriber = run.subscribe()
    first = await anext(subscriber)
    assert "id: 1" in first
    await subscriber.aclose()
    assert registry.active(919191)
    gate.set()
    await run.task
    events = [event async for event in run.subscribe(after=1)]
    assert len(events) == 1
    assert "turn.completed" in events[0]


@pytest.mark.asyncio
async def test_old_cursor_requests_snapshot_reset():
    run = ActiveRun(chat_id=77)
    for _ in range(2050):
        await run.publish("event: agent.step\ndata: {}\n\n")
    subscriber = run.subscribe(after=0)
    event = await anext(subscriber)
    assert "stream.reset" in event
    await subscriber.aclose()
    await run.finish()


@pytest.mark.asyncio
async def test_stream_reset_retains_current_text_prefix():
    import json

    run = ActiveRun(chat_id=78)
    await run.publish('event: message.delta\ndata: {"message_id":5,"content":"Начало "}\n\n')
    await run.publish('event: message.delta\ndata: {"message_id":5,"content":"ответа"}\n\n')
    data = json.loads(run.reset_event(0).split("data: ", 1)[1])
    assert data["live_message"] == {"id": 5, "content": "Начало ответа"}
