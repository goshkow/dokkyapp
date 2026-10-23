import json

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.orchestrator import AgentOrchestrator, create_message, save_message
from koddi.api import create_app


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "journal.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        return conn.execute(
            "INSERT INTO chats(title,mode) VALUES ('Journal','development')"
        ).lastrowid


def test_partial_flush_preserves_message_identity(chat):
    message_id = create_message(chat, "text", 42)
    save_message(message_id, "Начало", {"status": "running"})
    save_message(message_id, "Весь текст", {"status": "completed", "channel": "commentary"})
    with storage.db() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["turn_id"] == 42 and metadata["kind"] == "text"
    assert metadata["channel"] == "commentary" and row["content"] == "Весь текст"


@pytest.mark.asyncio
async def test_ui_history_exposes_turn_and_commentary_without_changing_provider_history(
    chat, tmp_path
):
    (tmp_path / "note.txt").write_text("context", encoding="utf-8")

    class Model:
        async def stream(self, *, messages, tools, **kwargs):
            if not tools:
                yield {"content": "Изучение файла", "finish_reason": "stop"}
            elif messages[-1]["role"] == "tool":
                yield {"content": "Файл изучен. Контекст получен.", "finish_reason": "stop"}
            else:
                yield {"content": "Сначала прочитаю файл."}
                yield {
                    "tool_call": {
                        "id": "read-1",
                        "name": "file.read",
                        "arguments": json.dumps({"path": "note.txt"}),
                    },
                    "finish_reason": "tool_calls",
                }

    events = [
        event
        async for event in AgentOrchestrator(Model()).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Изучи файл",
            model="mock",
        )
    ]
    assert any("event: message.updated" in event for event in events)
    rows = TestClient(create_app()).get(f"/api/chats/{chat}/messages").json()
    text = [row for row in rows if row["role"] == "assistant" and row["content"]]
    assert [row["metadata"]["channel"] for row in text] == ["commentary", "final"]
    assert len({row["metadata"]["turn_id"] for row in rows}) == 1
    assert all(row["metadata"]["turn"]["status"] == "completed" for row in rows)
    with storage.db() as conn:
        provider = [
            json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM agent_history")
        ]
    assert any(
        row.get("content") == "Сначала прочитаю файл." and row.get("tool_calls") for row in provider
    )
    assert not any("channel" in row for row in provider)
