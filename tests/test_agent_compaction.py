import asyncio
import json
import threading

import pytest

import koddi.storage.db as storage
from koddi.agents.compaction import SUMMARY_PROMPT, summarize_history
from koddi.agents.history import CHECKPOINT, replay_history
from koddi.agents.orchestrator import AgentOrchestrator, responsive_stream
from koddi.agents.prompts import project_instructions


def test_project_instructions_are_not_silently_truncated(tmp_path):
    instructions = "Important instruction. " * 1000 + "FINAL REQUIREMENT: preserve this rule."
    (tmp_path / "AGENTS.md").write_text(instructions, encoding="utf-8")
    (tmp_path / "LOCAL_AGENT.md").write_text("Additional workspace policy.", encoding="utf-8")
    result = project_instructions(tmp_path)
    assert instructions in result
    assert "Additional workspace policy." in result


@pytest.mark.asyncio
async def test_large_history_is_summarized_in_bounded_requests_and_merged():
    calls = []

    class Client:
        async def stream(self, **request):
            calls.append(request)
            assert request["tools"] == []
            assert len(json.dumps(request["messages"], ensure_ascii=False)) <= 8192
            yield {"content": "S" * 1000}

    source = [
        {"role": "system", "content": "Keep these original instructions"},
        {"role": "tool", "content": '"\\\n' * 10000},
    ]
    original = json.dumps(source, ensure_ascii=False)
    events = [
        event
        async for event in summarize_history(
            Client(),
            model="mock",
            messages=source,
            char_budget=8192,
            cancel_event=threading.Event(),
            responsive=responsive_stream,
        )
    ]
    assert len(calls) > 10
    assert len(events[-1]["summary"]) <= 2048
    assert max(event.get("round", 0) for event in events) > 1
    assert json.dumps(source, ensure_ascii=False) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "X" * 20000])
async def test_empty_or_nonreducing_summary_preserves_input(reply):
    class Client:
        async def stream(self, **kwargs):
            yield {"content": reply}

    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "task" * 3000},
    ]
    before = json.dumps(messages)
    with pytest.raises(ValueError):
        async for _ in summarize_history(
            Client(),
            model="mock",
            messages=messages,
            char_budget=8192,
            cancel_event=threading.Event(),
            responsive=responsive_stream,
        ):
            pass
    assert json.dumps(messages) == before


@pytest.mark.asyncio
async def test_cancellation_interrupts_summary_generation():
    closed = False

    class Client:
        async def stream(self, **kwargs):
            nonlocal closed
            try:
                await asyncio.sleep(30)
                yield {"content": "late"}
            finally:
                closed = True

    cancel = threading.Event()

    async def consume():
        async for _ in summarize_history(
            Client(),
            model="mock",
            messages=[{"role": "system"}, {"role": "user", "content": "history"}],
            char_budget=8192,
            cancel_event=cancel,
            responsive=responsive_stream,
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert closed


@pytest.mark.asyncio
async def test_agent_continues_after_chunked_compaction_and_keeps_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "compaction.sqlite3")
    storage.init_db()
    old = {"role": "user", "content": "Old record " * 10000}
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,mode) VALUES ('Long task','chat')").lastrowid
        conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Previous task')", (chat,)
        )
        conn.execute(
            "INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", (chat, json.dumps(old))
        )
        conn.execute(
            "INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)",
            (chat, json.dumps({"role": "assistant", "content": "Recent outcome: test passed"})),
        )
    requests = []

    class Client:
        config = {"context_tokens": 8192, "model": "mock"}

        async def stream(self, **request):
            requests.append(request)
            if request["messages"][0]["content"] == SUMMARY_PROMPT:
                assert len(json.dumps(request["messages"], ensure_ascii=False)) <= 16384
                yield {
                    "content": "Previous work preserved. Next: answer the current user request.",
                    "finish_reason": "stop",
                }
            else:
                assert any(m.get("content") == "Current request" for m in request["messages"])
                assert any(
                    "Recent outcome: test passed" in (m.get("content") or "")
                    for m in request["messages"]
                )
                yield {
                    "content": "Продолжил работу после сохранения контекста.",
                    "finish_reason": "stop",
                }

    events = [
        event
        async for event in AgentOrchestrator(Client()).run(
            chat_id=chat,
            project_id=None,
            workspace=None,
            user_message="Current request",
            mode="chat",
            model="mock",
        )
    ]
    assert any("event: context.compacted" in event for event in events)
    assert any("event: turn.completed" in event for event in events)
    with storage.db() as conn:
        records = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT payload_json FROM agent_history WHERE chat_id=? ORDER BY id", (chat,)
            )
        ]
    assert records[0] == old
    assert any(record.get(CHECKPOINT) == 1 for record in records)
    replay = replay_history(records)
    assert replay[-1]["content"] == "Продолжил работу после сохранения контекста."
    assert len(json.dumps(replay)) < len(json.dumps(records))
