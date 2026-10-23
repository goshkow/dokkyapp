import json

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.orchestrator import AgentOrchestrator
from koddi.api import create_app
from koddi.projects import add_project


@pytest.fixture
def project_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "planning.sqlite3")
    storage.init_db()
    project = add_project(str(tmp_path))
    with storage.db() as conn:
        chat_id = conn.execute(
            "INSERT INTO chats(project_id,title,mode,model) VALUES (?,'План','plan','mock')",
            (project["id"],),
        ).lastrowid
    return int(chat_id), tmp_path


class Planner:
    config = {"provider": "dokky-test", "model": "mock"}

    def __init__(self, tool_completion):
        self.step = 0
        self.tool_completion = tool_completion

    async def stream(self, *, tools, messages, **kwargs):
        if not tools:
            yield {"content": "План проекта", "finish_reason": "stop"}
            return
        self.step += 1
        names = {tool["function"]["name"] for tool in tools}
        assert not names & {"file.create", "file.write_patch", "shell.run", "task.delegate"}
        if self.step == 1:
            name, args = (
                "plan.update",
                {"document": "# План модуля\n\n## Реализация\nСоздать модуль.\n\n## Проверки\nПроверить тестами.", "steps": [{"title": "Создать модуль", "status": "pending"}]},
            )
        elif self.step == 2:
            name, args = "file.create", {"path": "forbidden.py", "content": "bad"}
        else:
            assert json.loads(messages[-1]["content"])["ok"] is False
            if not self.tool_completion:
                yield {
                    "content": "План: создать модуль, затем проверить тестами.",
                    "finish_reason": "stop",
                }
                return
            name, args = (
                "task.complete",
                {"summary": "План: создать модуль, затем проверить тестами."},
            )
        yield {
            "tool_call": {"id": f"call_{self.step}", "name": name, "arguments": json.dumps(args)},
            "finish_reason": "tool_calls",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_completion", [False, True])
async def test_plan_finishes_with_pending_steps_and_cannot_write(project_chat, tool_completion):
    chat, workspace = project_chat
    events = [
        event
        async for event in AgentOrchestrator(Planner(tool_completion)).run(
            chat_id=chat,
            project_id=None,
            workspace=workspace,
            user_message="Подготовь план",
            mode="plan",
        )
    ]
    assert any("event: turn.completed" in event for event in events)
    assert not (workspace / "forbidden.py").exists()
    with storage.db() as conn:
        assert (
            conn.execute("SELECT status FROM turns WHERE chat_id=?", (chat,)).fetchone()[0]
            == "completed"
        )
        steps = json.loads(conn.execute("SELECT steps_json FROM agent_plans").fetchone()[0])
        assert steps[0]["status"] == "pending"


def test_execute_plan_uses_server_snapshot_and_short_display(project_chat, monkeypatch):
    chat, _ = project_chat
    with storage.db() as conn:
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)
        ).lastrowid
        conn.execute(
            "INSERT INTO agent_plans(turn_id,steps_json) VALUES (?,?)",
            (turn, json.dumps([{"title": "Проверить результат", "status": "pending"}])),
        )
        conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Составь план')", (chat,)
        )
        conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'assistant','Полный подробный план с проверкой')",
            (chat,),
        )
    captured = []

    async def run(self, **kwargs):
        captured.append(kwargs)
        yield "event: turn.completed\ndata: {}\n\n"

    monkeypatch.setattr(AgentOrchestrator, "run", run)
    client = TestClient(create_app())
    snapshot = client.get(f"/api/chats/{chat}/plan").json()
    assert snapshot["ready"]
    assert snapshot["document"] == "Полный подробный план с проверкой"
    assert (
        client.post(f"/api/chats/{chat}/plan/execute", json={"turn_id": turn + 1}).status_code
        == 409
    )
    response = client.post(
        f"/api/chats/{chat}/plan/execute", json={"turn_id": turn, "content": "Подменённый план"}
    )
    assert response.status_code == 200
    assert captured[0]["display_message"] == "Реализуй план"
    assert "Полный подробный план" in captured[0]["user_message"]
    assert "Проверить результат" in captured[0]["user_message"]
    assert "Подменённый" not in captured[0]["user_message"]
    assert client.post(f"/api/chats/{chat}/plan/execute", json={"turn_id": turn}).status_code == 409
    accepted = client.get(f"/api/chats/{chat}/plan").json()
    assert accepted["executed"]
    assert accepted["document"] == "Полный подробный план с проверкой"
    with storage.db() as conn:
        conn.execute("UPDATE chats SET mode='plan' WHERE id=?", (chat,))
    assert not client.get(f"/api/chats/{chat}/plan").json()["ready"]
    assert client.post(f"/api/chats/{chat}/plan/execute", json={"turn_id": turn}).status_code == 409


@pytest.mark.asyncio
async def test_display_label_does_not_replace_model_plan(project_chat):
    chat, workspace = project_chat

    class Answer:
        config = {"provider": "dokky-test", "model": "mock"}

        async def stream(self, **kwargs):
            yield {"content": "Готово", "finish_reason": "stop"}

    full = "Implement the approved plan: create a tested module with input validation."
    _ = [
        event
        async for event in AgentOrchestrator(Answer()).run(
            chat_id=chat,
            project_id=None,
            workspace=workspace,
            user_message=full,
            display_message="Реализуй план",
            mode="development",
        )
    ]
    with storage.db() as conn:
        assert (
            conn.execute("SELECT content FROM messages WHERE role='user'").fetchone()[0]
            == "Реализуй план"
        )
        records = [
            json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM agent_history")
        ]
    assert any(record.get("role") == "user" and record.get("content") == full for record in records)


def test_markdown_contract_survives_new_tracking_plan(project_chat, monkeypatch):
    chat, _ = project_chat
    document = "# Полный контракт\n\n" + "Деталь реализации.\n" * 1800 + "\n## Приёмка\nМаркер конца"
    with storage.db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)).lastrowid
        conn.execute(
            "INSERT INTO agent_plans(turn_id,steps_json,document_markdown) VALUES (?,?,?)",
            (turn, json.dumps([{"title": "Проверить", "status": "pending"}]), document),
        )
    captured = []

    async def run(self, **kwargs):
        captured.append(kwargs)
        yield "event: turn.completed\ndata: {}\n\n"

    monkeypatch.setattr(AgentOrchestrator, "run", run)
    client = TestClient(create_app())
    assert client.get(f"/api/chats/{chat}/plan").json()["document"] == document
    assert client.post(f"/api/chats/{chat}/plan/execute", json={"turn_id": turn}).status_code == 200
    assert document in captured[0]["user_message"]
    with storage.db() as conn:
        next_turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)).lastrowid
        conn.execute(
            "INSERT INTO agent_plans(turn_id,steps_json) VALUES (?,?)",
            (next_turn, '[{"title":"Текущая проверка","status":"running"}]'),
        )
    snapshot = client.get(f"/api/chats/{chat}/plan").json()
    assert snapshot["document"] == document
    assert snapshot["executed"] and not snapshot["ready"]
    assert snapshot["tracking_steps"][0]["title"] == "Текущая проверка"
    assert snapshot["turn_id"] == turn
    assert snapshot["tracking_turn_id"] == next_turn
    assert snapshot["tracking_status"] == "completed"
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='cancelled' WHERE id=?", (next_turn,))
    cancelled = client.get(f"/api/chats/{chat}/plan").json()
    assert cancelled["tracking_status"] == "cancelled"
    assert cancelled["tracking_turn_id"] == next_turn
    assert cancelled["document"] == document


def test_plan_start_failure_does_not_consume_contract(project_chat, monkeypatch):
    import importlib

    chat, _ = project_chat
    with storage.db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)).lastrowid
        conn.execute(
            "INSERT INTO agent_plans(turn_id,steps_json,document_markdown) VALUES (?,'[]','# Контракт')",
            (turn,),
        )

    async def failed(*args, **kwargs):
        raise RuntimeError("Тест отказа запуска")

    monkeypatch.setattr(importlib.import_module('koddi.api.app').agent_runs, 'start', failed)
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.post(f"/api/chats/{chat}/plan/execute", json={"turn_id": turn}).status_code == 500
    snapshot = client.get(f"/api/chats/{chat}/plan").json()
    assert snapshot["ready"] and not snapshot["executed"]
