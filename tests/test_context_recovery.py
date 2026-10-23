import asyncio
import json
import threading
import time

import httpx
import pytest

import koddi.storage.db as storage
import koddi.agents.orchestrator as agent
from koddi.agents.compaction import (
    SUMMARY_PROMPT,
    context_limit,
    context_size,
    needs_compaction,
    summarize_history,
)
from koddi.agents.history import compacted_messages, repair_history
from koddi.dokky import coding
from koddi.dokky.client import API, DokkyClient
from koddi.remote.client import ContextLimitError, RemoteAIClient, RemoteAIError


SID = "cs_" + "c" * 24


@pytest.mark.asyncio
async def test_truncated_checkpoint_is_retried_with_smaller_fragments_not_committed():
    sizes = []

    class Model:
        async def stream(self, **request):
            sizes.append(len(request["messages"][-1]["content"]))
            if len(sizes) == 1:
                yield {"content": "INCOMPLETE CHECKPOINT", "finish_reason": "length"}
            else:
                yield {"content": "Verified state and next step", "finish_reason": "stop"}

    events = [
        event
        async for event in summarize_history(
            Model(),
            model="test",
            messages=[
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "evidence " * 1000},
            ],
            char_budget=8192,
            cancel_event=threading.Event(),
            responsive=agent.responsive_stream,
        )
    ]
    assert sizes[1] < sizes[0]
    assert "INCOMPLETE" not in events[-1]["summary"]
    assert "Verified state" in events[-1]["summary"]


@pytest.mark.asyncio
async def test_permanently_truncated_checkpoint_has_bounded_recovery():
    calls = []

    class Model:
        async def stream(self, **request):
            calls.append(request)
            yield {"content": "Incomplete", "finish_reason": "length"}

    with pytest.raises(ValueError, match="исходная история сохранена"):
        _ = [event async for event in summarize_history(
            Model(), model="test", messages=[{"role": "system", "content": "rules"}, {"role": "user", "content": "evidence " * 1000}],
            char_budget=8192, cancel_event=threading.Event(), responsive=agent.responsive_stream,
        )]
    assert len(calls) == 5


class Store:
    def load(self):
        return {"access_token": "test", "refresh_token": "test", "expires_at": time.time() + 900}

    def clear(self):
        pass


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "context.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "test-user"})
    with storage.db() as conn:
        chat_id = conn.execute(
            "INSERT INTO chats(title,mode) VALUES ('Context','development')"
        ).lastrowid
        conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Earlier task')",
            (chat_id,),
        )
    return chat_id


def session():
    return {
        "id": SID,
        "reasoning_mode": "light",
        "version": 1,
        "history": {"length": 0, "hash": ""},
    }


def answer(text, length=3):
    frames = [
        {"choices": [{"delta": {"content": text}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {
            "choices": [],
            "usage": {"context_tokens_limit": 200000},
            "dokky": {"history": {"length": length, "hash": "stored"}},
        },
    ]
    return httpx.Response(
        200,
        text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        + "data: [DONE]\n\n",
    )


def client_for(handler, monkeypatch):
    transport = httpx.MockTransport(handler)
    account = DokkyClient(transport=transport, store=Store())
    monkeypatch.setattr("koddi.dokky.client.dokky", account)
    return RemoteAIClient(
        {"provider": "dokky", "model": "dokky-code", "base_url": API, "context_tokens": 200000},
        transport=transport,
    )


def test_context_ceiling_respects_real_model_and_tools():
    assert context_limit({"context_tokens": 1000000}) == 600000
    assert context_limit({"context_tokens": 200000}) == 200000
    assert context_limit({"context_tokens": 600000, "context_tokens_limit": 100000}) == 100000
    tools = [{"function": {"description": "x" * 25000}}]
    messages = [{"role": "user", "content": "task"}]
    assert needs_compaction(messages, tools, {"provider": "dokky", "context_tokens": 8192})
    assert not needs_compaction(messages, [], {"provider": "dokky", "context_tokens": 8192})


def test_empty_assistant_repair_keeps_tool_calls_and_original():
    source = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": "  "},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "one"}]},
        {"role": "tool", "tool_call_id": "one", "content": "done"},
    ]
    result = repair_history(source)
    assert result == [source[0], source[3], source[4]]
    assert len(source) == 5


def test_checkpoint_keeps_latest_rules_and_recent_complete_exchanges():
    user = {"role": "user", "content": "Current task"}
    developer = {"role": "developer", "content": "Full current project instructions"}
    tail = []
    for index in range(3):
        tail.extend(
            [
                {"role": "assistant", "content": None, "tool_calls": [{"id": str(index)}]},
                {
                    "role": "tool",
                    "tool_call_id": str(index),
                    "content": "Verified result " + str(index),
                },
            ]
        )
    source = [
        {"role": "system", "content": "Stable rules"},
        {"role": "user", "content": "Old data " * 10000},
        developer,
        user,
        *tail,
    ]
    result = compacted_messages(
        source, "Project decisions and pending work", user, [], max_chars=4000
    )
    assert developer in result
    assert result[-6:] == tail
    assert sum(message == user for message in result) == 1


def test_checkpoint_does_not_move_old_developer_rules_after_current_rules():
    user = {"role": "user", "content": "Review only"}
    current = {"role": "developer", "content": "CURRENT: review-only, no writes"}
    source = [
        {"role": "system", "content": "Stable application policy"},
        {"role": "user", "content": "Old state " * 10000},
        user,
        {"role": "assistant", "content": "First exchange"},
        {"role": "developer", "content": "OLD: development, edit files"},
        {"role": "assistant", "content": "Second exchange"},
        current,
        {"role": "assistant", "content": "Third exchange"},
    ]
    compacted = compacted_messages(source, "Only review remains", user, [])
    assert [message for message in compacted if message["role"] == "developer"] == [current]
    assert [message for message in compacted if message["role"] == "system"] == [source[0]]


def test_dokky_checkpoint_cannot_replace_terminal_tool_exchange_with_user():
    user = {"role": "user", "content": "Current task"}
    source = [
        {"role": "system", "content": "Rules"},
        user,
        {"role": "assistant", "content": None, "tool_calls": [{"id": "large"}]},
        {"role": "tool", "tool_call_id": "large", "content": "x" * 20000},
    ]
    with pytest.raises(ValueError, match="не помещаются"):
        compacted_messages(
            source, "Actual task state", user, [], max_chars=2000, preserve_terminal_exchange=True
        )
    continued = {"role": "user", "content": "Continue the task"}
    compacted = compacted_messages(
        [*source, continued],
        "Actual task state",
        continued,
        [],
        max_chars=2000,
        preserve_terminal_exchange=True,
    )
    assert compacted[-1] == continued
    assert source[-1]["role"] == "tool" and len(source[-1]["content"]) == 20000


@pytest.mark.asyncio
async def test_completed_empty_reply_does_not_poison_next_request(chat, monkeypatch):
    seen = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        body = json.loads(request.content)
        seen.append((request.headers["Idempotency-Key"], body))
        return answer("" if len(seen) == 1 else "Готово")

    client = client_for(handler, monkeypatch)
    messages = [{"role": "system", "content": "Agent"}, {"role": "user", "content": "Task"}]
    with pytest.raises(RemoteAIError, match="без текста"):
        _ = [
            event
            async for event in client.stream(
                model="dokky-code", messages=messages, tools=[], options={"_chat_id": chat}
            )
        ]
    value = coding.load(chat)
    assert value["messages"] == messages and value["history"] is None
    _ = [
        event
        async for event in client.stream(
            model="dokky-code",
            messages=messages + [{"role": "user", "content": "Continue"}],
            tools=[],
            options={"_chat_id": chat},
        )
    ]
    assert len(seen) == 2 and seen[0][0] != seen[1][0]
    assert "history" not in seen[1][1]
    assert coding.load(chat)["messages"][-1]["content"] == "Готово"


@pytest.mark.asyncio
async def test_legacy_empty_canonical_reply_uses_full_replacement(chat, monkeypatch):
    seen = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        seen.append(json.loads(request.content))
        return answer("Recovered")

    client = client_for(handler, monkeypatch)
    value = await coding.ensure(chat)
    value.update(
        messages=[{"role": "user", "content": "Old"}, {"role": "assistant", "content": None}],
        history={"length": 2, "hash": "broken"},
    )
    coding.save(chat, value)
    _ = [
        event
        async for event in client.stream(
            model="dokky-code",
            messages=value["messages"] + [{"role": "user", "content": "Continue"}],
            tools=[],
            options={"_chat_id": chat},
        )
    ]
    assert "history" not in seen[0]
    assert [message["role"] for message in seen[0]["messages"]] == ["user", "user"]


@pytest.mark.asyncio
async def test_pending_invalid_empty_request_is_checked_before_repair(chat, monkeypatch):
    seen = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        body = json.loads(request.content)
        seen.append((request.headers["Idempotency-Key"], body))
        if len(seen) == 1:
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "validation_error",
                        "message": "messages[1]: пустое сообщение.",
                        "retryable": False,
                    }
                },
            )
        return answer("Recovered")

    client = client_for(handler, monkeypatch)
    await coding.ensure(chat)
    messages = [
        {"role": "user", "content": "Old"},
        {"role": "assistant", "content": None},
        {"role": "user", "content": "Continue"},
    ]
    body = {
        "model": "dokky-code",
        "session_id": SID,
        "messages": messages,
        "stream": True,
        "store": False,
    }
    coding.save_pending(chat, "original-key", body, messages)
    _ = [
        event
        async for event in client.stream(
            model="dokky-code", messages=messages, tools=[], options={"_chat_id": chat}
        )
    ]
    assert seen[0] == ("original-key", body)
    assert seen[1][0] != "original-key"
    assert len(seen[1][1]["messages"]) == 2 and coding.pending(chat) is None


@pytest.mark.asyncio
async def test_pending_empty_repair_on_last_network_attempt_is_actually_sent(chat, monkeypatch):
    seen = []

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr("koddi.remote.client.asyncio.sleep", no_wait)

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        body = json.loads(request.content)
        seen.append((request.headers["Idempotency-Key"], body))
        if len(seen) < 4:
            raise httpx.ConnectError("Test disconnection", request=request)
        if len(seen) == 4:
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "validation_error",
                        "message": "messages[1]: пустое сообщение.",
                        "retryable": False,
                    }
                },
            )
        return answer("Recovered")

    client = client_for(handler, monkeypatch)
    await coding.ensure(chat)
    messages = [
        {"role": "user", "content": "Old"},
        {"role": "assistant", "content": None},
        {"role": "user", "content": "Continue"},
    ]
    body = {
        "model": "dokky-code",
        "session_id": SID,
        "messages": messages,
        "stream": True,
        "store": False,
    }
    coding.save_pending(chat, "original-key", body, messages)
    chunks = [
        event
        async for event in client.stream(
            model="dokky-code", messages=messages, tools=[], options={"_chat_id": chat}
        )
    ]
    assert [key for key, _ in seen[:4]] == ["original-key"] * 4
    assert len(seen) == 5 and seen[-1][0] != "original-key"
    assert seen[-1][1]["messages"] == [messages[0], messages[2]]
    assert any(event.get("content") == "Recovered" for event in chunks)
    assert coding.pending(chat) is None


@pytest.mark.asyncio
async def test_context_validation_is_not_blindly_retried(chat, monkeypatch):
    seen = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        seen.append(request)
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "validation_error",
                    "message": "Контекст около 200386 токенов превышает лимит 200000.",
                    "retryable": False,
                }
            },
        )

    client = client_for(handler, monkeypatch)
    with pytest.raises(ContextLimitError) as error:
        _ = [
            event
            async for event in client.stream(
                model="dokky-code",
                messages=[{"role": "user", "content": "Task"}],
                tools=[],
                options={"_chat_id": chat},
            )
        ]
    assert error.value.limit == 200000
    assert len(seen) == 1 and coding.pending(chat) is None


async def setup_canonical(chat, monkeypatch, content):
    canonical = {
        "session": session(),
        "messages": [
            {"role": "system", "content": "Stable rules"},
            {"role": "user", "content": content},
            {"role": "assistant", "content": "Recent verified outcome"},
        ],
        "tools": [],
        "history": {"length": 3, "hash": "old"},
    }

    coding.save(chat, canonical)

    class Account:
        async def request(self, method, path, **kwargs):
            if path == '/chat/completions':
                assert kwargs['json']['purpose']=='compaction'
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'Verified old work; continue current task.'}}]})
            assert path == '/coding/sessions'
            return httpx.Response(201,json={**session(),'id':'cs_'+'f'*24})

    monkeypatch.setattr(coding, 'account_client', lambda: Account())
    monkeypatch.setattr(coding, "pending", lambda _: None)
    with storage.db() as conn:
        for message in canonical["messages"]:
            conn.execute(
                "INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)",
                (chat, json.dumps(message)),
            )
    return canonical


@pytest.mark.asyncio
async def test_dokky_compacts_before_request_and_continues_same_chat(chat, monkeypatch):
    canonical = await setup_canonical(chat, monkeypatch, "Historical evidence " * 12000)
    original = json.dumps(canonical)
    main_requests = []

    class Model:
        config = {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000,
                  "models":[{"id":"dokky-code","reasoning_modes":[{"id":"light","compact_at_tokens":50000}]}]}

        async def stream(self, **request):
            if request["messages"][0]["content"] == SUMMARY_PROMPT:
                yield {
                    "content": "Project: verified old work. Continue the current request. Recent verified outcome.",
                    "finish_reason": "stop",
                }
            else:
                main_requests.append(request)
                assert (
                    context_size(request["messages"], request["tools"], dokky=True)
                    <= 32768 * 0.3 + 2
                )
                yield {"content": "Готово", "finish_reason": "stop"}

    events = [
        event
        async for event in agent.AgentOrchestrator(Model()).run(
            chat_id=chat,
            project_id=None,
            workspace=None,
            user_message="Continue current task",
            mode="chat",
        )
    ]
    assert any("event: context.compacted" in event for event in events)
    assert "turn.completed" in events[-1] and len(main_requests) == 1
    assert json.dumps(canonical) == original
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM agent_history WHERE chat_id=?", (chat,)).fetchone()[
                0
            ]
            > 3
        )


@pytest.mark.asyncio
async def test_mid_task_compaction_does_not_repeat_completed_tool(chat, tmp_path, monkeypatch):
    await setup_canonical(chat, monkeypatch, "Historical evidence " * 3000)
    executed = []

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, name, arguments, **kwargs):
            executed.append((name, arguments))
            return {"ok": True, "path": arguments["path"]}

    monkeypatch.setattr(agent, "ToolRunner", Runner)

    class Model:
        config = {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000}
        requests = 0

        async def stream(self, **request):
            if request["messages"][0]["content"] == SUMMARY_PROMPT:
                yield {
                    "content": "The tool already created file.py successfully. Do not repeat it. Continue verification.",
                    "finish_reason": "stop",
                }
                return
            self.requests += 1
            if self.requests == 1:
                yield {
                    "tool_call": {
                        "id": "create-once",
                        "name": "file.create",
                        "arguments": '{"path":"file.py","content":"print(1)"}',
                    },
                    "finish_reason": "tool_calls",
                }
            elif self.requests == 2:
                raise ContextLimitError("Context rejected before generation", 32768)
            else:
                assert any(
                    message.get("tool_call_id") == "create-once" for message in request["messages"]
                )
                yield {"content": "Готово", "finish_reason": "stop"}

    model = Model()
    events = [
        event
        async for event in agent.AgentOrchestrator(model).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Create file.py",
            mode="development",
        )
    ]
    assert "turn.completed" in events[-1]
    assert any("event: context.compacted" in event for event in events)
    assert executed == [("file.create", {"path": "file.py", "content": "print(1)"})]
    assert model.requests == 3


@pytest.mark.asyncio
async def test_stop_during_compaction_preserves_original_history(chat, monkeypatch):
    canonical = await setup_canonical(chat, monkeypatch, "Historical evidence " * 12000)
    started = asyncio.Event()
    cancel = threading.Event()
    closed = []

    class Account:
        async def request(self, method, path, **kwargs):
            try:
                started.set()
                await asyncio.sleep(30)
            finally:
                closed.append(True)

    monkeypatch.setattr(coding,'account_client',lambda: Account())

    class Model:
        config = {"provider":"dokky","model":"dokky-code","context_tokens":200000,
                  "models":[{"id":"dokky-code","reasoning_modes":[{"id":"light","compact_at_tokens":50000}]}]}

    async def consume():
        return [
            event
            async for event in agent.AgentOrchestrator(Model()).run(
                chat_id=chat,
                project_id=None,
                workspace=None,
                user_message="Continue",
                mode="chat",
                cancel_event=cancel,
            )
        ]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 2)
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert closed == [True]
    with storage.db() as conn:
        records = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT payload_json FROM agent_history WHERE chat_id=? ORDER BY id", (chat,)
            )
        ]
        assert (
            conn.execute(
                "SELECT status FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat,)
            ).fetchone()[0]
            == "cancelled"
        )
    assert records[:3] == canonical["messages"]
    assert not any(record.get("reason") == "compaction" for record in records)


@pytest.mark.asyncio
async def test_interrupted_transport_after_checkpoint_replays_exact_pending_request(
    chat, monkeypatch
):
    started = asyncio.Event()
    seen = []
    healthy = False

    async def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        seen.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        if not healthy:
            started.set()
            await asyncio.sleep(30)
        return answer("Continued from checkpoint")

    client = client_for(handler, monkeypatch)
    messages = [
        {"role": "system", "content": "Agent"},
        {"role": "user", "content": "Continuation checkpoint: file.py was already created"},
    ]

    async def consume(source):
        return [
            event
            async for event in client.stream(
                model="dokky-code", messages=source, tools=[], options={"_chat_id": chat}
            )
        ]

    task = asyncio.create_task(consume(messages))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pending = coding.pending(chat)
    assert pending["messages"] == messages
    healthy = True
    output = await consume(
        messages + [{"role": "user", "content": "New intent stays outside the pending request"}]
    )
    assert seen[0] == seen[1]
    assert any(event.get("content") == "Continued from checkpoint" for event in output)
    assert coding.pending(chat) is None
