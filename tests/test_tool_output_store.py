import json
import threading
from pathlib import Path

import pytest

import koddi.storage.db as storage
from koddi.agents.orchestrator import AgentOrchestrator
from koddi.agents.history import repair_history
from koddi.tools import ToolRunner, tool_schemas_for_mode
from koddi.tools import output_store


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "outputs.sqlite3")
    monkeypatch.setattr(output_store, "data_dir", lambda: tmp_path / "profile")
    storage.init_db()
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title) VALUES ('Own chat')").lastrowid
        other = conn.execute("INSERT INTO chats(title) VALUES ('Other chat')").lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (chat,)
        ).lastrowid
        later = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
        foreign = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (other,)
        ).lastrowid
        message = conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'assistant','')", (chat,)
        ).lastrowid
    text = json.dumps(
        {"ok": True, "content": ("Привет 🌍\\\n" * 2000) + "END_OF_OUTPUT"}, ensure_ascii=False
    )
    result = output_store.save_tool_output(turn, message, 2, text)
    return result, text, later, foreign


def test_full_output_can_be_read_in_unicode_character_pages(saved):
    result, text, later, _ = saved
    runner = ToolRunner(None, turn_id=later)
    chunks = []
    offset = 0
    while True:
        page = runner.run(
            "tool.output_read",
            {"output_id": result["output_id"], "offset": offset, "max_chars": 777},
        )
        assert page["ok"], page
        chunks.append(page["content"])
        assert len(page["content"]) <= 777
        if page["next_offset"] is None:
            assert page["eof"]
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(chunks) == text
    assert list(output_store.data_dir().joinpath("tool-output").glob("*")) == [
        output_store.output_path(result["output_id"])[0]
    ]


def test_other_chat_and_unscoped_tool_calls_are_denied(saved):
    result, _, later, foreign = saved
    for turn in (None, foreign, 999999):
        response = ToolRunner(None, turn_id=turn).run(
            "tool.output_read", {"output_id": result["output_id"]}
        )
        assert response["ok"] is False
    assert ToolRunner(None, turn_id=later).run(
        "tool.output_read", {"output_id": result["output_id"]}
    )["ok"]


def test_legacy_references_are_upgraded_without_mutating_audit(saved, tmp_path):
    result, _, _, _ = saved
    legacy = {
        "ok": True,
        "truncated": True,
        "output_file": result["output_file"],
        "excerpt": "old excerpt",
    }
    history = [
        {"role": "assistant", "tool_calls": [{"id": "old-read"}]},
        {"role": "tool", "tool_call_id": "old-read", "content": json.dumps(legacy)},
    ]
    repaired = repair_history(history)
    assert json.loads(repaired[-1]["content"])["output_id"] == result["output_id"]
    assert "output_id" not in json.loads(history[-1]["content"])
    outside = {
        **legacy,
        "output_file": str(tmp_path / "unrelated" / Path(result["output_file"]).name),
    }
    assert output_store.upgrade_output_reference(outside) == outside


@pytest.mark.parametrize(
    "identifier", ["../secret", "out_1_2_3/../../secret", "out_0_2_3", "out_1_2_3.json"]
)
def test_identifier_cannot_be_a_filesystem_path(saved, identifier):
    _, _, later, _ = saved
    assert (
        ToolRunner(None, turn_id=later).run("tool.output_read", {"output_id": identifier})["ok"]
        is False
    )


def test_read_ranges_are_bounded_and_cancellation_is_honored(saved):
    result, _, later, _ = saved
    runner = ToolRunner(None, turn_id=later)
    assert (
        runner.run("tool.output_read", {"output_id": result["output_id"], "max_chars": 8001})["ok"]
        is False
    )
    assert (
        runner.run("tool.output_read", {"output_id": result["output_id"], "offset": -1})["ok"]
        is False
    )
    page = runner.run("tool.output_read", {"output_id": result["output_id"], "offset": 1000000000})
    assert page["ok"] and page["eof"] and page["content"] == ""
    cancel = threading.Event()
    cancel.set()
    assert output_store.read_tool_output(
        result["output_id"], later, offset=10000, cancel_event=cancel
    )["cancelled"]
    for mode in ("development", "plan", "review", "debug"):
        assert "tool.output_read" in {
            tool["function"]["name"] for tool in tool_schemas_for_mode(mode)
        }


@pytest.mark.asyncio
async def test_native_agent_reads_tail_of_truncated_result(saved, tmp_path, monkeypatch):
    # окно модели как у реальных провайдеров: при 32K по умолчанию 40 КБ файла уже запускают сжатие истории
    monkeypatch.setattr("koddi.agents.orchestrator.remote_config",
                        lambda: {"provider": "openai-compatible", "model": "mock", "context_tokens": 128000})
    (tmp_path / "large.txt").write_text("x" * 40000 + " IMPORTANT_END", encoding="utf-8")
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title) VALUES ('Large result')").lastrowid
        conn.execute(
            "INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Earlier')", (chat,)
        )

    class Client:
        async def stream(self, *, messages, tools, **kwargs):
            assert "tool.output_read" in {tool["function"]["name"] for tool in tools}
            if messages[-1]["role"] != "tool":
                yield {
                    "tool_call": {
                        "id": "read-large",
                        "name": "file.read",
                        "arguments": json.dumps({"path": "large.txt", "max_bytes": 50000}),
                    },
                    "finish_reason": "tool_calls",
                }
            else:
                result = json.loads(messages[-1]["content"])
                if result.get("truncated"):
                    assert result["read_tool"] == "tool.output_read"
                    yield {
                        "tool_call": {
                            "id": "read-tail",
                            "name": "tool.output_read",
                            "arguments": json.dumps(
                                {
                                    "output_id": result["output_id"],
                                    "offset": 39000,
                                    "max_chars": 2000,
                                }
                            ),
                        },
                        "finish_reason": "tool_calls",
                    }
                else:
                    assert "IMPORTANT_END" in result["content"]
                    yield {"content": "Полный результат дочитан.", "finish_reason": "stop"}

    events = [
        event
        async for event in AgentOrchestrator(Client()).run(
            chat_id=chat,
            project_id=None,
            workspace=tmp_path,
            user_message="Прочитай конец большого результата",
            model="mock",
        )
    ]
    assert any("event: turn.completed" in event for event in events), events[-4:]
    assert any('"name": "tool.output_read"' in event for event in events)
