import asyncio
import copy
import json
import time

import httpx
import pytest

import koddi.storage.db as storage
from koddi.dokky import coding
from koddi.dokky.cache import record_cache
from koddi.dokky.client import API, DokkyClient, DokkyError
from koddi.remote.client import RemoteAIClient


SID = "cs_" + "d" * 24
TOOLS = [{"type": "function", "function": {
    "name": "file.read", "description": "Read a project file",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
}}]


class Store:
    def load(self):
        return {"access_token": "test", "refresh_token": "test", "expires_at": time.time() + 900}

    def clear(self):
        pass


def session(history=None):
    return {"id": SID, "reasoning_mode": "pro", "version": 1, "history": history or {"length": 0, "hash": ""}}


def reply(assistant, history, **metadata):
    finish = "tool_calls" if assistant.get("tool_calls") else "stop"
    delta = {key: value for key, value in assistant.items() if key != "role"}
    if "tool_calls" in delta:
        delta["tool_calls"] = [dict(call, index=index) for index, call in enumerate(delta["tool_calls"])]
    frames = [
        {"choices": [{"delta": delta}]},
        {"choices": [{"delta": {}, "finish_reason": finish}]},
        {"choices": [], "usage": {"prompt_tokens": 12000, "context_tokens_limit": 200000}, "dokky": {"history": history, **metadata}},
    ]
    return httpx.Response(200, text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n")


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "cache.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "cache-user"})
    with storage.db() as conn:
        return conn.execute("INSERT INTO chats(title,mode) VALUES ('Cache','development')").lastrowid


def client_for(handler, monkeypatch):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("koddi.dokky.client.dokky", DokkyClient(transport=transport, store=Store()))
    return RemoteAIClient({"provider": "dokky", "base_url": API}, transport=transport)


async def collect(client, chat, messages, tools=None, **options):
    return [event async for event in client.stream(
        model="dokky-code", messages=messages, tools=TOOLS if tools is None else tools,
        options={"_chat_id": chat, **options},
    )]


@pytest.mark.asyncio
async def test_eight_steps_send_only_tail_and_preserve_raw_arguments(chat, monkeypatch):
    canonical, seen = [], []
    raw_arguments = '{ "path" : "main.py" }'

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        body = json.loads(request.content)
        seen.append((request.headers["Idempotency-Key"], body))
        if len(seen) > 1:
            assert body["history"] == {"base_length": len(canonical), "base_hash": str(len(canonical))}
            assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "tool"
        canonical.extend(body["messages"])
        assistant = {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"call_{len(seen)}", "type": "function",
            "function": {"name": "file__read", "arguments": raw_arguments},
        }]}
        canonical.append(assistant)
        return reply(assistant, {"length": len(canonical), "hash": str(len(canonical))},
                     cache={"status": "cold" if len(seen) == 1 else "hit", "ratio": .9, "cached_tokens": 10000, "prompt_tokens": 12000, "gap_seconds": 2, "reasons": []},
                     usage={"cache_write_tokens": 500})

    client = client_for(handler, monkeypatch)
    messages = [{"role": "system", "content": "Stable complete instructions"}, {"role": "user", "content": "Read main.py"}]
    for index in range(8):
        await collect(client, chat, messages)
        stored = coding.load(chat)
        assert stored["messages"] == canonical
        assert stored["messages"][-1]["tool_calls"][0]["function"]["arguments"] == raw_arguments
        messages = copy.deepcopy(stored["messages"])
        for message in messages:
            for call in message.get("tool_calls", []):
                call["function"]["arguments"] = json.dumps(json.loads(call["function"]["arguments"]), separators=(",", ":"))
        messages.append({"role": "tool", "tool_call_id": f"call_{index + 1}", "content": "fixed result\n"})
    assert len({key for key, _ in seen}) == 8
    assert all(body["tools"] == seen[0][1]["tools"] for _, body in seen)
    assert coding.load(chat)["session"]["client_cache"]["counts"]["hit"] == 7
    with storage.db() as conn:
        logs = [json.loads(row[0]) for row in conn.execute("SELECT data_json FROM logs WHERE event='ai.cache'")]
    assert len(logs) == 8 and logs[-1]["cache_write"] == 500
    assert "main.py" not in json.dumps(logs)


def test_prefix_mutation_requires_explicit_checkpoint():
    previous = [{"role": "system", "content": "Stable"}, {"role": "user", "content": "Old"}]
    value = {"session": session(), "messages": previous, "history": {"length": 2, "hash": "h"}}
    changed = [previous[0], {"role": "user", "content": "Summary"}, {"role": "user", "content": "New"}]
    with pytest.raises(DokkyError) as exc:
        coding.prepare(value, changed, [])
    assert exc.value.code == "history_changed"
    body = coding.prepare(value, changed, [], checkpoint=True)
    assert "history" not in body and body["messages"] == changed
    with pytest.raises(DokkyError) as exc:
        coding.prepare(value, [{"role": "system", "content": "Different"}, *changed[1:]], [], checkpoint=True)
    assert exc.value.code == "system_prompt_changed"
    assert value["messages"] == previous


@pytest.mark.asyncio
async def test_request_parameters_are_forwarded_without_internal_options(chat, monkeypatch):
    seen = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        seen.append(json.loads(request.content))
        return reply({"role": "assistant", "content": "Done"}, {"length": 2, "hash": "h"})

    options = {"seed": 3, "parallel_tool_calls": False, "response_format": {"type": "json_object"}, "tool_choice": "none", "stop": ["END"], "max_tokens": 4000, "top_p": .9, "temperature": .2}
    await collect(client_for(handler, monkeypatch), chat, [{"role": "user", "content": "Task"}], **options)
    assert all(seen[0][key] == value for key, value in options.items())
    assert "_chat_id" not in seen[0] and seen[0]["stream_options"] == {"include_usage": True}
    assert seen[0]["tools"] and seen[0]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_concurrent_session_request_is_rejected_and_cancel_releases_guard(chat, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        calls.append(request.headers["Idempotency-Key"])
        entered.set()
        await release.wait()
        return reply({"role": "assistant", "content": "Done"}, {"length": 2, "hash": "h"})

    client = client_for(handler, monkeypatch)
    messages = [{"role": "user", "content": "Task"}]
    first = asyncio.create_task(collect(client, chat, messages))
    await asyncio.wait_for(entered.wait(), 3)
    with pytest.raises(DokkyError) as exc:
        await collect(client, chat, messages)
    assert exc.value.code == "coding_busy" and len(calls) == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    await collect(client, chat, messages)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert not coding._active


@pytest.mark.asyncio
async def test_history_conflict_restores_server_without_overwriting_or_reexecuting(chat, monkeypatch):
    original = [{"role": "user", "content": "Task"}, {"role": "assistant", "content": "Old"}]
    remote = [original[0], {"role": "assistant", "content": "Changed elsewhere"}]
    posts, reads = [], []

    def handler(request):
        if request.method == "GET":
            reads.append(request.url.path)
            if request.url.path.endswith("/history"):
                return httpx.Response(200, json={"items": [{"message": item} for item in remote], "next_after": None, "history": {"length": 2, "hash": "remote"}})
            return httpx.Response(200, json=session({"length": 2, "hash": "remote"}))
        posts.append(json.loads(request.content))
        return httpx.Response(409, json={"error": {"code": "history_mismatch"}})

    coding.save(chat, {"session": session(), "messages": original, "history": {"length": 2, "hash": "old"}, "tools": []})
    with pytest.raises(DokkyError) as exc:
        await collect(client_for(handler, monkeypatch), chat, original + [{"role": "user", "content": "New"}], tools=[])
    assert exc.value.code == "history_changed"
    assert len(posts) == 1 and len(reads) == 2
    assert coding.load(chat)["messages"] == remote and coding.pending(chat) is None


@pytest.mark.asyncio
async def test_expired_session_is_recreated_once_without_reexecuting_tools(chat, monkeypatch):
    messages = [{"role": "user", "content": "Old"}]
    coding.save(chat, {"session": session(), "messages": messages, "tools": [], "history": None})
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith('/coding/sessions'):
            return httpx.Response(201, json={**session(), 'id':'cs_'+'e'*24})
        if len(seen)==1:
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        assert seen[-1][1]['session_id']=='cs_'+'e'*24
        assert 'history' not in seen[-1][1]
        return reply({'role':'assistant','content':'Recovered'}, {'length':3,'hash':'new'})

    await collect(client_for(handler, monkeypatch), chat, messages + [{"role": "user", "content": "Next"}], tools=[])
    assert len(seen)==3 and seen[1][0].endswith('/coding/sessions')
    assert coding.load(chat)['messages'][:1]==messages and coding.pending(chat) is None
    assert coding.load(chat)['session']['previous_session_id']==SID


def test_cache_diagnostics_are_bounded_and_replay_does_not_count_twice():
    state = session()
    metadata = {"cache": {"status": "miss", "prompt_tokens": 12000, "ratio": float("inf"), "gap_seconds": 3,
                           "reasons": ["history_rewritten", "Bearer PRIVATE", {"secret": "value"}]},
                "usage": {"cache_write_tokens": 12000}}
    for _ in range(3):
        record_cache(state, metadata, {}, [{"role": "tool", "content": "private"}])
    assert state["client_cache"]["suspect_streak"] == 3
    assert state["client_cache"]["last"]["reasons"] == ["history_rewritten"]
    record_cache(state, {**metadata, "replayed": True}, {}, [])
    assert state["client_cache"]["counts"]["miss"] == 3
    assert state["client_cache"]["suspect_streak"] == 3
    assert "private" not in json.dumps(state) and "PRIVATE" not in json.dumps(state)
    # деньги не сохраняются нигде, даже если сервер их прислал
    result = record_cache(state, {**metadata, "billing": {"charged_rub": "0.0043"}}, {}, [])
    assert "charged_rub" not in result and "0.0043" not in json.dumps(state)


@pytest.mark.asyncio
async def test_account_change_during_response_does_not_save_to_new_owner(chat, monkeypatch):
    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        storage.set_setting("dokky_user", {"id": "another-user"})
        return reply({"role": "assistant", "content": "Private response"}, {"length": 2, "hash": "h"})

    with pytest.raises(DokkyError) as exc:
        await collect(client_for(handler, monkeypatch), chat, [{"role": "user", "content": "Task"}])
    assert exc.value.code == "session_changed"
    assert coding.load(chat) is None
    storage.set_setting("dokky_user", {"id": "cache-user"})
    assert coding.load(chat)["messages"] == []


@pytest.mark.asyncio
async def test_closing_stream_closes_network_before_releasing_session(chat, monkeypatch):
    closed = asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial"}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        return httpx.Response(200, stream=SlowStream())

    stream = client_for(handler, monkeypatch).stream(
        model="dokky-code", messages=[{"role": "user", "content": "Task"}], tools=[], options={"_chat_id": chat},
    )
    assert await anext(stream) == {"content": "Partial"}
    assert coding._active
    await stream.aclose()
    assert closed.is_set() and not coding._active


@pytest.mark.asyncio
async def test_account_change_while_authorizing_cannot_send_or_move_pending(chat, monkeypatch):
    messages = [{"role": "user", "content": "Account A private task"}]
    coding.save(chat, {"session": session(), "messages": [], "tools": [], "history": None})
    seen = []

    def handler(request):
        seen.append(request)
        return reply({"role": "assistant", "content": "Unexpected"}, {"length": 2, "hash": "h"})

    client = client_for(handler, monkeypatch)

    async def authorize(http):
        storage.set_setting("dokky_user", {"id": "new-user"})
        http.headers["Authorization"] = "Bearer new-token"

    monkeypatch.setattr(client, "authorize", authorize)
    with pytest.raises(DokkyError) as exc:
        await collect(client, chat, messages, tools=[])
    assert exc.value.code == "session_changed" and seen == []
    assert coding.pending(chat) is None
    storage.set_setting("dokky_user", {"id": "cache-user"})
    assert coding.pending(chat) is None


@pytest.mark.asyncio
async def test_corrupt_messages_recovery_preserves_pinned_tools(chat, monkeypatch):
    messages = [{"role": "system", "content": "Stable"}, {"role": "user", "content": "Old"}, {"role": "assistant", "content": "Done"}]
    wire_tools = copy.deepcopy(TOOLS)
    wire_tools[0]["function"]["name"] = "file__read"
    coding.save(chat, {"session": session({"length": 3, "hash": "old"}), "messages": messages, "tools": wire_tools, "history": {"length": 3, "hash": "old"}})
    with storage.db() as conn:
        conn.execute("UPDATE coding_sessions SET messages_json='broken' WHERE chat_id=?", (chat,))
    seen = []

    def handler(request):
        if request.method == "GET":
            if request.url.path.endswith("/history"):
                return httpx.Response(200, json={"items": [{"message": item} for item in messages], "next_after": None})
            return httpx.Response(200, json=session({"length": 3, "hash": "old"}))
        body = json.loads(request.content)
        seen.append(body)
        return reply({"role": "assistant", "content": "Next result"}, {"length": 5, "hash": "new"})

    client = client_for(handler, monkeypatch)
    await collect(client, chat, [*messages, {"role": "user", "content": "Next"}])
    assert len(seen) == 1 and seen[0]["tools"] == wire_tools
    assert seen[0]["messages"] == [{"role": "user", "content": "Next"}]
    assert coding.pending(chat) is None


@pytest.mark.parametrize("cache", [{"status": []}, {"status": {}}, {"status": "hit", "prompt_tokens": 10**1000}])
@pytest.mark.asyncio
async def test_malformed_optional_cache_does_not_poison_success(chat, monkeypatch, cache):
    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        return reply({"role": "assistant", "content": "Done"}, {"length": 2, "hash": "h"}, cache=cache)

    events = await collect(client_for(handler, monkeypatch), chat, [{"role": "user", "content": "Task"}], tools=[])
    assert any(event.get("content") == "Done" for event in events)
    assert coding.load(chat)["messages"][-1]["content"] == "Done"
    assert coding.pending(chat) is None


def test_divergent_recovery_does_not_match_repeated_old_assistant_as_unsent_tail(chat):
    u1, u2, u3 = [{"role": "user", "content": text} for text in ("U1", "U2", "U3")]
    done = {"role": "assistant", "content": "Done"}
    journal = [u1, done, u2, {"role": "assistant", "content": "Local answer"}, u3]
    remote = [u1, done, u2, done]
    with storage.db() as conn:
        conn.executemany("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", [(chat, json.dumps(message)) for message in journal])
    value = {"session": session(), "messages": remote, "tools": [], "history": {"length": 4, "hash": "h"}}
    coding.isolate_recovered_journal(chat, value)
    saved = coding.load(chat)
    tail = coding.unconfirmed_tail(remote, coding.journal_since_recovery(chat, saved, journal))
    assert tail == []
    with storage.db() as conn:
        assert conn.execute("SELECT count(*) FROM agent_history WHERE chat_id=?", (chat,)).fetchone()[0] == 5
        result = {"role": "tool", "tool_call_id": "fresh-id", "content": "new result"}
        conn.execute("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", (chat, json.dumps(result)))
    assert coding.journal_since_recovery(chat, saved, journal) == [result]


@pytest.mark.asyncio
async def test_retry_uses_frozen_bytes_even_if_callers_mutate_options_or_schema(chat, monkeypatch):
    tools = copy.deepcopy(TOOLS)
    options = {"_chat_id": chat, "response_format": {"type": "json_object"}}
    seen = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        seen.append((request.headers["Idempotency-Key"], request.content))
        if len(seen) == 1:
            tools[0]["function"]["parameters"]["properties"]["path"]["description"] = "Changed while waiting"
            options["response_format"]["type"] = "text"
            return httpx.Response(503, json={"error": {"code": "upstream_unavailable", "retryable": True}})
        return reply({"role": "assistant", "content": "Done"}, {"length": 2, "hash": "h"})

    async def no_wait(_):
        pass

    monkeypatch.setattr("koddi.remote.client.asyncio.sleep", no_wait)
    client = client_for(handler, monkeypatch)
    _ = [event async for event in client.stream(model="dokky-code", messages=[{"role": "user", "content": "Task"}], tools=tools, options=options)]
    assert len(seen) == 2 and seen[0] == seen[1]
    assert "description" not in coding.load(chat)["tools"][0]["function"]["parameters"]["properties"]["path"]


@pytest.mark.asyncio
async def test_switch_mode_preserves_recovery_cutoff_and_cache_diagnostics(chat, monkeypatch):
    done = {"role": "assistant", "content": "Done"}
    u1, u2, u3 = [{"role": "user", "content": text} for text in ("U1", "U2", "U3")]
    journal = [u1, done, u2, {"role": "assistant", "content": "Local"}, u3]
    with storage.db() as conn:
        conn.executemany("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", [(chat, json.dumps(item)) for item in journal])
    value = {"session": session({"length": 4, "hash": "h"}), "messages": [u1, done, u2, done], "tools": [], "history": {"length": 4, "hash": "h"}}
    value["session"]["client_cache"] = {"counts": {"hit": 7}}
    coding.isolate_recovered_journal(chat, value)

    def handler(request):
        if request.url.path.endswith('/switch-plan'):
            return httpx.Response(200, json={"recommended":"switch", "to":"max"})
        assert request.method == "PATCH"
        return httpx.Response(200, json={**session({"length": 4, "hash": "h"}), "reasoning_mode": "max", "version": 2})

    client_for(handler, monkeypatch)
    await coding.switch_mode(chat, "max", True)
    _ = [event async for event in coding.apply_pending_mode(chat)]
    saved = coding.load(chat)
    assert saved["session"]["reasoning_mode"] == "max"
    assert saved["session"]["client_cache"] == {"counts": {"hit": 7}}
    assert coding.journal_since_recovery(chat, saved, journal) == []


@pytest.mark.parametrize('usage', [
    {'prompt_tokens': 65385, 'prompt_tokens_details': {'cached_tokens': 65382, 'cache_write_tokens': 3}},
    {'input_tokens': 65385, 'input_tokens_details': {'cached_tokens': 65382, 'cache_write_tokens': 3}},
])
def test_cache_diagnostics_read_real_nested_usage_not_just_optional_metadata(usage):
    value = record_cache({}, {'cache': {'status': 'hit'}}, usage, [])
    assert value['cache_read'] == 65382 and value['prompt'] == 65385
    assert value['cache_write'] == 3 and value['ratio'] == 65382 / 65385


def test_usage_zero_is_authoritative_but_bad_values_fall_back_safely():
    metadata = {'cache': {'status': 'partial', 'cached_tokens': 500}, 'usage': {'cache_write_tokens': 100}}
    value = record_cache({}, metadata, {'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': 0}}, [])
    assert value['cache_read'] == 0 and value['ratio'] == 0
    value = record_cache({}, metadata, {'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': float('nan')}}, [])
    assert value['cache_read'] == 500 and value['ratio'] == .5
