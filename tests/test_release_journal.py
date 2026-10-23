import json

import httpx
import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.runs import ActiveRun
from koddi.api import create_app
from koddi.remote import RemoteAIClient
from koddi.remote.client import RemoteAIError


@pytest.fixture
def journal_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "journal.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title) VALUES ('journal')").lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
    return chat, turn


@pytest.mark.asyncio
async def test_statuses_are_durable_ordered_and_deduplicated(journal_chat):
    chat, turn = journal_chat
    run = ActiveRun(chat_id=chat)
    await run.publish(f'event: turn.started\ndata: {{"turn_id":{turn}}}\n\n')
    for text in ("Изучаю проект", "Изучаю проект", "Проверяю результат"):
        await run.publish("event: agent.status\ndata: " + json.dumps({"text": text}) + "\n\n")
    with storage.db() as conn:
        events = conn.execute("SELECT * FROM turn_events ORDER BY sequence").fetchall()
    assert [json.loads(event["payload_json"])["text"] for event in events] == [
        "Изучаю проект",
        "Проверяю результат",
    ]
    assert all(event["created_at"] for event in events)


def test_local_history_is_paginated_without_losing_export(journal_chat):
    chat, turn = journal_chat
    with storage.db() as conn:
        conn.executemany(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'user',?)",
            [(chat, str(index)) for index in range(205)],
        )
    client = TestClient(create_app())
    latest = client.get(f"/api/chats/{chat}/messages").json()
    assert len(latest) == 200 and latest[0]["content"] == "5"
    older = client.get(f"/api/chats/{chat}/messages?before={latest[0]['id']}").json()
    assert len(older) == 5
    assert len(client.get(f"/api/chats/{chat}/export").json()["messages"]) == 205


@pytest.mark.asyncio
async def test_completed_empty_response_is_not_retried():
    storage.init_db()
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            headers={"x-request-id": "request-empty"},
        )

    client = RemoteAIClient(
        {"base_url": "https://example.test/v1"}, transport=httpx.MockTransport(server)
    )
    with pytest.raises(RemoteAIError) as error:
        _ = [part async for part in client.stream(model="test", messages=[], tools=[])]
    assert error.value.code == "empty_response"
    assert error.value.request_id == "request-empty"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_setup_statuses_are_bound_to_turn_before_model_starts(
    journal_chat, tmp_path, monkeypatch
):
    from koddi.agents.orchestrator import AgentOrchestrator

    chat, _ = journal_chat

    async def discover():
        return [], {}, []

    monkeypatch.setattr("koddi.agents.orchestrator.discover_agent_tools", discover)
    monkeypatch.setattr("koddi.integrations.mcp.mcp_config_path", lambda: tmp_path / "missing.json")

    class Model:
        async def stream(self, **request):
            yield {"content": "Готово", "finish_reason": "stop"}

    run = ActiveRun(chat_id=chat)
    frames = []
    async for frame in AgentOrchestrator(Model()).run(
        chat_id=chat,
        project_id=None,
        workspace=tmp_path,
        user_message="Inspect",
        mode="development",
        model="test",
    ):
        frames.append(frame)
        await run.publish(frame)
    assert frames[0].startswith("event: turn.started")
    with storage.db() as conn:
        events = conn.execute(
            "SELECT payload_json FROM turn_events WHERE turn_id=? ORDER BY sequence", (run.turn_id,)
        ).fetchall()
    texts = [json.loads(event[0])["text"] for event in events]
    assert texts[0] == "Думаю…"
    assert "Подключение внешних инструментов" in texts


@pytest.mark.asyncio
async def test_partial_tool_generation_survives_stop_without_storing_arguments(journal_chat):
    chat, turn = journal_chat
    run = ActiveRun(chat_id=chat)
    await run.publish(f'event: turn.started\ndata: {{"turn_id":{turn}}}\n\n')
    for size in range(10):
        payload = {"id": "partial-call", "name": "file.create", "bytes_received": size * 160, "arguments": "PRIVATE"}
        await run.publish("event: tool.progress\ndata: " + json.dumps(payload) + "\n\n")
    await run.publish('event: turn.cancelled\ndata: {}\n\n')
    with storage.db() as conn:
        events = conn.execute("SELECT kind,payload_json FROM turn_events WHERE turn_id=?", (turn,)).fetchall()
    assert len(events) == 1
    assert events[0]["kind"] == "agent.status"
    assert json.loads(events[0]["payload_json"])["text"] == "Подготавливаю действие…"
    assert "PRIVATE" not in events[0]["payload_json"]
