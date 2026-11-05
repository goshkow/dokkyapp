import asyncio
import json
import time

import httpx
import pytest

import koddi.storage.db as storage
from koddi.dokky import coding
from koddi.dokky.client import API, DokkyClient, DokkyError
from koddi.dokky.context import drop_transition, soft_dokky_error, trim_history
from koddi.remote.client import RemoteAIClient, RemoteAIError
from koddi.telemetry import errors


class Store:
    def load(self):
        return {"access_token": "test", "refresh_token": "refresh", "expires_at": time.time() + 900}

    def clear(self):
        pass


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    real = asyncio.sleep

    async def sleep(_delay, *args):
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "resilience.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "test-user"})
    with storage.db() as conn:
        return conn.execute("INSERT INTO chats(title,mode) VALUES ('Тест','development')").lastrowid


def session(**kwargs):
    return {"id": "cs_" + "a" * 24, "reasoning_mode": "pro", "version": 1, "history": {"length": 0, "hash": ""}, **kwargs}


def sse(*frames):
    return httpx.Response(
        200, text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
    )


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def content(text):
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


def usage(length=3):
    return {"choices": [], "usage": {"prompt_tokens": 5}, "dokky": {"history": {"length": length, "hash": "h"}}}


def make_client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("koddi.dokky.client.dokky", DokkyClient(transport=transport, store=Store()))
    return RemoteAIClient({"provider": "dokky", "base_url": API}, transport=transport)


async def run(client, chat, messages=None):
    messages = messages or [{"role": "system", "content": "Agent"}, {"role": "user", "content": "Test"}]
    return [e async for e in client.stream(model="x", messages=messages, tools=[], options={"_chat_id": chat})]


@pytest.mark.asyncio
async def test_empty_answer_with_length_is_reported_as_length(chat, monkeypatch):
    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        return sse(finish("length"), usage())

    events = await run(make_client(monkeypatch, handler), chat)
    assert any(e.get("finish_reason") == "length" for e in events)


@pytest.mark.asyncio
async def test_empty_answer_retries_once_then_gives_clear_error(chat, monkeypatch):
    posts = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        posts.append(json.loads(request.content))
        return sse(finish("stop"), usage())

    with pytest.raises(RemoteAIError) as caught:
        await run(make_client(monkeypatch, handler), chat)
    assert caught.value.code == "empty_response"
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_empty_answer_then_good_one_continues(chat, monkeypatch):
    posts = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        posts.append(1)
        return sse(finish("stop"), usage()) if len(posts) == 1 else sse(content("Готово"), finish("stop"), usage())

    events = await run(make_client(monkeypatch, handler), chat)
    assert "".join(e.get("content", "") for e in events) == "Готово"


@pytest.mark.asyncio
async def test_gateway_error_without_json_is_retried(chat, monkeypatch):
    posts = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        posts.append(request.headers["Idempotency-Key"])
        if len(posts) < 3:
            return httpx.Response(502, text="<html>Bad Gateway</html>")
        return sse(content("Готово"), finish("stop"), usage())

    events = await run(make_client(monkeypatch, handler), chat)
    assert "".join(e.get("content", "") for e in events) == "Готово"
    assert len(posts) == 3 and len(set(posts)) == 1


def test_retryable_statuses_and_hard_limits():
    client = DokkyClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)), store=Store())
    for status in (408, 502, 503, 504, 429):
        with pytest.raises(DokkyError) as caught:
            client.check(httpx.Response(status, headers={"Retry-After": "7"}, text="oops", request=httpx.Request("GET", API)))
        assert caught.value.retryable and caught.value.retry_after == 7
    with pytest.raises(DokkyError) as caught:
        client.check(httpx.Response(402, json={"error": {"code": "quota_exceeded"}}, request=httpx.Request("GET", API)))
    assert not caught.value.retryable
    with pytest.raises(DokkyError) as caught:
        client.check(httpx.Response(429, json={"error": {"code": "agent_loop_limit"}}, request=httpx.Request("GET", API)))
    assert not caught.value.retryable


@pytest.mark.asyncio
async def test_history_changed_is_recovered_and_request_repeated(chat, monkeypatch):
    saved = [{"role": "system", "content": "Agent"}, {"role": "user", "content": "Test"}, {"role": "assistant", "content": "Готово"}]
    posts = []

    def handler(request):
        if request.method == "GET":
            if request.url.path.endswith("/history"):
                return httpx.Response(200, json={"items": [{"message": m} for m in saved], "next_after": None})
            return httpx.Response(200, json=session(history={"length": 3, "hash": "h"}))
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        posts.append(json.loads(request.content))
        return sse(content("Готово"), finish("stop"), usage(3 if len(posts) == 1 else 5))

    client = make_client(monkeypatch, handler)
    await run(client, chat)
    changed = [
        {"role": "system", "content": "Agent"},
        {"role": "user", "content": "Изменённый старый текст"},
        {"role": "assistant", "content": "Готово"},
        {"role": "user", "content": "Дальше"},
    ]
    events = await run(client, chat, changed)
    assert "".join(e.get("content", "") for e in events) == "Готово"
    assert len(posts) == 2


def test_soft_errors_exclude_limits_and_sign_out():
    assert soft_dokky_error(DokkyError("x", 422, "compaction_incomplete"))
    assert soft_dokky_error(DokkyError("x", 502, "upstream_unavailable"))
    assert soft_dokky_error(DokkyError("x", 503, "x", details={"retryable": True}))
    assert not soft_dokky_error(DokkyError("x", 402, "quota_exceeded"))
    assert not soft_dokky_error(DokkyError("x", 429, "agent_loop_limit"))
    assert not soft_dokky_error(DokkyError("x", 401, "session_revoked"))


def test_trim_history_shrinks_old_outputs_and_keeps_tail():
    big = "x" * 60_000
    messages = [{"role": "system", "content": "Инструкции"}, {"role": "user", "content": "Задача"}]
    for index in range(8):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{index}", "type": "function", "function": {"name": "file.read", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": big})
    messages.append({"role": "user", "content": "Последнее"})
    trimmed = trim_history(messages, [], 40_000)
    assert trimmed is not None and trimmed[0]["content"] == "Инструкции"
    assert trimmed[-1]["content"] == "Последнее"
    assert sum(len(str(m.get("content"))) for m in trimmed) < sum(len(str(m.get("content"))) for m in messages)
    assert trim_history([{"role": "system", "content": "a"}, {"role": "user", "content": "b"}], [], 40_000) is None


def test_drop_transition_unblocks_session(chat, monkeypatch):
    value = coding.load(chat) or {
        "session": session(context_transition={"id": "op"}), "messages": [], "tools": [], "history": None,
    }
    value["session"]["context_transition"] = {"id": "op"}
    coding.save(chat, value)
    drop_transition(chat)
    after = coding.load(chat)
    assert "context_transition" not in after["session"]
    assert after["session"]["context_policy"]["failures"] == 1


@pytest.fixture
def reporter(monkeypatch):
    monkeypatch.setattr(errors, "_enabled", lambda: True)
    errors._last_sent.clear()
    errors._sent_at.clear()
    sent = []

    class Fake:
        async def request(self, method, path, json=None):
            sent.append((method, path, json))
            raise DokkyError("нет такого пути", 404, "not_found")

    monkeypatch.setattr("koddi.dokky.client.dokky", Fake())
    return sent


@pytest.mark.asyncio
async def test_report_error_sends_only_closed_fields_and_never_raises(reporter):
    errors.report_error(
        "compaction_incomplete", ValueError("секретный текст"), stage="compaction", http_status=422,
        reasoning_mode="pro", step=3, text="приватное сообщение", path="/Users/me/secret.py",
    )
    await asyncio.sleep(0)
    await asyncio.gather(*list(errors._tasks))
    assert len(reporter) == 1
    method, path, payload = reporter[0]
    assert (method, path) == ("POST", "/app/errors")
    assert set(payload) == {"code", "kind", "stage", "http_status", "reasoning_mode", "step", "app_version", "platform"}
    assert payload["kind"] == "ValueError" and "секретный" not in json.dumps(payload, ensure_ascii=False)
    errors.report_error("плохой код!", "X")
    errors.report_error(None)


@pytest.mark.asyncio
async def test_report_error_dedups_and_limits_per_hour(reporter):
    for _ in range(3):
        errors.report_error("same_code", "X")
    for index in range(60):
        errors.report_error(f"code_{index}", "X")
    await asyncio.gather(*list(errors._tasks))
    assert len([r for r in reporter if r[2]["code"] == "same_code"]) == 1
    assert len(reporter) == errors.PER_HOUR
    errors.report_error("quota_exceeded", "X")
    assert all(r[2]["code"] != "quota_exceeded" for r in reporter)


def test_report_error_without_event_loop_does_not_raise(monkeypatch):
    monkeypatch.setattr(errors, "_enabled", lambda: True)
    errors.report_error("no_loop", "X")


def orchestrator_client(answers):
    class Client:
        count = 0

        async def stream(self, **kwargs):
            if "Generate only" in kwargs["messages"][0]["content"]:
                yield {"content": "Тест"}
                return
            self.count += 1
            for chunk in answers(self.count, kwargs["messages"]):
                yield chunk

    return Client()


async def run_turn(client, chat, tmp_path):
    from koddi.agents import AgentOrchestrator

    return [e async for e in AgentOrchestrator(client).run(
        chat_id=chat, project_id=None, workspace=tmp_path, user_message="Сделай", model="test")]


@pytest.mark.asyncio
async def test_turn_survives_empty_answer_and_bad_call_ids(chat, tmp_path):
    seen = []

    def answers(count, messages):
        seen.append(messages[-1])
        if count == 1:
            return []
        if count == 2:
            return [{"tool_call": {"id": "", "name": "directory.list", "arguments": "{}"}}]
        return [{"content": "Готово"}]

    events = await run_turn(orchestrator_client(answers), chat, tmp_path)
    assert "turn.completed" in events[-1]
    assert not any("turn.failed" in e for e in events)
    assert "пустым" in seen[1]["content"] and "идентификатор" in seen[2]["content"]


@pytest.mark.asyncio
async def test_repeated_empty_answers_end_turn_softly(chat, tmp_path):
    client = orchestrator_client(lambda count, messages: [])
    events = await run_turn(client, chat, tmp_path)
    assert "turn.completed" in events[-1]
    assert client.count == 3
    assert any("message.delta" in e and "пустой ответ" in e for e in events)


def test_summary_window_follows_selected_mode_not_stale_config():
    from koddi.dokky.context import summary_window

    # общий context_tokens устарел (200 тыс.), а у режима Max окно 500 тыс.: сводка берёт окно режима
    config = {"provider": "dokky", "context_tokens": 200_000, "models": [{"id": "dokky-code", "reasoning_modes": [
        {"id": "light", "context_tokens": 150_000}, {"id": "max", "context_tokens": 500_000}]}]}
    assert summary_window(config, "max") == 500_000
    assert summary_window(config, "light") == 150_000
    live = {"session": {"last_usage": {"dokky": {"context": {"mode": "max", "limit_tokens": 450_000}}}}}
    assert summary_window(config, "max", live) == 450_000
    # живое окно другого режима не подменяет окно выбранного
    assert summary_window(config, "light", live) == 150_000
    assert summary_window({"provider": "dokky", "context_tokens": 300_000}, "max") == 300_000


def test_summary_request_always_fits_even_when_history_is_three_windows():
    from koddi.agents.compaction import context_size
    from koddi.dokky.context import SUMMARY_MAX_TOKENS, SUMMARY_REQUEST, shrink_for_summary

    messages = [{"role": "system", "content": "rules"}]
    for index in range(400):
        messages += [
            {"role": "user", "content": f"шаг {index} " + "текст " * 400},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{index}", "type": "function", "function": {"name": "file__read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{index}", "content": "вывод " * 900},
            {"role": "assistant", "content": "ответ " * 300},
        ]
    limit = 150_000
    assert context_size(messages, [], dokky=True) > limit * 3
    result = shrink_for_summary(messages, [], limit)
    assert result is not None
    request = [{"role": "user", "content": SUMMARY_REQUEST}]
    assert context_size(result + request, [], dokky=True) <= limit - SUMMARY_MAX_TOKENS
    assert result[0] == messages[0]
    # последняя просьба человека остаётся, а у каждого результата инструмента есть свой вызов
    assert result[-4]["content"] == messages[-4]["content"]
    calls = {call["id"] for item in result for call in item.get("tool_calls", [])}
    assert all(item["tool_call_id"] in calls for item in result if item.get("role") == "tool")
    assert any("не поместились в окно" in str(item.get("content")) for item in result)


def test_summary_request_without_need_keeps_history_intact():
    from koddi.dokky.context import shrink_for_summary

    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "привет"},
                {"role": "assistant", "content": "готово"}]
    assert shrink_for_summary(messages, [], 150_000) == messages


def test_tool_size_counts_only_what_model_receives():
    from koddi.agents.compaction import tool_chars

    wire = {"type": "function", "function": {"name": "file.read", "parameters": {"type": "object"}}}
    full = wire | {"x-koddi": {"risk": "low", "result_schema": {"type": "object", "description": "x" * 5000}}}
    assert tool_chars([full]) == tool_chars([wire]) < 200


def test_retry_schedule_matches_codex_like_pauses():
    from koddi.remote.client import RETRY_DELAYS, _backoff

    assert RETRY_DELAYS == (5, 10, 15, 30, 30, 60)
    assert all(abs(_backoff(index) - delay) <= delay * 0.05 + 0.01 for index, delay in enumerate(RETRY_DELAYS))
    # заголовок сервера важнее расписания, но не дольше 90 с
    assert _backoff(0, 40) == 40 and _backoff(0, 500) == 90


@pytest.mark.asyncio
async def test_each_retry_is_reported_for_the_status_line(chat, monkeypatch):
    posts = []

    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        posts.append(1)
        if len(posts) < 3:
            return httpx.Response(502, text="<html>Bad Gateway</html>")
        return sse(content("Готово"), finish("stop"), usage())

    events = await run(make_client(monkeypatch, handler), chat)
    retries = [e["retrying"] for e in events if "retrying" in e]
    assert [item["attempt"] for item in retries] == [1, 2]
    assert all(item["total"] == 6 and item["seconds"] >= 1 for item in retries)
    assert "".join(e.get("content", "") for e in events) == "Готово"


@pytest.mark.asyncio
async def test_server_down_after_all_retries_is_ai_unavailable_and_request_kept(chat, monkeypatch):
    def handler(request):
        if request.url.path.endswith("/coding/sessions"):
            return httpx.Response(201, json=session())
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(RemoteAIError) as caught:
        await run(make_client(monkeypatch, handler), chat)
    assert caught.value.code == "ai_unavailable"
    assert "временно не отвечает" in str(caught.value)
    # запрос не забыт: «Повторить попытку» продолжит его с тем же ключом
    assert coding.pending(chat)
