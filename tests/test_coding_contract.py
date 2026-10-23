import json
import time

import httpx
import pytest

import koddi.storage.db as storage
from koddi.dokky.client import API, DokkyClient, DokkyError
from koddi.dokky import coding
from koddi.remote.client import RemoteAIClient


class Store:
    def load(self):
        return {"access_token": "test", "refresh_token": "refresh", "expires_at": time.time() + 900}

    def clear(self):
        pass


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "contract.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "test-user"})
    with storage.db() as conn:
        return conn.execute("INSERT INTO chats(title,mode) VALUES ('Тест','development')").lastrowid


def session(**kwargs):
    return {
        "id": "cs_" + "a" * 24,
        "reasoning_mode": "pro",
        "version": 1,
        "history": {"length": 0, "hash": ""},
        **kwargs,
    }


def stream_response(text="Готово", metadata=None):
    frames = [
        {"choices": [{"index": 0, "delta": {"content": text}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {
            "choices": [],
            "usage": {"prompt_tokens": 5},
            "dokky": metadata or {"history": {"length": 3, "hash": "hash"}},
        },
    ]
    return httpx.Response(
        200,
        text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        + "data: [DONE]\n\n",
    )


@pytest.mark.asyncio
async def test_sessions_delta_metadata_and_account_isolation(chat, monkeypatch):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request, body))
        assert request.headers["Authorization"] == "Bearer test"
        assert request.headers["Idempotency-Key"]
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        assert body["session_id"] == session()["id"]
        assert body["reasoning_mode"] == "pro" and body["store"] is False
        assert "reasoning_effort" not in body
        return stream_response()

    transport = httpx.MockTransport(handler)
    account = DokkyClient(transport=transport, store=Store())
    monkeypatch.setattr("koddi.dokky.client.dokky", account)
    client = RemoteAIClient({"provider": "dokky", "base_url": API}, transport=transport)
    messages = [{"role": "system", "content": "Agent"}, {"role": "user", "content": "Test"}]
    events = [
        e
        async for e in client.stream(
            model="ignored", messages=messages, tools=[], options={"_chat_id": chat}
        )
    ]
    assert any("usage" in event for event in events)
    value = coding.load(chat)
    assert value["messages"][-1] == {"role": "assistant", "content": "Готово"}
    assert value["history"] == {"length": 3, "hash": "hash"}
    next_messages = value["messages"] + [{"role": "user", "content": "Next"}]
    _ = [
        e
        async for e in client.stream(
            model="dokky-code", messages=next_messages, tools=[], options={"_chat_id": chat}
        )
    ]
    body = seen[-1][1]
    assert body["messages"] == next_messages[-1:]
    assert body["history"] == {"base_length": 3, "base_hash": "hash"}
    assert len([r for r, _ in seen if r.url.path.endswith("/coding/sessions")]) == 1
    storage.set_setting("dokky_user", {"id": "other"})
    assert coding.load(chat) is None
    with pytest.raises(DokkyError, match="другому аккаунту"):
        await coding.ensure(chat)


@pytest.mark.asyncio
async def test_history_mismatch_and_retry_keep_safe_idempotency(chat, monkeypatch):
    seen = []
    saved = [{"role": "user", "content": "Old"}, {"role": "assistant", "content": "Done"}]

    def handler(request):
        if request.method == "GET":
            if request.url.path.endswith("/history"):
                return httpx.Response(200, json={"items": [{"message": item} for item in saved], "next_after": None})
            return httpx.Response(200, json=session(history={"length": 2, "hash": "recovered"}))
        body = json.loads(request.content)
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        seen.append((request.headers["Idempotency-Key"], body))
        if len(seen) == 1:
            return httpx.Response(
                409, json={"error": {"code": "history_mismatch", "retryable": False}}
            )
        if len(seen) == 2:
            return httpx.Response(
                503,
                json={
                    "error": {
                        "code": "upstream_unavailable",
                        "retryable": True,
                        "retry_after_seconds": 0,
                    }
                },
            )
        return stream_response()

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("koddi.dokky.client.dokky", DokkyClient(transport=transport, store=Store()))
    value = await coding.ensure(chat)
    value.update(
        messages=[{"role": "user", "content": "Old"}, {"role": "assistant", "content": "Done"}],
        history={"length": 2, "hash": "old"},
    )
    coding.save(chat, value)
    messages = value["messages"] + [{"role": "user", "content": "New"}]
    client = RemoteAIClient({"provider": "dokky", "base_url": API}, transport=transport)
    _ = [
        e
        async for e in client.stream(
            model="dokky-code", messages=messages, tools=[], options={"_chat_id": chat}
        )
    ]
    assert seen[0][1]["messages"] == messages[-1:]
    assert seen[1][1]["history"] == {"base_length": 2, "base_hash": "recovered"}
    assert seen[1][1]["messages"] == messages[-1:]
    assert seen[0][0] != seen[1][0] and seen[1] == seen[2]


@pytest.mark.asyncio
async def test_mode_switch_confirmation_and_version_retry(chat, monkeypatch):
    patches = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json=session(history={"length": 2, "hash": "h"}))
        if request.method == "GET":
            return httpx.Response(200, json=session(version=3, history={"length": 2, "hash": "h"}))
        body = json.loads(request.content)
        patches.append(body)
        if len(patches) == 1:
            return httpx.Response(409, json={"error": {"code": "conflict"}})
        return httpx.Response(200, json=session(version=4, reasoning_mode="max"))

    client = DokkyClient(transport=httpx.MockTransport(handler), store=Store())
    with pytest.raises(DokkyError) as error:
        await coding.switch_mode(chat, "max", False, client)
    assert str(error.value) == coding.MODE_WARNING and not patches
    # в диалоге с историей режим только запоминается: ни одного запроса к серверу
    result = await coding.switch_mode(chat, "max", True, client)
    assert result["reasoning_mode"] == "pro" and result["pending_reasoning_mode"] == "max"
    assert not patches
    # промпт глубины работы следует за режимом, с которым уйдёт следующее сообщение
    assert coding.turn_mode(chat) == "max"
    # возврат к прежнему режиму снимает отложенную смену сразу и без подтверждения
    result = await coding.switch_mode(chat, "pro", False, client)
    assert "pending_reasoning_mode" not in result and not patches
    assert coding.turn_mode(chat) == "pro"
    await coding.switch_mode(chat, "max", True, client)
    # смена выполняется со следующим сообщением
    events = [event async for event in coding.apply_pending_mode(chat, client=client)]
    assert events and events[0]["event"] == "agent.status"
    assert coding.load(chat)["session"]["reasoning_mode"] == "max"
    assert "pending_reasoning_mode" not in coding.load(chat)["session"]
    assert [body["expected_version"] for body in patches] == [1, 3]
    assert all(body["acknowledge_cache_cost"] for body in patches)
    with pytest.raises(DokkyError):
        await coding.switch_mode(chat, "low", True, client)


def test_logout_removes_account_state_but_keeps_local_chats_and_project_files(chat, tmp_path):
    project = tmp_path / "project.py"
    project.write_text("print('keep')")
    with storage.db() as conn:
        conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'user','private')", (chat,)
        )
        conn.execute("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,'{}')", (chat,))
    coding.clear_local_account_data()
    with storage.db() as conn:
        assert conn.execute("SELECT count(*) FROM chats").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM agent_history").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM coding_sessions").fetchone()[0] == 0
    assert project.read_text() == "print('keep')"


def test_local_chats_are_adopted_by_the_next_account(chat):
    coding.adopt_local_data("another-user")
    with storage.db() as conn:
        assert conn.execute("SELECT owner_id FROM chats WHERE id=?", (chat,)).fetchone()[0] == "another-user"


@pytest.mark.asyncio
async def test_pending_step_survives_transport_failure_and_client_recreation(chat, monkeypatch):
    requests = []
    healthy = False

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        requests.append((request.headers["Idempotency-Key"], request.content))
        if not healthy:
            raise httpx.ConnectError("Нет сети")
        return stream_response()

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr("koddi.remote.client.asyncio.sleep", no_wait)
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("koddi.dokky.client.dokky", DokkyClient(transport=transport, store=Store()))
    args = dict(
        model="dokky-code",
        messages=[{"role": "user", "content": "Исходная задача"}],
        tools=[],
        options={"_chat_id": chat},
    )
    with pytest.raises(RuntimeError, match="Соединение"):
        _ = [
            e
            async for e in RemoteAIClient(
                {"provider": "dokky", "base_url": API}, transport=transport
            ).stream(**args)
        ]
    saved = coding.pending(chat)
    assert saved and len(requests) == 4
    healthy = True
    _ = [
        e
        async for e in RemoteAIClient(
            {"provider": "dokky", "base_url": API}, transport=transport
        ).stream(**args)
    ]
    assert len(set(requests)) == 1
    assert coding.pending(chat) is None


@pytest.mark.asyncio
async def test_eof_after_finish_cannot_commit_or_execute_tool(chat, monkeypatch):
    attempts = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        attempts.append(request.headers["Idempotency-Key"])
        frames = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "file__create",
                                        "arguments": '{"path":"test.py","content":"print(1)"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        return httpx.Response(200, text="".join("data: " + json.dumps(f) + "\n\n" for f in frames))

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr("koddi.remote.client.asyncio.sleep", no_wait)
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("koddi.dokky.client.dokky", DokkyClient(transport=transport, store=Store()))
    client = RemoteAIClient({"provider": "dokky", "base_url": API}, transport=transport)
    events = []
    with pytest.raises(RuntimeError, match="Соединение"):
        async for event in client.stream(
            model="dokky-code",
            messages=[{"role": "user", "content": "Создай файл"}],
            tools=[],
            options={"_chat_id": chat},
        ):
            events.append(event)
    assert not any("tool_call" in event for event in events)
    assert coding.load(chat)["messages"] == []
    assert len(attempts) == 4 and len(set(attempts)) == 1


def test_unconfirmed_user_request_is_not_lost():
    canonical = [
        {"role": "user", "content": "Старая задача"},
        {"role": "assistant", "content": "Готово"},
    ]
    tail = [{"role": "user", "content": "Важная прерванная задача"}]
    assert coding.unconfirmed_tail(canonical, canonical + tail) == tail


@pytest.mark.asyncio
async def test_corrupted_local_history_is_recovered_with_pagination(chat):
    pages = []
    saved_messages = [
        {"role": "user", "content": "Задача"},
        {"role": "assistant", "content": "Результат"},
    ]

    def handler(request):
        if request.url.path.endswith("/history"):
            after = int(request.url.params["after"])
            pages.append(after)
            index = 0 if after == -1 else 1
            return httpx.Response(
                200,
                json={
                    "items": [{"message": saved_messages[index]}],
                    "next_after": 0 if index == 0 else None,
                },
            )
        return httpx.Response(200, json=session(history={"length": 2, "hash": "saved"}))

    value = {
        "session": session(history={"length": 2, "hash": "saved"}),
        "messages": saved_messages,
        "history": {"length": 2, "hash": "saved"},
        "tools": [],
    }
    coding.save(chat, value)
    with storage.db() as conn:
        conn.execute("UPDATE coding_sessions SET messages_json='broken' WHERE chat_id=?", (chat,))
    client = DokkyClient(transport=httpx.MockTransport(handler), store=Store())
    result = await coding.ensure(chat, client)
    assert result["messages"] == saved_messages and pages == [-1, 0]
    assert coding.load(chat)["history"] == {"length": 2, "hash": "saved"}


@pytest.mark.asyncio
async def test_current_mode_is_user_context_not_stale_system(chat, tmp_path, monkeypatch):
    from koddi.agents.orchestrator import AgentOrchestrator
    import koddi.agents.orchestrator as agent

    canonical = {"session": session(), "messages": [], "tools": [], "history": None}

    async def ensure(_chat):
        return canonical

    async def discover():
        return [], {}, []

    monkeypatch.setattr(coding, "ensure", ensure)
    monkeypatch.setattr(coding, "pending", lambda _: None)
    monkeypatch.setattr(agent, "discover_agent_tools", discover)

    class Model:
        config = {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000}
        seen = []

        async def stream(self, **kwargs):
            if not kwargs["tools"]:
                yield {"content": "Задача", "finish_reason": "stop"}
                return
            self.seen.append(json.loads(json.dumps(kwargs)))
            # настоящий transport атомарно сохраняет сообщения и wire-схемы
            canonical["tools"] = json.loads(json.dumps(kwargs["tools"]))
            for tool in canonical["tools"]:
                tool["function"]["name"] = tool["function"]["name"].replace(".", "__")
            canonical["messages"] = json.loads(json.dumps(kwargs["messages"])) + [
                {"role": "assistant", "content": "Готово"}
            ]
            yield {"content": "Готово", "finish_reason": "stop"}

    model = Model()
    for mode in ("plan", "development"):
        from koddi.policy.account import local_owner
        storage.set_setting("coding_runtime_extensions:" + local_owner(), mode == "development")
        _ = [
            event
            async for event in AgentOrchestrator(model).run(
                chat_id=chat,
                project_id=None,
                workspace=tmp_path,
                user_message="Проверить текущий режим",
                mode=mode,
            )
        ]
    first, second = model.seen
    assert first["messages"][0]["role"] == "system"
    assert "## Active mode" not in first["messages"][0]["content"]
    from koddi.agents.prompts import CONTEXT_PREFIX

    assert not any(item["role"] == "developer" for item in second["messages"])
    contexts = [json.loads(item["content"][len(CONTEXT_PREFIX):]) for item in second["messages"]
                if item["role"] == "user" and item["content"].startswith(CONTEXT_PREFIX)]
    assert contexts[-1]["mode"] == "development"
    assert "Development mode" in contexts[-1]["active_mode_instructions"]
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert first["tools"] == second["tools"]


@pytest.mark.asyncio
async def test_mode_switch_needs_no_confirmation_when_server_keeps_the_cache(chat):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json=session(history={"length": 2, "hash": "h"}))
        return httpx.Response(200, json=session(history={"length": 2, "hash": "h"}))

    client = DokkyClient(transport=httpx.MockTransport(handler), store=Store())
    value = await coding.ensure(chat, client)
    # модель меняет режим служебным элементом истории: сервер сообщил об этом в dokky.context
    value["session"]["last_usage"] = {"usage": {}, "dokky": {"context": {"reasoning_switch_cache": "preserved"}}}
    coding.save(chat, value)
    result = await coding.switch_mode(chat, "max", False, client)
    assert result["pending_reasoning_mode"] == "max"
    value = coding.load(chat)
    value["session"]["last_usage"]["dokky"]["context"]["reasoning_switch_cache"] = "rewrite"
    value["session"].pop("pending_reasoning_mode", None)
    coding.save(chat, value)
    with pytest.raises(DokkyError):
        await coding.switch_mode(chat, "max", False, client)
