from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import koddi.storage.db as storage
from koddi.agents.orchestrator import AgentOrchestrator
from koddi.remote import RemoteAIClient


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "agent.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        return int(
            conn.execute(
                "INSERT INTO chats(title,mode,model) VALUES ('Test','development','server-model')"
            ).lastrowid
        )


def response(delta=None, finish=None):
    payload = {"choices": [{"delta": delta or {}, "finish_reason": finish}]}
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def tool(name, arguments, call_id="call_1", index=0):
    return {
        "index": index,
        "id": call_id,
        "function": {"name": name.replace(".", "__"), "arguments": json.dumps(arguments)},
    }


@pytest.mark.asyncio
async def test_native_mcp_schema_routes_through_permission_boundary(chat, tmp_path, monkeypatch):
    import koddi.agents.orchestrator as module
    from koddi.tools import ToolRunner

    alias = "mcp_test_external"
    seen = []

    async def discover():
        return (
            [
                {
                    "type": "function",
                    "function": {
                        "name": alias,
                        "description": "Lookup",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ],
            {alias: ("test-server", "lookup")},
            [],
        )

    def run(self, name, args, *, approved=False):
        seen.append((name, args, approved))
        return {"ok": True, "value": "found"}

    monkeypatch.setattr(module, "discover_agent_tools", discover)
    monkeypatch.setattr(ToolRunner, "run", run)
    count = 0

    def server(request):
        nonlocal count
        count += 1
        body = json.loads(request.content)
        if count == 1:
            data = response({"content": "Поиск"}, "stop")
        elif count == 2:
            assert alias in {t["function"]["name"] for t in body["tools"]}
            data = response({"tool_calls": [tool(alias, {"query": "example"})]}, "tool_calls")
        else:
            assert json.loads(body["messages"][-1]["content"])["value"] == "found"
            data = response({"content": "Найдено."}, "stop")
        return httpx.Response(200, text=data + "data: [DONE]\n\n")

    client = RemoteAIClient(
        {"base_url": "https://mock.example/v1"}, transport=httpx.MockTransport(server)
    )
    events = [
        event
        async for event in AgentOrchestrator(client).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Найди пример",
            model="server-model",
        )
    ]
    assert seen == [
        (
            "mcp.tool_call",
            {"server": "test-server", "name": "lookup", "arguments": {"query": "example"}},
            False,
        )
    ]
    assert "turn.completed" in events[-1]


@pytest.mark.asyncio
async def test_remote_loop_creates_real_file_verifies_and_preserves_history(chat, tmp_path):
    requests = []
    code = "def greet(value):\n    return 'Привет!' if value == 'привет' else ''\n"

    def server(request):
        body = json.loads(request.content)
        requests.append(body)
        count = len(requests)
        if count == 1:
            data = response({"content": "Скрипт приветствия"}, "stop")
        elif count == 2:
            assert {"file__create", "verification__run", "plan__update"}.issubset(
                {t["function"]["name"] for t in body["tools"]}
            )
            data = response({"content": "Создам скрипт и проверю его."}) + response(
                {"tool_calls": [tool("file.create", {"path": "greeting.py", "content": code})]},
                "tool_calls",
            )
        elif count == 3:
            assert body["messages"][-1]["tool_call_id"] == "call_1"
            assert json.loads(body["messages"][-1]["content"])["ok"]
            data = response(
                {"tool_calls": [tool("verification.run", {"paths": ["greeting.py"]}, "verify")]},
                "tool_calls",
            )
        else:
            assert json.loads(body["messages"][-1]["content"])["ok"]
            data = response(
                {
                    "content": "Готово. Создан greeting.py, проверка пройдена. Импортируйте greet для вызова."
                },
                "stop",
            )
        return httpx.Response(
            200, text=data + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
        )

    client = RemoteAIClient(
        {"base_url": "https://mock.example/v1", "api_key_env": "NO_TEST_KEY"},
        transport=httpx.MockTransport(server),
    )
    events = []
    async for event in AgentOrchestrator(client).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message='Напиши лёгкий скрипт: на "привет" ответь "Привет!" и протестируй',
            model="server-model",
        ):
        events.append(event)
        if 'event: agent.input_required' in event:
            data = json.loads(event.split('data: ',1)[1])
            assert data['kind'] == 'approval'
            with storage.db() as conn:
                conn.execute('UPDATE agent_requests SET response_json=? WHERE id=?', ('{"approved":true}',data['id']))
    assert (tmp_path / "greeting.py").read_text(encoding="utf-8") == code
    namespace = {}
    exec(code, namespace)
    assert namespace["greet"]("привет") == "Привет!"
    assert "turn.completed" in events[-1]
    with storage.db() as conn:
        history = [
            json.loads(row[0])
            for row in conn.execute("SELECT payload_json FROM agent_history ORDER BY id")
        ]
        assert [m["role"] for m in history] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
        ]
        assert (
            conn.execute("SELECT title FROM chats WHERE id=?", (chat,)).fetchone()[0]
            == "Скрипт приветствия"
        )


@pytest.mark.asyncio
async def test_question_does_not_force_file_write(chat):
    class Client:
        async def stream(self, **kwargs):
            yield {"content": "Объяснение"}
            yield {"finish_reason": "stop"}

    events = [
        e
        async for e in AgentOrchestrator(Client()).run(
            chat_id=chat,
            project_id=None,
            workspace=None,
            user_message="Что такое сортировка?",
            mode="chat",
            model="server-model",
        )
    ]
    assert not any("tool.call" in e for e in events)
    assert "turn.completed" in events[-1]


@pytest.mark.asyncio
async def test_fragmented_tool_arguments_and_no_output_limit():
    storage.init_db()
    def server(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body and "num_predict" not in body
        data = response(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "a",
                        "function": {"name": "file__create", "arguments": '{"path":'},
                    }
                ]
            }
        )
        data += response(
            {"tool_calls": [{"index": 0, "function": {"arguments": '"x.py","content":"x=1"}'}}]},
            "tool_calls",
        )
        return httpx.Response(200, text=data + "data: [DONE]\n\n")

    from koddi.tools import TOOL_SCHEMAS

    client = RemoteAIClient(
        {"base_url": "https://mock.example/v1"}, transport=httpx.MockTransport(server)
    )
    chunks = [c async for c in client.stream(model="test", messages=[], tools=TOOL_SCHEMAS)]
    call = chunks[-1]["tool_call"]
    assert call["name"] == "file.create"
    assert json.loads(call["arguments"]) == {"path": "x.py", "content": "x=1"}


@pytest.mark.asyncio
async def test_truncated_stream_is_error_not_success():
    client = RemoteAIClient(
        {"base_url": "https://mock.example/v1"},
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=response({"content": "Partial"}))
        ),
    )
    with pytest.raises(RuntimeError, match="прервалось"):
        _ = [c async for c in client.stream(model="test", messages=[], tools=[])]


@pytest.mark.asyncio
async def test_user_question_waits_and_resumes(chat, tmp_path):
    class Client:
        count = 0

        async def stream(self, **kwargs):
            self.count += 1
            if self.count == 1:
                yield {"content": "Уточнение"}
            elif self.count == 2:
                yield {
                    "tool_call": {
                        "id": "ask",
                        "name": "task.ask_user",
                        "arguments": '{"question":"Как назвать файл?"}',
                    }
                }
            else:
                assert (
                    json.loads(kwargs["messages"][-1]["content"])["response"]["answer"] == "main.py"
                )
                yield {"content": "Принято"}

    seen = []
    async for event in AgentOrchestrator(Client()).run(
        chat_id=chat, project_id=None, workspace=tmp_path, user_message="Создай файл", model="test"
    ):
        seen.append(event)
        if "event: agent.input_required" in event:
            data = json.loads(event.split("data: ", 1)[1])
            with storage.db() as conn:
                conn.execute(
                    "UPDATE agent_requests SET response_json=? WHERE id=?",
                    ('{"answer":"main.py"}', data["id"]),
                )
        await asyncio.sleep(0)
    assert "turn.completed" in seen[-1]


@pytest.mark.asyncio
async def test_loop_does_not_stop_after_small_step_limit(chat, tmp_path):
    class Client:
        count = 0

        async def stream(self, **kwargs):
            self.count += 1
            if self.count == 1:
                yield {"content": "Исследование"}
            elif self.count <= 26:
                yield {
                    "tool_call": {
                        "id": f"step_{self.count}",
                        "name": "directory.list",
                        "arguments": "{}",
                    }
                }
            else:
                yield {"content": "Готово"}

    client = Client()
    events = [
        e
        async for e in AgentOrchestrator(client).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Изучи проект",
            model="test",
        )
    ]
    assert client.count == 27
    assert "turn.completed" in events[-1]


@pytest.mark.asyncio
async def test_subagent_runs_real_child_loop(chat, tmp_path):
    class Client:
        async def stream(self, **kwargs):
            history = kwargs["messages"]
            if "Generate only" in history[0]["content"]:
                yield {"content": "Подзадача"}
            elif history[-1].get("role") == "tool":
                assert json.loads(history[-1]["content"])["ok"]
                yield {"content": "Проверка завершена"}
            elif history[-1]["content"] == "Проверь папку":
                yield {"tool_call": {"id": "inspect", "name": "directory.list", "arguments": "{}"}}
            else:
                yield {
                    "tool_call": {
                        "id": "delegate",
                        "name": "task.delegate",
                        "arguments": json.dumps(
                            {"title": "Проверка", "task": "Проверь папку", "mode": "review"}
                        ),
                    }
                }

    events = [
        e
        async for e in AgentOrchestrator(Client()).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Передай проверку агенту",
            model="test",
        )
    ]
    assert "turn.completed" in events[-1]
    assert any("task.progress" in e for e in events)
    with storage.db() as conn:
        assert (
            conn.execute("SELECT status FROM tasks ORDER BY id DESC LIMIT 1").fetchone()[0]
            == "completed"
        )


@pytest.mark.asyncio
async def test_large_tool_output_is_retained_and_model_receives_bounded_excerpt(chat, tmp_path):
    (tmp_path / "large.txt").write_bytes(b"x" * 40000)

    class Client:
        # проверяется усечение вывода, а не сжатие истории: окно модели больше набора инструментов и выдержки
        config = {"context_tokens": 65536}

        async def stream(self, **kwargs):
            messages = kwargs["messages"]
            if "Generate only" in messages[0]["content"]:
                yield {"content": "Большой файл"}
            elif messages[-1]["role"] == "tool":
                result = json.loads(messages[-1]["content"])
                assert result["truncated"]
                from pathlib import Path

                saved = json.loads(Path(result["output_file"]).read_text(encoding="utf-8"))
                assert len(saved["content"]) == 40000
                assert len(messages[-1]["content"]) < 24000
                yield {"content": "Файл изучен"}
            else:
                yield {
                    "tool_call": {
                        "id": "read",
                        "name": "file.read",
                        "arguments": '{"path":"large.txt","max_bytes":50000}',
                    }
                }

    events = [
        e
        async for e in AgentOrchestrator(Client()).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Прочитай файл",
            model="test",
        )
    ]
    assert "turn.completed" in events[-1]


@pytest.mark.asyncio
async def test_server_progress_names_the_action_before_the_call_is_complete():
    from koddi.agents.progress import progress_text
    from koddi.tools import TOOL_SCHEMAS

    def server(_request):
        progress = {"choices": [], "dokky_progress": {"tool": {"index": 0, "name": "file__edit", "path": "src/templates/landing.html", "chars": 2048}}}
        broken = {"choices": [], "dokky_progress": {"tool": {"name": 5, "path": "x"}}}
        data = "data: " + json.dumps(progress) + "\n\n" + "data: " + json.dumps(broken) + "\n\n"
        data += response({"tool_calls": [tool("file.edit", {"path": "src/templates/landing.html", "old_text": "a", "new_text": "b"})]}, "tool_calls")
        return httpx.Response(200, text=data + "data: [DONE]\n\n")

    client = RemoteAIClient({"base_url": "https://mock.example/v1"}, transport=httpx.MockTransport(server))
    chunks = [c async for c in client.stream(model="test", messages=[], tools=TOOL_SCHEMAS)]
    early = chunks[0]["tool_progress"]
    assert early == {"id": "", "name": "file.edit", "path": "src/templates/landing.html", "bytes_received": 2048}
    # подпись без имени действия отбрасывается, сам вызов приходит как раньше
    assert sum("tool_progress" in chunk and chunk["tool_progress"].get("path") is not None for chunk in chunks) == 1
    assert chunks[-1]["tool_call"]["name"] == "file.edit"
    assert progress_text(early) == "Правлю файл · templates/landing.html"
    assert progress_text({"name": ""}) == "Подготавливаю действие…"
