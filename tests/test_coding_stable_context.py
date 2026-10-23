"""Стабильный контекст coding-сессии; все ответы и сессии локальные тестовые."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import koddi.agents.orchestrator as agent
import koddi.storage.db as storage
from koddi.agents.compaction import SUMMARY_PROMPT, context_limit, context_size, needs_compaction
from koddi.agents.history import CHECKPOINT, compacted_messages, initial_instructions
from koddi.agents.prompts import (
    CONTEXT_PREFIX,
    coding_context_update,
    compose_coding_context,
    compose_coding_system_prompt,
)
from koddi.dokky import coding
from koddi.remote.client import RemoteAIError
from koddi.tools import output_store
from koddi.tools.runner import tool_output_excerpt


def wire(messages):
    result = deepcopy(messages)
    for message in result:
        for call in message.get("tool_calls", []):
            call["function"]["name"] = call["function"]["name"].replace(".", "__")
    return result


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "stable.sqlite3")
    storage.init_db()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT RULE ONE", encoding="utf-8")
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,mode) VALUES ('Stable','development')").lastrowid
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Earlier')", (chat,))
    value = {
        "session": {"id": "cs_" + "d" * 24, "history": {"length": 0, "hash": ""}},
        "messages": [], "tools": [], "history": None,
    }
    state = {"chat": chat, "workspace": workspace, "canonical": value, "ensures": 0, "memory": []}

    async def ensure(_chat):
        assert _chat == chat
        state["ensures"] += 1
        return value

    async def discover():
        return [], {}, []

    monkeypatch.setattr(coding, "ensure", ensure)
    monkeypatch.setattr(coding, "load", lambda _: value)
    monkeypatch.setattr(coding, "pending", lambda _: None)
    monkeypatch.setattr(agent, "discover_agent_tools", discover)
    monkeypatch.setattr(agent, "retrieve_memory", lambda *_: state["memory"])
    monkeypatch.setattr(agent, "remote_config", lambda: {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000})
    state['transitions'] = []

    async def transition(_chat, **kwargs):
        from koddi.dokky.context import recent_tail

        state['transitions'].append(deepcopy(value))
        yield {'event':'context.compacting', 'text':'Сжимаю контекст…'}
        value['messages'] = [value['messages'][0], {'role':'user','content':'Verified work checkpoint'}, *recent_tail(value['messages'])]
        value['session']['id'] = 'cs_' + 'f' * 24
        value['history'] = None
        with storage.db() as conn:
            value['session']['journal_rebase_after'] = conn.execute('SELECT MAX(id) FROM agent_history').fetchone()[0]
        yield {'event':'context.compacted', 'text':'Контекст сжат'}

    monkeypatch.setattr('koddi.dokky.context.transition', transition)
    return state


class Model:
    config = {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000}

    def __init__(self, context, replies):
        self.state = context
        self.replies = iter(replies)
        self.requests = []
        self.summaries = []

    async def stream(self, **request):
        if request["messages"][0]["content"] == SUMMARY_PROMPT:
            self.summaries.append(deepcopy(request))
            yield {"content": "Verified work retained. Inspect current files before repeating an uncertain action.", "finish_reason": "stop"}
            return
        self.requests.append(deepcopy(request))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        assistant = {"role": "assistant", "content": reply.get("content") or None}
        if "tool_call" in reply:
            call = reply["tool_call"]
            assistant["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": call["arguments"],
            }}]
        value = self.state["canonical"]
        value["messages"] = wire(request["messages"] + [assistant])
        value["tools"] = deepcopy(request["tools"])
        for tool in value["tools"]:
            tool["function"]["name"] = tool["function"]["name"].replace(".", "__")
        value["history"] = {"length": len(value["messages"]), "hash": f"test-{len(self.requests)}"}
        yield deepcopy(reply)


async def run(context, model, task="Настоящая задача", **kwargs):
    return [event async for event in agent.AgentOrchestrator(model).run(
        chat_id=context["chat"], project_id=None, workspace=context["workspace"],
        user_message=task, **kwargs,
    )]


def final(text="Проверено."):
    return {"content": text, "finish_reason": "stop"}


def records(context):
    with storage.db() as conn:
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM agent_history WHERE chat_id=? ORDER BY id", (context["chat"],)
        )]


def seed(context, messages):
    value = context["canonical"]
    value["messages"] = deepcopy(messages)
    value["history"] = {"length": len(messages), "hash": "initial"}
    with storage.db() as conn:
        conn.executemany("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", [
            (context["chat"], json.dumps(message, ensure_ascii=False)) for message in messages
        ])


def test_system_is_complete_stable_and_has_no_runtime_data(context):
    system = compose_coding_system_prompt()
    first = compose_coding_context(mode="development", workspace=context["workspace"], project_id=None)
    (context["workspace"] / "AGENTS.md").write_text("CHANGED PROJECT RULE", encoding="utf-8")
    storage.set_setting("context_budget", "large")
    second = compose_coding_context(mode="review", workspace=context["workspace"], project_id=None)
    assert compose_coding_system_prompt() == system
    assert str(context["workspace"]) not in system and "PROJECT RULE" not in system
    assert "Plan mode" in system and "Development mode" in system
    assert "native function" in system and "private reasoning" in system
    assert first != second and "CHANGED PROJECT RULE" in second["content"]
    update = coding_context_update(second, [first])
    delta = json.loads(update["content"][len(CONTEXT_PREFIX):])
    assert delta["mode"] == "review" and "active_mode_instructions" in delta
    assert "workspace" not in delta
    assert coding_context_update(second, [first, update]) is None


def test_work_depth_catalog_is_in_system_and_active_depth_only_in_context(context):
    system = compose_coding_system_prompt()
    for name in ("Light: fast and precise", "Pro: thorough and researched", "Max: exhaustive"):
        assert name in system
    assert "Докки" in system and "You are Koddi" not in system and "Koddi uses" not in system
    light = compose_coding_context(mode="development", workspace=context["workspace"], project_id=None)
    deep = compose_coding_context(mode="development", workspace=context["workspace"], project_id=None, depth="max")
    assert json.loads(light["content"][len(CONTEXT_PREFIX):])["work_depth"] == "light"
    update = coding_context_update(deep, [light])
    # смена глубины доходит до модели одной дельтой, system и остальной снимок не меняются
    assert json.loads(update["content"][len(CONTEXT_PREFIX):]) == {"work_depth": "max"}
    forged = compose_coding_context(mode="development", workspace=context["workspace"], project_id=None, depth="ultra")
    assert json.loads(forged["content"][len(CONTEXT_PREFIX):])["work_depth"] == "light"


def test_turn_mode_follows_pending_switch_then_session_then_setting(context, monkeypatch):
    monkeypatch.setattr(coding, "load", lambda _chat: None)
    storage.set_setting("coding_reasoning_mode", "pro")
    assert coding.turn_mode(context["chat"]) == "pro"
    storage.set_setting("coding_reasoning_mode", "bogus")
    assert coding.turn_mode(context["chat"]) == "pro"
    monkeypatch.setattr(coding, "load", lambda _chat: {"session": {"reasoning_mode": "pro"}})
    assert coding.turn_mode(context["chat"]) == "pro"
    monkeypatch.setattr(coding, "load", lambda _chat: {"session": {"reasoning_mode": "pro", "pending_reasoning_mode": "max"}})
    assert coding.turn_mode(context["chat"]) == "max"


@pytest.mark.asyncio
async def test_two_turns_keep_system_tools_and_canonical_prefix_with_context_only_in_tail(context):
    model = Model(context, [final("Первая задача готова."), final("Вторая задача готова.")])
    assert "turn.completed" in (await run(context, model, "Первая задача"))[-1]
    first = deepcopy(context["canonical"]["messages"])
    (context["workspace"] / "AGENTS.md").write_text("PROJECT RULE TWO", encoding="utf-8")
    context["memory"] = [{"content": "Verified project memory"}]
    storage.set_setting("permission_profile", "read-only")
    assert "turn.completed" in (await run(context, model, "Вторая задача", mode="review"))[-1]
    before, after = model.requests
    assert after["messages"][:len(first)] == first
    assert before["tools"] == after["tools"]
    assert before["messages"][0] == after["messages"][0]
    assert sum(message["role"] == "system" for message in after["messages"]) == 1
    assert not any(message["role"] == "developer" for message in after["messages"])
    tail = after["messages"][len(first):]
    assert [message["role"] for message in tail] == ["user", "user"]
    data = json.loads(tail[0]["content"][len(CONTEXT_PREFIX):])
    assert data["mode"] == "review" and "PROJECT RULE TWO" in data["project_instructions"]
    assert data["memory"] == ["Verified project memory"]
    assert "workspace" not in data and "## Active mode" not in tail[0]["content"]
    assert tail[-1]["content"] == "Вторая задача"
    assert not any(record.get(CHECKPOINT) for record in records(context))


@pytest.mark.asyncio
async def test_new_chats_share_tool_order_despite_mcp_discovery_order(context, monkeypatch):
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,mode) VALUES ('Other','development')").lastrowid
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Earlier')", (chat,))
    second = dict(context, chat=chat, canonical={
        "session": {"id": "cs_" + "e" * 24, "history": {"length": 0, "hash": ""}},
        "messages": [], "tools": [], "history": None,
    })
    states = {context["chat"]: context, chat: second}
    count = 0

    async def ensure(chat_id):
        return states[chat_id]["canonical"]

    async def discover():
        nonlocal count
        count += 1
        names = ["mcp_z", "mcp_a"] if count == 1 else ["mcp_a", "mcp_z"]
        return [{"type": "function", "function": {
            "name": name, "description": name,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }} for name in names], {name: ("test", name) for name in names}, []

    monkeypatch.setattr(coding, "ensure", ensure)
    monkeypatch.setattr(coding, "load", lambda chat_id: states[chat_id]["canonical"])
    monkeypatch.setattr(agent, "discover_agent_tools", discover)
    first_model, second_model = Model(context, [final()]), Model(second, [final()])
    assert "turn.completed" in (await run(context, first_model))[-1]
    assert "turn.completed" in (await run(second, second_model))[-1]
    first, other = first_model.requests[0], second_model.requests[0]
    assert first["tools"] == other["tools"]
    assert first["messages"][0] == other["messages"][0]
    names = [item["function"]["name"] for item in first["tools"]]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_existing_pinned_tool_order_is_not_resorted(context):
    source = agent.tool_schemas_for_mode("development")[:3]
    pinned = [{"type": item["type"], "function": deepcopy(item["function"])} for item in reversed(source)]
    for item in pinned:
        item["function"]["name"] = item["function"]["name"].replace(".", "__")
    context["canonical"]["tools"] = deepcopy(pinned)
    seed(context, [{"role": "system", "content": "Pinned policy"}, {"role": "assistant", "content": "Earlier result"}])
    model = Model(context, [final()])
    assert "turn.completed" in (await run(context, model))[-1]
    actual = deepcopy(model.requests[0]["tools"])
    for item in actual:
        item["function"]["name"] = item["function"]["name"].replace(".", "__")
    assert actual == pinned


@pytest.mark.asyncio
async def test_canonical_calls_results_and_legacy_empty_message_are_not_rewritten(context):
    prefix = [
        {"role": "system", "content": "Pinned old system"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "old", "type": "function", "function": {
            "name": "file__read", "arguments": '{  "path" : "old.py"  }',
        }}]},
        {"role": "tool", "tool_call_id": "old", "content": '{  "ok" : true, "output_file" : "old-reference" }'},
        {"role": "assistant", "content": "Previous result"},
    ]
    seed(context, prefix)
    model = Model(context, [final()])
    assert "turn.completed" in (await run(context, model))[-1]
    assert model.requests[0]["messages"][:len(prefix)] == prefix
    assert records(context)[:len(prefix)] == prefix
    assert not any(record.get("reason") == "protocol_repair" for record in records(context))


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_checkpoint", [False, True])
async def test_recovered_journal_cutoff_does_not_replay_repeated_answers_or_conflicting_users(context, stale_checkpoint):
    server = [
        {"role": "user", "content": "U1"}, {"role": "assistant", "content": "Готово"},
        {"role": "user", "content": "U2"}, {"role": "assistant", "content": "Готово"},
    ]
    audit = [
        *server[:3], {"role": "assistant", "content": "Локальный ответ"},
        {"role": "user", "content": "U3"},
    ]
    if stale_checkpoint:
        audit.append({CHECKPOINT: 1, "reason": "compaction", "messages": [{"role": "user", "content": "U3"}],
                      "coding_base": {"session_id": context["canonical"]["session"]["id"],
                                      "history": {"length": len(server), "hash": "recovered"}}})
    seed(context, audit)
    value = context["canonical"]
    value.update(messages=deepcopy(server), history={"length": len(server), "hash": "recovered"})
    with storage.db() as conn:
        value["session"]["journal_rebase_after"] = conn.execute(
            "SELECT MAX(id) FROM agent_history WHERE chat_id=?", (context["chat"],)
        ).fetchone()[0]
    model = Model(context, [final(), final()])
    events = await run(context, model, "U4")
    assert "turn.completed" in events[-1]
    assert any("agent.warning" in event for event in events)
    sent = model.requests[0]["messages"]
    assert sent[:len(server)] == server
    assert [item["content"] for item in sent if item["role"] == "user" and not item["content"].startswith(CONTEXT_PREFIX)] == ["U1", "U2", "U4"]
    assert not any(item.get("content") in {"U3", "Локальный ответ"} for item in sent)
    assert records(context)[:len(audit)] == audit
    events = await run(context, model, "U5")
    assert "turn.completed" in events[-1]
    assert not any("agent.warning" in event for event in events)
    sent = model.requests[-1]["messages"]
    assert sum(item.get("content") == "U2" for item in sent) == 1
    assert sum(item.get("content") == "U4" for item in sent) == 1
    assert sum(item.get("content") == "U5" for item in sent) == 1
    assert not any(item.get("content") == "U3" for item in sent)


def test_actual_dokky_window_and_server_estimate_have_only_token_threshold():
    assert context_limit({"provider": "dokky"}) == 200000
    assert context_limit({"provider": "dokky", "context_tokens": 600000}) == 200000
    assert context_limit({"provider": "dokky", "context_tokens_limit": 120000}) == 120000
    assert context_limit({"context_tokens": 1000000}) == 600000
    short = [{"role": "assistant", "content": "x"}] * 3200
    assert not needs_compaction(short, [], {"provider": "dokky"})
    source = [{"role": "user", "content": "x" * 480000}]
    assert needs_compaction(source, [], {"provider": "dokky"})
    assert context_size(source, [], dokky=True) == len(json.dumps(source, ensure_ascii=False)) // 3 + len(json.dumps([])) // 3 + 2


@pytest.mark.asyncio
async def test_compaction_signals_exactly_next_stream_and_keeps_session_tools_and_audit(context):
    old = [
        {"role": "system", "content": "Pinned complete legacy policy"},
        {"role": "user", "content": "old evidence " * 45000},
        {"role": "assistant", "content": "Previous verified result"},
    ]
    seed(context, old)
    model = Model(context, [{"tool_call": {"id": "inspect", "name": "directory.list", "arguments": '{ "path": "." }'}, "finish_reason": "tool_calls"}, final()])
    events = await run(context, model)
    assert "turn.completed" in events[-1]
    assert any("context.compacted" in event for event in events)
    assert len(context['transitions']) == 1 and len(model.requests) == 2
    assert "_context_checkpoint" not in model.requests[0]["options"]
    assert "_context_checkpoint" not in model.requests[1]["options"]
    assert model.requests[0]["tools"] == model.requests[1]["tools"]
    assert model.requests[0]["messages"][0] == old[0]
    assert context["ensures"] == 2
    assert records(context)[:len(old)] == old
    assert not any(record.get("reason") == "compaction" for record in records(context))


def test_legacy_checkpoint_keeps_initial_privileges_and_latest_rules(context):
    leading = [{"role": "system", "content": "Stable system"}, {"role": "developer", "content": "Initial full restrictions"}]
    current = {"role": "developer", "content": "LATEST: review only, no writes"}
    user = {"role": "user", "content": "Review task"}
    runtime = compose_coding_context(mode="review", workspace=context["workspace"], project_id=None)
    source = [*leading, {"role": "user", "content": "old " * 10000}, current, user]
    result = compacted_messages(source, "Only review remains", user, [], current_context=runtime)
    assert initial_instructions(result) == leading
    assert result.index(current) > len(leading)
    assert result.index(current) < result.index(runtime)
    assert result.count(user) == 1 and "Review mode" in runtime["content"]
    value = deepcopy(context["canonical"])
    value.update(messages=source, history={"length": len(source), "hash": "legacy"})
    body = coding.prepare(value, result, [], checkpoint=True)
    assert "history" not in body and initial_instructions(body["messages"]) == leading


@pytest.mark.asyncio
async def test_unconfirmed_checkpoint_survives_restart_without_replaying_old_prefix(context):
    old = [
        {"role": "system", "content": "Pinned instructions"},
        {"role": "user", "content": "old evidence " * 45000},
        {"role": "assistant", "content": "Earlier verified result"},
    ]
    seed(context, old)
    first = Model(context, [RemoteAIError("Тестовый обрыв до подтверждения")])
    assert "turn.failed" in (await run(context, first))[-1]
    assert context["canonical"]["messages"][0] == old[0]
    assert context["canonical"]["session"]["id"] == 'cs_' + 'f' * 24
    second = Model(context, [final()])
    assert "turn.completed" in (await run(context, second, "Продолжи после обрыва"))[-1]
    assert not second.summaries and len(second.requests) == 1
    request = second.requests[0]
    assert "_context_checkpoint" not in request["options"]
    assert len(json.dumps(request["messages"], ensure_ascii=False)) < 20000
    assert request["messages"][0] == old[0]
    assert len(context['transitions']) == 1


@pytest.mark.asyncio
async def test_mode_change_keeps_schemas_but_disallows_write_server_side(context):
    model = Model(context, [final(), {"tool_call": {
        "id": "denied", "name": "file.create", "arguments": '{"path":"forbidden.py","content":"pass"}'
    }, "finish_reason": "tool_calls"}, final()])
    assert "turn.completed" in (await run(context, model))[-1]
    assert "turn.completed" in (await run(context, model, mode="review"))[-1]
    assert model.requests[0]["tools"] == model.requests[1]["tools"] == model.requests[2]["tools"]
    result = json.loads(model.requests[-1]["messages"][-1]["content"])
    assert result["ok"] is False and "недоступен" in result["error"]
    assert not (context["workspace"] / "forbidden.py").exists()


@pytest.mark.asyncio
async def test_missing_result_is_appended_as_unknown_without_reexecuting_canonical_call(context):
    prefix = [
        {"role": "system", "content": "Stable policy"},
        {"role": "user", "content": "Earlier real task"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "uncertain", "type": "function", "function": {
            "name": "file__create", "arguments": '{ "path": "never-replay.py", "content": "pass" }'
        }}]},
    ]
    seed(context, prefix)
    model = Model(context, [final()])
    assert "turn.completed" in (await run(context, model))[-1]
    request = model.requests[0]["messages"]
    assert request[:len(prefix)] == prefix
    assert request[len(prefix)]["role"] == "tool"
    assert request[len(prefix)]["tool_call_id"] == "uncertain"
    assert json.loads(request[len(prefix)]["content"])["interrupted"] is True
    assert not (context["workspace"] / "never-replay.py").exists()


@pytest.mark.asyncio
async def test_failed_summary_does_not_commit_checkpoint_or_change_canonical(context, monkeypatch):
    old = [
        {"role": "system", "content": "Stable policy"},
        {"role": "user", "content": "evidence " * 65000},
        {"role": "assistant", "content": "Verified earlier"},
    ]
    seed(context, old)

    async def failed_transition(*args, **kwargs):
        yield {'event':'context.compacting', 'text':'Сжимаю контекст…'}
        raise ValueError('Пустая сводка')

    monkeypatch.setattr('koddi.dokky.context.transition', failed_transition)

    class EmptySummary(Model):
        async def stream(self, **request):
            assert request["messages"][0]["content"] == SUMMARY_PROMPT
            yield {"content": "", "finish_reason": "stop"}

    model = EmptySummary(context, [])
    assert "turn.failed" in (await run(context, model))[-1]
    assert context["canonical"]["messages"] == old
    assert not any(record.get(CHECKPOINT) for record in records(context))


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["agent_loop_limit", "coding_session_expired"])
async def test_limit_or_expired_session_is_reported_without_new_session_or_synthetic_user(context, code):
    model = Model(context, [RemoteAIError("Явное ограничение сервера", code=code)])
    events = await run(context, model)
    assert "turn.failed" in events[-1] and code in events[-1]
    assert len(model.requests) == 1 and not model.summaries and context["ensures"] == 1
    assert [record["content"] for record in records(context) if record.get("role") == "user" and not record["content"].startswith(CONTEXT_PREFIX)] == ["Настоящая задача"]
    assert not any(record.get(CHECKPOINT) for record in records(context))


@pytest.mark.asyncio
async def test_truncated_answer_stays_partial_and_waits_for_real_user(context):
    model = Model(context, [{"content": "Частичная реализация", "finish_reason": "length"}])
    events = await run(context, model)
    assert "response_truncated" in events[-1] and len(model.requests) == 1
    assert records(context)[-1] == {"role": "assistant", "content": "Частичная реализация"}
    with storage.db() as conn:
        row = conn.execute("SELECT content,metadata_json FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "Частичная реализация" and json.loads(row[1])["status"] == "truncated"


@pytest.mark.asyncio
async def test_incomplete_plan_does_not_inject_an_automatic_user(context):
    with storage.db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'completed')", (context["chat"],)).lastrowid
        conn.execute("INSERT INTO agent_plans(turn_id,steps_json) VALUES (?,?)", (turn, json.dumps([{"title": "Unfinished", "status": "pending"}])))
    model = Model(context, [final("Ещё не всё готово.")])
    events = await run(context, model)
    assert "turn.failed" in events[-1] and len(model.requests) == 1
    assert len([record for record in records(context) if record.get("role") == "user"]) == 2


@pytest.mark.asyncio
async def test_caller_cannot_enable_checkpoint_without_compaction(context):
    model = Model(context, [final()])
    assert "turn.completed" in (await run(context, model, options={"_context_checkpoint": True}))[-1]
    assert "_context_checkpoint" not in model.requests[0]["options"]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_rules", [False, True])
async def test_new_large_result_has_deterministic_head_tail_and_keeps_full_local_output(
    context, tmp_path, monkeypatch, with_rules,
):
    result = {"ok": True, "output": "HEAD_OF_OUTPUT\n" + "middle evidence\n" * 7000 + "TAIL_OF_OUTPUT"}
    rules = [{"path": "pkg/AGENTS.md", "scope": "pkg", "content": "Complete scoped rules"}]
    if with_rules:
        result["project_instructions"] = rules
    original = json.dumps(result, ensure_ascii=False)
    ordinary = json.dumps({key: value for key, value in result.items() if key != "project_instructions"}, ensure_ascii=False)
    monkeypatch.setattr(output_store, "data_dir", lambda: tmp_path / "profile")

    async def execute(*args, **kwargs):
        yield {"result": deepcopy(result)}

    monkeypatch.setattr(agent, "execute_tool_events", execute)
    model = Model(context, [{"tool_call": {
        "id": "large", "name": "directory.list", "arguments": '{"path":"."}'
    }, "finish_reason": "tool_calls"}, final(), final("Следующая задача.")])
    assert "turn.completed" in (await run(context, model))[-1]
    response = model.requests[1]["messages"][-1]
    payload = json.loads(response["content"])
    assert payload["truncated"] is True and payload["read_tool"] == "tool.output_read"
    assert payload["excerpt"] == tool_output_excerpt(ordinary, 20000)
    assert len(payload["excerpt"]) <= 20000
    assert "HEAD_OF_OUTPUT" in payload["excerpt"] and "TAIL_OF_OUTPUT" in payload["excerpt"]
    assert "пропущено" in payload["excerpt"]
    if with_rules:
        assert payload["project_instructions"] == rules
    path, turn, _ = output_store.output_path(payload["output_id"])
    assert path.read_text(encoding="utf-8") == original
    assert payload["size_bytes"] == len(original.encode("utf-8"))
    assert output_store.read_tool_output(payload["output_id"], turn, max_chars=128)["content"] == original[:128]
    prefix = deepcopy(context["canonical"]["messages"])
    assert "turn.completed" in (await run(context, model, "Следующая настоящая задача"))[-1]
    assert model.requests[-1]["messages"][:len(prefix)] == prefix
    persisted = [record for record in records(context) if record.get("role") == "tool" and record.get("tool_call_id") == "large"]
    assert persisted == [response]
    with storage.db() as conn:
        rows = conn.execute("SELECT metadata_json FROM messages WHERE role='assistant'").fetchall()
    outcomes = [event["data"]["result"] for row in rows for event in json.loads(row[0]).get("events", []) if event["event"] == "tool.result"]
    assert result in outcomes


def test_import_tools_before_remote_does_not_cycle(tmp_path):
    env = dict(os.environ, KODDI_DATA_DIR=str(tmp_path / "profile"))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "backend")
    result = subprocess.run([sys.executable, "-c", "import koddi.tools; import koddi.remote.client; import koddi.agents.prompts"], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
