import asyncio
import json
import threading
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.orchestrator import AgentOrchestrator
from koddi.agents.progress import FailureTracker, local_title
from koddi.agents.task_context import read_completed_context, save_completed_context
from koddi.agents.telemetry import ObservedClient, TurnMetrics
from koddi.api import create_app
from koddi.tools import ToolRunner


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "opt.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "owner-a"})
    with storage.db() as conn:
        pid = conn.execute(
            "INSERT INTO projects(name,path) VALUES ('p',?)", (str(tmp_path),)
        ).lastrowid
        chat = conn.execute(
            "INSERT INTO chats(project_id,title,mode,owner_id) VALUES (?,'Новый чат','development','owner-a')",
            (pid,),
        ).lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
    return tmp_path, pid, chat, turn


def test_clean_tools_reuse_read_cache_mutations_invalidate(project):
    path, pid, _, turn = project
    (path / "code.py").write_text("value = 1\n", newline="\n")
    runner = ToolRunner(path, pid, turn)
    assert runner.run("file.read", {"path": "code.py"})["ok"]
    assert runner.run("search.files", {"query": "*.py"})["files"] == ["code.py"]
    assert runner.run("file.read", {"path": "code.py"})["content"] == "value = 1\n"
    assert runner.read_cache.hits == 1
    assert runner.run(
        "file.edit", {"path": "code.py", "old_text": "1", "new_text": "2"}, approved=True
    )["ok"]
    assert runner.run("file.read", {"path": "code.py"})["content"] == "value = 2\n"
    assert runner.read_cache.invalidations == 1
    # внешние изменения и атомарная замена проверяются при каждом чтении
    replacement = path / "replacement"
    replacement.write_text("value = 3\n", newline="\n")
    replacement.replace(path / "code.py")
    assert runner.run("file.read", {"path": "code.py"})["content"] == "value = 3\n"


def test_workspace_inspection_is_read_only_bounded_and_enforces_paths(project):
    path, pid, _, turn = project
    (path / "code.py").write_text("import json\nasync def work():\n    return json.dumps({})\n")
    runner = ToolRunner(path, pid, turn)
    result = runner.run("workspace.inspect", {"paths": ["code.py"]})
    assert result["ok"] and result["outlines"][0]["parser"] == "python_ast"
    assert any(item.get("kind") == "AsyncFunctionDef" for item in result["outlines"][0]["outline"])
    assert runner.run("workspace.inspect", {"paths": ["../outside.py"]})["blocked"]
    assert not runner.run("workspace.inspect", {"paths": [str(i) for i in range(9)]})["ok"]


def test_edit_runs_local_syntax_check_without_rejecting_intermediate_state(project):
    path, pid, _, turn = project
    runner = ToolRunner(path, pid, turn)
    result = runner.run(
        "file.create", {"path": "bad.py", "content": "def broken(:\n"}, approved=True
    )
    assert result["ok"] and result["checks"][0]["ok"] is False
    assert runner.completion_diagnostics()
    (path / "bad.py").write_text("value = 1\n")
    assert not runner.completion_diagnostics()
    assert runner.run(
        "file.create", {"path": "unicode.json", "content": '{"ключ":"🌍"}'}, approved=True
    )["checks"][0]["ok"]


def test_failure_guard_does_not_limit_successful_polling_or_distinct_work():
    guard = FailureTracker()
    for _ in range(10):
        guard.record("process.list", {}, {"ok": True})
    assert not guard.needs_approval("process.list", {})
    for _ in range(3):
        guard.record("test.run", {"command": "broken"}, {"ok": False})
    assert guard.needs_approval("test.run", {"command": "broken"})
    assert not guard.needs_approval("test.run", {"command": "fixed"})
    guard.record("test.run", {"command": "broken"}, {"ok": True})
    assert not guard.needs_approval("test.run", {"command": "broken"})


def test_usage_counts_only_final_cumulative_sample_and_preserves_unknowns():
    metrics = TurnMetrics()
    assert metrics.snapshot()["api_cost"] is None
    metrics.usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100},
            "completion_tokens_details": {"reasoning_tokens": 40},
        }
    )
    assert metrics.input_tokens == 1000 and metrics.reasoning_tokens == 40
    assert metrics.snapshot()["cache_hit_rate"] == 0.8
    metrics.usage({"prompt_tokens": 1000}, {"replayed": True})
    assert metrics.input_tokens == 1000 and metrics.replayed_responses == 1
    metrics.usage(
        {
            "input_tokens": 40,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": -1},
            "output_tokens_details": {"reasoning_tokens": float("inf")},
        }
    )
    assert metrics.input_tokens == 1040 and metrics.reasoning_tokens == 40
    assert "text" not in metrics.snapshot()


@pytest.mark.asyncio
async def test_usage_wrapper_ignores_intermediate_and_reset_samples():
    class Stream:
        async def stream(self, **kwargs):
            yield {"usage": {"input_tokens": 100}}
            yield {"reset": True}
            yield {"usage": {"input_tokens": 150}}
            yield {"usage": {"input_tokens": 200, "output_tokens": 5}}

    metrics = TurnMetrics()
    _ = [chunk async for chunk in ObservedClient(Stream(), metrics).stream()]
    assert metrics.input_tokens == 200 and metrics.output_tokens == 5
    assert metrics.llm_requests == metrics.usage_samples == 1


def test_completed_context_is_separate_and_cannot_cross_chat_or_owner(project):
    _, _, chat, turn = project
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE id=?", (turn,))
        conn.execute(
            "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'assistant','Проверено',?)",
            (chat, json.dumps({"turn_id": turn, "channel": "final"})),
        )
        next_turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
        other_chat = conn.execute(
            "INSERT INTO chats(title,owner_id) VALUES ('b','owner-b')"
        ).lastrowid
        other_turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (other_chat,)
        ).lastrowid
    save_completed_context(turn)
    assert read_completed_context(next_turn, turn)["context"]["summary"] == "Проверено"
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as denied:
        read_completed_context(other_turn, turn)
    assert denied.value.status_code == 404
    client = TestClient(create_app())
    metrics = TurnMetrics()
    metrics.persist(turn)
    assert client.get(f"/api/chats/{chat}/metrics").status_code == 200
    storage.set_setting("dokky_user", {"id": "owner-b"})
    assert client.get(f"/api/chats/{chat}/metrics").status_code == 404
    with pytest.raises(Exception) as error:
        read_completed_context(next_turn, turn)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_production_first_task_uses_no_title_llm_and_stop_keeps_idempotency(
    project, monkeypatch
):
    import koddi.agents.orchestrator as module
    from koddi.dokky import coding

    path, pid, chat, _ = project
    canonical = {
        "session": {"id": "cs_" + "a" * 24, "history": {"length": 0}},
        "messages": [],
        "tools": [],
        "history": None,
    }
    monkeypatch.setattr(
        module, "remote_config", lambda: {"provider": "dokky", "model": "dokky-code"}
    )
    monkeypatch.setattr(coding, "load", lambda _: canonical)

    async def ensure(_):
        return canonical

    async def discover():
        return [], {}, []

    monkeypatch.setattr(coding, "ensure", ensure)
    monkeypatch.setattr(module, "discover_agent_tools", discover)
    entered = asyncio.Event()
    requests = []

    class Model:
        config = {"provider": "dokky"}

        async def stream(self, **request):
            requests.append(deepcopy(request))
            coding.save_pending(chat, "same-key", {"model": "dokky-code"}, request["messages"])
            entered.set()
            await asyncio.Event().wait()
            yield {}

    async def consume():
        return [
            event
            async for event in AgentOrchestrator(Model()).run(
                chat_id=chat,
                project_id=pid,
                workspace=path,
                user_message="Исправь ошибку\nПодробности",
                cancel_event=threading.Event(),
            )
        ]

    worker = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 3)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert len(requests) == 1 and requests[0]["tools"]
    assert coding.pending(chat)["key"] == "same-key"
    with storage.db() as conn:
        assert (
            conn.execute("SELECT title FROM chats WHERE id=?", (chat,)).fetchone()[0]
            == "Исправь ошибку"
        )
        value = json.loads(
            conn.execute(
                "SELECT metrics_json FROM turn_metrics ORDER BY turn_id DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert value["llm_requests"] == 1 and value["stop_reason"] == "cancelled"
    assert local_title("\n# Задача\nЕщё") == "Задача"

    class Recovery:
        config = {"provider": "dokky"}

        def __init__(self):
            self.calls = 0

        async def stream(self, **request):
            self.calls += 1
            if self.calls == 1:
                coding.discard_pending(chat, "same-key")
                yield {
                    "tool_call": {
                        "id": "cancelled-action",
                        "name": "file.create",
                        "arguments": json.dumps(
                            {"path": "must-not-exist.py", "content": "print(1)"}
                        ),
                    },
                    "finish_reason": "tool_calls",
                }
            else:
                tool_result = next(
                    message
                    for message in request["messages"]
                    if message.get("tool_call_id") == "cancelled-action"
                )
                assert json.loads(tool_result["content"])["skipped"] is True
                assert request["messages"][-1]["content"] == "Теперь только объясни"
                yield {"content": "Прежние действия не исполнялись", "finish_reason": "stop"}

    recovered = Recovery()
    events = [
        event
        async for event in AgentOrchestrator(recovered).run(
            chat_id=chat, project_id=pid, workspace=path, user_message="Теперь только объясни"
        )
    ]
    assert "turn.completed" in events[-1] and recovered.calls == 2
    assert not (path / "must-not-exist.py").exists()


def test_archived_action_can_be_restored_in_ranges_only_within_own_chat(project):
    path, pid, chat, turn = project
    runner = ToolRunner(path, pid, turn)
    assert runner.run("file.create", {"path": "code.py", "content": "value = 1\n"}, approved=True)[
        "ok"
    ]
    with storage.db() as conn:
        action = conn.execute("SELECT id FROM tool_calls WHERE turn_id=?", (turn,)).fetchone()[0]
        other = conn.execute(
            "INSERT INTO chats(title,owner_id) VALUES ('other','owner-a')"
        ).lastrowid
        other_turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (other,)
        ).lastrowid
    first = read_completed_context(turn, action_id=action, max_chars=40)
    assert not first["eof"] and first["next_offset"] == 40
    rest = read_completed_context(turn, action_id=action, offset=40, max_chars=8000)
    assert json.loads(first["content"] + rest["content"])["result"]["checks"][0]["ok"]
    with pytest.raises(ValueError):
        read_completed_context(other_turn, action_id=action)
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE id=?", (turn,))
    save_completed_context(turn)
    assert read_completed_context(turn)["context"]["verification"][0]["checks"][0]["ok"]


@pytest.mark.asyncio
async def test_completion_requires_fixing_known_syntax_errors(project, monkeypatch):
    import koddi.agents.orchestrator as module

    path, pid, chat, _ = project
    monkeypatch.setattr(
        module, "remote_config", lambda: {"provider": "openai-compatible", "model": "scripted"}
    )
    # автоматический режим зовёт проверяющую модель, здесь нужен запуск без вопросов
    storage.set_setting("permission_profile", "full")

    async def discover():
        return [], {}, []

    monkeypatch.setattr(module, "discover_agent_tools", discover)

    class Model:
        config = {"provider": "openai-compatible"}
        count = 0

        async def stream(self, **request):
            if not request["tools"]:
                yield {"content": "Проверка", "finish_reason": "stop"}
                return
            self.count += 1
            if self.count == 1:
                name, args = "file.create", {"path": "code.py", "content": "def broken(:\n"}
            elif self.count == 2:
                name, args = "task.complete", {"summary": "Преждевременный итог"}
            elif self.count == 3:
                result = json.loads(request["messages"][-1]["content"])
                assert result["ok"] is False and result["checks"][0]["ok"] is False
                name, args = (
                    "file.edit",
                    {
                        "path": "code.py",
                        "old_text": "def broken(:",
                        "new_text": "def broken():\n    pass",
                    },
                )
            elif self.count == 4:
                # после правок гейт качества требует прогон проверок проекта
                result = json.loads(request["messages"][-1]["content"])
                assert result["ok"] is True
                name, args = "qa.run", {}
            else:
                name, args = "task.complete", {"summary": "Синтаксис исправлен"}
            yield {
                "tool_call": {"id": str(self.count), "name": name, "arguments": json.dumps(args)},
                "finish_reason": "tool_calls",
            }

    model = Model()
    events = [
        event
        async for event in AgentOrchestrator(model).run(
            chat_id=chat, project_id=pid, workspace=path, user_message="Исправь синтаксис"
        )
    ]
    assert "turn.completed" in events[-1] and model.count == 5
    # в проекте нет настроенных проверок: итог честно говорит «не проверено», а не «проверено»
    assert any("Проверка Докки: не проверено" in event for event in events)
    compile((path / "code.py").read_bytes(), "code.py", "exec")
    with storage.db() as conn:
        completed = conn.execute(
            "SELECT id FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat,)
        ).fetchone()[0]
    assert "Синтаксис исправлен" in json.dumps(
        read_completed_context(completed), ensure_ascii=False
    )


def test_long_final_is_preserved_and_restore_is_lossless_and_scoped(project):
    _, _, chat, turn = project
    full = "Архитектурное решение 🌍\n" * 500
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE id=?", (turn,))
        message = conn.execute(
            "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'assistant',?,?)",
            (chat, full, json.dumps({"turn_id": turn, "channel": "final"})),
        ).lastrowid
        other = conn.execute(
            "INSERT INTO chats(title,owner_id) VALUES ('other','owner-a')"
        ).lastrowid
        other_turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (other,)
        ).lastrowid
    save_completed_context(turn)
    summary = read_completed_context(turn)["context"]
    assert summary["summary_truncated"] and summary["final_message_id"] == message
    first = read_completed_context(turn, message_id=message, max_chars=8000)
    second = read_completed_context(
        turn, message_id=message, offset=first["next_offset"], max_chars=8000
    )
    assert first["content"] + second["content"] == full and second["eof"]
    with pytest.raises(ValueError):
        read_completed_context(other_turn, message_id=message)


def test_active_action_archive_pages_without_polluting_stable_tool_results(project):
    path, pid, _, turn = project
    runner = ToolRunner(path, pid, turn)
    (path / "readme.txt").write_text("stable")
    first = runner.run("file.read", {"path": "readme.txt"})
    second = runner.run("file.read", {"path": "readme.txt"})
    assert first == second and "action_id" not in first
    with storage.db() as conn:
        conn.executemany(
            "INSERT INTO tool_calls(turn_id,name,arguments_json,status) VALUES (?,'test.run','{}','ok')",
            [(turn,)] * 55,
        )
    page = read_completed_context(turn)
    assert page["context"] is None and len(page["actions"]) == 50
    rest = read_completed_context(turn, before_action_id=page["next_action_before"])
    assert len(rest["actions"]) == 7 and rest["next_action_before"] is None
    assert not set(row["id"] for row in page["actions"]).intersection(
        row["id"] for row in rest["actions"]
    )


def test_unbenchmarked_tools_are_opt_in_only_for_new_sessions(project):
    from koddi.tools.schemas import tool_schemas_for_mode, tool_schemas_for_new_session
    from koddi.dokky.coding import compatible_tools

    _, _, _, _ = project
    available = tool_schemas_for_mode("development")
    plain = tool_schemas_for_new_session(available, runtime_extensions=False, computer_use=False)
    assert not {
        "workspace.inspect",
        "task.history",
        "computer.use",
        "computer.status",
    }.intersection(tool["function"]["name"] for tool in plain)
    experimental = tool_schemas_for_new_session(
        available, runtime_extensions=True, computer_use=False
    )
    assert {"workspace.inspect", "task.history"}.issubset(
        tool["function"]["name"] for tool in experimental
    )
    assert not {"computer.use", "computer.status"}.intersection(
        tool["function"]["name"] for tool in experimental
    )
    assert [tool["function"] for tool in compatible_tools([{**tool, "function": {**tool["function"], "name": tool["function"]["name"].replace(".", "__")}} for tool in experimental], available)] == [
        tool["function"] for tool in experimental
    ]
    client = TestClient(create_app())
    assert client.get("/api/settings").json()["coding_runtime_extensions"] is False
    assert (
        client.put("/api/settings", json={"coding_runtime_extensions": "true"}).status_code == 422
    )
    assert (
        client.put("/api/settings", json={"coding_runtime_extensions": True}).json()[
            "coding_runtime_extensions"
        ]
        is True
    )
    storage.set_setting("dokky_user", {"id": "owner-b"})
    assert client.get("/api/settings").json()["coding_runtime_extensions"] is False
