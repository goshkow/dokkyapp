from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import koddi.storage.db as storage
from koddi.agents.compaction import context_size
from koddi.dokky import coding
from koddi.dokky.client import DokkyError
from koddi.dokky.context import (
    compact_threshold,
    keepalive,
    prune_outputs,
    recent_tail,
    should_compact,
    transition,
)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "context.sqlite3")
    storage.init_db()
    from koddi.dokky import activity

    activity._active.clear()
    storage.set_setting("dokky_user", {"id": "context-test"})
    with storage.db() as conn:
        chat_id = conn.execute(
            "INSERT INTO chats(title,mode,owner_id) VALUES ('Контекст','development','context-test')"
        ).lastrowid
        conn.execute(
            "INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)",
            (chat_id, '{"role":"user","content":"audit"}'),
        )
    messages = [
        {"role": "system", "content": "Pinned system"},
        {"role": "user", "content": "old facts " * 16000},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "read",
                    "type": "function",
                    "function": {"name": "file__read", "arguments": '{ "path": "a.py" }'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "read", "content": "File contents"},
        {"role": "assistant", "content": "Verified edits"},
    ]
    history = {"length": len(messages), "hash": "old-hash"}
    coding.save(
        chat_id,
        {
            "session": {
                "id": "cs_" + "a" * 24,
                "reasoning_mode": "pro",
                "version": 1,
                "history": history,
            },
            "messages": messages,
            "history": history,
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "file__read", "parameters": {"type": "object"}},
                }
            ],
        },
    )
    return chat_id


class Client:
    def __init__(self):
        self.requests = []
        self.fail_create = False
        self.fail_summary = False
        self.truncate = 0
        self.action = "switch"

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, deepcopy(kwargs)))
        if path.endswith("/switch-plan"):
            return httpx.Response(
                200, json={"to": "max", "recommended": self.action, "options": []}
            )
        if path == "/chat/completions":
            if self.fail_summary:
                self.fail_summary = False
                raise DokkyError("Сеть")
            truncated = self.truncate > 0
            self.truncate -= 1
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length" if truncated else "stop",
                            "message": {"role": "assistant", "content": "Verified checkpoint"},
                        }
                    ],
                    "dokky": {
                        "history": {"length": 7 if truncated else 9, "hash": "summary"},
                        "billing": {"charged_rub": "0.0021"},
                    },
                },
            )
        if path == "/coding/sessions":
            if self.fail_create:
                self.fail_create = False
                raise DokkyError("Сеть")
            return httpx.Response(
                201,
                json={
                    "id": "cs_" + "b" * 24,
                    "version": 1,
                    "reasoning_mode": kwargs["json"]["reasoning_mode"],
                    "history": {"length": 0, "hash": ""},
                },
            )
        if method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "id": "cs_" + "a" * 24,
                    "version": 2,
                    "reasoning_mode": "max",
                    "history": {"length": 5, "hash": "old-hash"},
                },
            )
        if path.endswith("/warm"):
            return httpx.Response(
                200,
                json={
                    "warmed": True,
                    "cache": {
                        "valid_until": (
                            datetime.now(timezone.utc) + timedelta(minutes=30)
                        ).isoformat(),
                        "prompt_tokens": 12000,
                    },
                },
            )
        raise AssertionError((method, path))


async def compact(chat, client, **kwargs):
    return [event async for event in transition(chat, client=client, **kwargs)]


def test_policy_uses_server_mode_threshold_and_boundary(chat):
    config = {
        "provider": "dokky",
        "context_tokens": 200000,
        "models": [
            {"id": "dokky-code", "reasoning_modes": [{"id": "pro", "compact_at_tokens": 47000}]}
        ],
    }
    assert compact_threshold(config, "pro") == 47000
    value = coding.load(chat)
    assert should_compact(value, config, boundary=True)
    assert not should_compact(value, config, boundary=False)
    value["session"]["context_policy"] = {"steps_since_compaction": 7}
    assert not should_compact(value, config, boundary=True)
    value["session"]["context_policy"]["steps_since_compaction"] = 8
    assert should_compact(value, config, boundary=True)
    assert compact_threshold(config | {"context_tokens_limit": 40000}, "pro") == 36000
    value["session"]["context_policy"]["failures"] = 3
    assert not should_compact(value, config, boundary=True)


@pytest.mark.asyncio
async def test_summary_current_mode_new_session_and_verbatim_tail(chat):
    old = coding.load(chat)
    client = Client()
    events = await compact(chat, client, target_mode="max")
    assert [event["event"] for event in events] == ["context.compacting", "context.compacted"]
    body = client.requests[0][2]["json"]
    assert body["purpose"] == "compaction" and body["reasoning_mode"] == "pro"
    assert body["tools"] == old["tools"] and body["tool_choice"] == "none"
    assert body["max_tokens"] == 6000 and body["history"] == {
        "base_length": 5,
        "base_hash": "old-hash",
    }
    assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
    saved = coding.load(chat)
    assert (
        saved["session"]["reasoning_mode"] == "max"
        and saved["session"]["previous_session_id"] == old["session"]["id"]
    )
    assert saved["history"] is None and saved["tools"] == old["tools"]
    assert saved["messages"][0] == old["messages"][0]
    assert saved["messages"][-3:] == old["messages"][-3:]
    assert saved["messages"][-3]["tool_calls"][0]["function"]["arguments"] == '{ "path": "a.py" }'
    first = coding.prepare(
        saved, saved["messages"] + [{"role": "user", "content": "next"}], saved["tools"]
    )
    assert "history" not in first
    assert first["session_id"] == saved["session"]["id"]
    assert coding.journal_since_recovery(chat, saved, []) == []
    assert "billing" not in events[-1]
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_history").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["summary", "create"])
async def test_restart_reuses_exact_key_and_body_without_duplicate_summary(chat, phase):
    client = Client()
    client.fail_create = phase == "create"
    client.fail_summary = phase == "summary"
    with pytest.raises(DokkyError):
        await compact(chat, client)
    old = coding.load(chat)
    assert old["session"]["id"] == "cs_" + "a" * 24
    with pytest.raises(DokkyError, match="Сначала завершите"):
        coding.prepare(
            old, old["messages"] + [{"role": "user", "content": "new task"}], old["tools"]
        )
    await compact(chat, client)
    path = "/chat/completions" if phase == "summary" else "/coding/sessions"
    repeats = [req for req in client.requests if req[1] == path]
    assert len(repeats) == 2 and repeats[0] == repeats[1]
    if phase == "create":
        assert len([req for req in client.requests if req[1] == "/chat/completions"]) == 1


@pytest.mark.asyncio
async def test_cancel_leaves_original_and_resumable_operation(chat):
    client = Client()
    iterator = transition(chat, client=client)
    assert (await anext(iterator))["event"] == "context.compacting"
    await iterator.aclose()
    assert not client.requests
    assert coding.load(chat)["session"]["id"] == "cs_" + "a" * 24
    await compact(chat, client)
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_account_switch_before_network_does_not_send_old_history(chat):
    client = Client()
    iterator = transition(chat, client=client)
    await anext(iterator)
    storage.set_setting("dokky_user", {"id": "other"})
    with pytest.raises(DokkyError, match="Вход изменился"):
        await anext(iterator)
    assert not client.requests
    assert coding.load(chat) is None


@pytest.mark.asyncio
async def test_truncated_summary_retries_once_as_new_logical_request(chat):
    client = Client()
    client.truncate = 1
    await compact(chat, client)
    requests = [req[2] for req in client.requests if req[1] == "/chat/completions"]
    assert len(requests) == 2 and requests[0]["key"] != requests[1]["key"]
    assert requests[1]["json"]["history"] == {"base_length": 7, "base_hash": "summary"}
    assert requests[1]["json"]["purpose"] == "compaction"


@pytest.mark.asyncio
async def test_repeated_truncation_keeps_canonical_state_and_counts_failure(chat):
    client = Client()
    client.truncate = 2
    with pytest.raises(DokkyError, match="Сводка снова"):
        await compact(chat, client)
    value = coding.load(chat)
    assert value["session"]["id"] == "cs_" + "a" * 24
    assert "context_transition" not in value["session"]
    assert value["session"]["context_policy"]["failures"] == 1
    assert value["messages"][0]["content"] == "Pinned system"
    assert not any(req[1] == "/coding/sessions" for req in client.requests)


def test_pruning_is_copy_only_and_protects_recent_outputs():
    messages = [
        {"role": "system", "content": "Stable"},
        {"role": "tool", "tool_call_id": "old", "content": "old\n" * 25000},
        {"role": "assistant", "content": "protected " * 14000},
        {"role": "tool", "tool_call_id": "new", "content": "fresh" * 3000},
    ]
    before = deepcopy(messages)
    pruned = prune_outputs(messages)
    assert messages == before
    assert pruned[1]["content"].startswith("[вывод удалён")
    assert pruned[-1] == messages[-1]
    assert context_size(messages, [], dokky=True) - context_size(pruned, [], dokky=True) >= 20000
    assert prune_outputs(messages[-2:]) == messages[-2:]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["switch", "compress_then_switch", "prune_then_switch"])
async def test_mode_switch_executes_server_recommendation(chat, action):
    client = Client()
    client.action = action
    if action == "prune_then_switch":
        value = coding.load(chat)
        value["messages"] = [
            value["messages"][0],
            {"role": "user", "content": "Task"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "old",
                        "type": "function",
                        "function": {"name": "file__read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "old", "content": "old\n" * 25000},
            {"role": "assistant", "content": "recent " * 18000},
        ]
        coding.save(chat, value)
    result = await coding.switch_mode(chat, "max", True, client)
    # выбор режима ничего не делает на сервере, пока не отправлено следующее сообщение
    assert result["pending_reasoning_mode"] == "max" and not client.requests
    _ = [event async for event in coding.apply_pending_mode(chat, client=client)]
    result = coding.load(chat)["session"]
    assert result["reasoning_mode"] == "max" and "pending_reasoning_mode" not in result
    assert client.requests[0][1].endswith("/switch-plan")
    assert any(req[0] == "PATCH" for req in client.requests) == (action == "switch")
    assert any(req[1] == "/chat/completions" for req in client.requests) == (
        action == "compress_then_switch"
    )


@pytest.mark.asyncio
async def test_session_lock_blocks_second_operation(chat):
    iterator = transition(chat, client=Client())
    await anext(iterator)
    with pytest.raises(DokkyError, match="уже выполняется"):
        await compact(chat, Client())
    await iterator.aclose()


@pytest.mark.asyncio
async def test_keepalive_default_on_only_while_active_and_due(chat):
    client = Client()
    from koddi.dokky.activity import mark

    assert await keepalive(chat, client) == {"warmed": False, "reason": "idle"}
    mark("context-test", chat)
    storage.set_setting("coding_keepalive:context-test", False)
    assert await keepalive(chat, client) == {"warmed": False, "reason": "disabled"}
    storage.set_setting("coding_keepalive:context-test", True)
    value = coding.load(chat)
    value["session"]["last_usage"] = {
        "dokky": {
            "cache": {
                "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(),
                "prompt_tokens": 12000,
            }
        }
    }
    coding.save(chat, value)
    assert (await keepalive(chat, client))["warmed"] is True
    assert client.requests[-1][2]["json"] == {"keepalive": True}
    assert (await keepalive(chat, client))["reason"] == "not_due"
    assert len(client.requests) == 1


def test_tail_cannot_split_tool_exchange():
    messages = [
        {"role": "system", "content": "fixed"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "x" * 50000},
    ]
    with pytest.raises(DokkyError, match="хвоста"):
        recent_tail(messages)
    assert recent_tail(messages, budget=20000) == messages[1:]


@pytest.mark.asyncio
async def test_small_context_does_not_leave_blocking_transition(chat):
    value = coding.load(chat)
    value["messages"] = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "done"},
    ]
    coding.save(chat, value)
    client = Client()
    with pytest.raises(DokkyError, match="сжатие пока не требуется"):
        await compact(chat, client)
    assert not client.requests
    assert "context_transition" not in coding.load(chat)["session"]


@pytest.mark.asyncio
async def test_retryable_error_reuses_frozen_request_and_respects_backoff(chat, monkeypatch):
    from koddi.dokky.context import request_step

    client = Client()
    original = client.request
    delays = []

    async def request(*args, **kwargs):
        if not delays:
            raise DokkyError(
                "Подождите",
                429,
                "rate_limited",
                details={"retryable": True, "retry_after_seconds": 4},
            )
        return await original(*args, **kwargs)

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr("koddi.dokky.context.asyncio.sleep", sleep)
    body = {"reasoning_mode": "pro"}
    await request_step(client, "/coding/sessions", identity="context-test", key="fixed", body=body)
    assert delays == [4]
    assert client.requests[0][2] == {"key": "fixed", "json": body}


def test_local_endpoints_hide_transition_and_reject_other_account(chat):
    from fastapi.testclient import TestClient
    from koddi.api import create_app

    value = coding.load(chat)
    value["session"]["context_transition"] = {
        "source": value["messages"],
        "body": {"private": "repository"},
    }
    coding.save(chat, value)
    client = TestClient(create_app())
    result = client.get(f"/api/chats/{chat}/coding-session").json()
    assert result["compaction_pending"] is True and "context_transition" not in result
    assert (
        client.put(
            "/api/settings", json={"coding_keepalive": True, "private_network": True}
        ).status_code
        == 422
    )
    assert client.get("/api/settings").json()["coding_keepalive"] is True
    assert client.put("/api/settings", json={"coding_keepalive": False}).status_code == 200
    storage.set_setting("dokky_user", {"id": "other"})
    assert client.get("/api/settings").json()["coding_keepalive"] is True
    for path in ("compact", "warm"):
        assert client.post(f"/api/chats/{chat}/coding-session/{path}").status_code == 404
    assert client.get(f"/api/chats/{chat}/coding-session/switch-plan?mode=max").status_code == 404


def test_write_after_account_change_cannot_reassign_private_context(chat):
    value = coding.load(chat)
    storage.set_setting("dokky_user", {"id": "other"})
    with pytest.raises(DokkyError, match="Вход изменился"):
        coding.save(chat, value, expected_owner="context-test")
    assert coding.load(chat) is None
    with storage.db() as conn:
        assert (
            conn.execute("SELECT user_id FROM coding_sessions WHERE chat_id=?", (chat,)).fetchone()[
                0
            ]
            == "context-test"
        )


@pytest.mark.asyncio
async def test_compaction_retains_latest_scoped_rules_and_runtime_context(chat):
    value = coding.load(chat)
    rules = {"role": "developer", "content": "Review only; writes forbidden"}
    value["messages"].insert(2, rules)
    value["history"] = {"length": len(value["messages"]), "hash": "rules"}
    coding.save(chat, value)
    runtime = {"role": "user", "content": "Runtime snapshot: review, current plan"}
    await compact(chat, Client(), runtime_context=runtime)
    saved = coding.load(chat)
    assert saved["messages"][0] == value["messages"][0]
    assert rules in saved["messages"] and runtime in saved["messages"]


def test_threshold_follows_the_model_that_answered_last(chat):
    config = {
        "provider": "dokky",
        "context_tokens": 200000,
        "models": [
            {"id": "dokky-code", "reasoning_modes": [{"id": "pro", "compact_at_tokens": 120000}]}
        ],
    }
    value = coding.load(chat)
    value["session"]["last_usage"] = {
        "usage": {},
        "dokky": {"context": {"compact_at_tokens": 56000, "limit_tokens": 73000}},
    }
    # сервер переключился на модель с меньшим окном после запуска приложения
    assert compact_threshold(config, "pro", value) == 56000
    assert compact_threshold(config, "pro") == 120000
