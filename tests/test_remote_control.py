"""Доступ с телефона: перевод хода в события сервера, надёжная отправка, команды с телефона и ошибки сервера.

Сервер подменён записью вызовов `koddi.remote_control.api.call` с той же логикой номеров и повторов, что у
mini_app/remote_api.py; сети нет.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.remote_control import Hooks, RemoteControl, api, outbox
from koddi.remote_control.api import RemoteFailure
from koddi.remote_control.mirror import Mirror
from koddi.remote_control.redact import clean_path, clean_text, diff_files, fit, secret_file

# фальшивый ключ собирается по частям, чтобы его не ловил сканер секретов репозитория
FAKE_KEY = "sk-" + "secretvalue1234567890abcd"

HOST = "rh_test0000000000000000"


class FakeServer:
    """Минимальный сервер удалённого управления: номера событий, идемпотентность сообщений, очередь команд."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, object]] = []
        self.events: dict[str, list[dict]] = {}
        self.watermark: dict[str, int] = {}
        self.messages: dict[str, dict[str, dict]] = {}
        self.sessions: dict[str, dict] = {}
        self.acks: list[tuple[str, str, str]] = []
        self.commands: list[dict] = []
        self.failures: list[tuple[object, RemoteFailure]] = []
        self.public = 100

    def fail(self, match, failure: RemoteFailure) -> None:
        self.failures.append((match, failure))

    async def call(self, method: str, *parts: str, json=None, params=None) -> dict:
        self.calls.append((method, parts, json))
        for index, (match, failure) in enumerate(self.failures):
            if match(method, parts, json):
                del self.failures[index]
                raise failure
        if parts == ("hosts",):
            return {"host": {"id": HOST, "name": json["name"]}, "poll": {"wait_seconds": 25}}
        if parts[-1] == "heartbeat":
            return {"pending_commands": len(self.commands)}
        if parts[-1] == "commands" and method == "GET":
            items, self.commands = self.commands, []
            if not items:
                await asyncio.sleep(0.01)
            return {"commands": items}
        if parts[2] == "commands":
            self.acks.append((parts[3], json["status"], json.get("error", "")))
            return {"ok": True}
        key = parts[3]
        if method == "PUT":
            self.sessions.setdefault(key, {}).update(json)
            if key not in self.watermark:
                self.public += 1
                self.sessions[key]["chat_id"] = self.public
            return {"chat_id": self.sessions[key]["chat_id"], "created": True, "last_client_seq": self.watermark.setdefault(key, 0)}
        if method == "DELETE":
            self.sessions.setdefault(key, {})["closed"] = True
            return {"ok": True}
        if parts[-1] == "messages":
            box = self.messages.setdefault(key, {})
            duplicate = json["client_id"] in box
            box.setdefault(json["client_id"], json)
            return {"message_id": len(box), "duplicate": duplicate}
        if parts[-1] == "events":
            for item in json["events"]:
                if item["client_seq"] > self.watermark.get(key, 0):
                    self.watermark[key] = item["client_seq"]
                    self.events.setdefault(key, []).append(item)
            return {"accepted": len(json["events"]), "last_client_seq": self.watermark.get(key, 0)}
        raise AssertionError((method, parts))

    def kinds(self, key: str) -> list[str]:
        return [item["kind"] for item in self.events.get(key, [])]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "remote.sqlite3")
    storage.init_db()
    server = FakeServer()
    monkeypatch.setattr(api, "call", server.call)
    workspace = tmp_path / "home" / "me" / "shop"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home" / "me"))
    with storage.db() as conn:
        project = conn.execute("INSERT INTO projects(name, path) VALUES ('shop', ?)", (str(workspace),)).lastrowid
        chat = conn.execute(
            "INSERT INTO chats(project_id, title, mode, owner_id) VALUES (?, 'Починить корзину', 'development', '__test__')",
            (project,),
        ).lastrowid
    control = RemoteControl()
    control.host = {"id": HOST, "name": "MacBook"}
    control._enabled = True
    return type("Env", (), {"server": server, "control": control, "chat": chat, "project": project, "workspace": workspace, "tmp": tmp_path})


class FakeRun:
    """Поток `ActiveRun.subscribe` с заранее записанными SSE-событиями."""

    def __init__(self, chat_id: int, items: list[tuple[str, dict]], *, stall: bool = False):
        self.chat_id = chat_id
        self.items = [f"id: {index}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for index, (kind, data) in enumerate(items, 1)]
        self.stall = stall

    async def subscribe(self, after: int = 0):
        for index, item in enumerate(self.items, 1):
            if index > after:
                yield item
                await asyncio.sleep(0)


def insert(sql: str, *params) -> int:
    with storage.db() as conn:
        return int(conn.execute(sql, params).lastrowid)


def turn_rows(chat: int, question: str = "Почини корзину") -> tuple[int, int]:
    turn = insert("INSERT INTO turns(chat_id, status) VALUES (?, 'running')", chat)
    user = insert(
        "INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'user', ?, ?)",
        chat, question, json.dumps({"turn_id": turn}),
    )
    return turn, user


async def deliver(control: RemoteControl) -> None:
    await control.drain()


def share(env) -> str:
    assert env.control.share(env.chat)
    return outbox.session_key(env.chat)


@pytest.mark.asyncio
async def test_turn_from_computer_reaches_server_like_the_window(env):
    key = share(env)
    turn, user = turn_rows(env.chat)
    ws = str(env.workspace)
    change = insert(
        "INSERT INTO change_sets(project_id, turn_id, description, diff) VALUES (?, ?, 'edit', ?)",
        env.project, turn, "--- a/cart.py\n+++ b/cart.py\n@@ -1 +1 @@\n-total = 0\n+total = sum(items)\n",
    )
    question = insert(
        "INSERT INTO agent_requests(turn_id, kind, payload_json) VALUES (?, 'question', ?)",
        turn, json.dumps({"question": "Какую валюту показывать?"}),
    )
    final = insert(
        "INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'assistant', ?, ?)",
        env.chat, f"Готово, правка в {ws}/cart.py", json.dumps({"turn_id": turn, "channel": "final", "status": "completed"}),
    )
    items = [
        ("turn.started", {"turn_id": turn, "model": "dokky-code"}),
        ("turn.started", {"turn_id": turn, "chat_title": "Корзина: итог"}),
        ("agent.status", {"text": "Думаю…"}),
        ("agent.status", {"text": "Думаю…"}),
        ("plan.updated", {"ok": True, "steps": [
            {"title": "Найти ошибку", "status": "completed"},
            {"title": "Исправить", "status": "running"},
            {"title": "Выкатить", "status": "blocked"},
        ]}),
        ("message.delta", {"content": "Сначала ", "message_id": 50, "turn_id": turn}),
        ("message.delta", {"content": "посмотрю ", "message_id": 50, "turn_id": turn}),
        ("message.delta", {"content": "корзину.", "message_id": 50, "turn_id": turn}),
        ("message.updated", {"message_id": 50, "turn_id": turn, "channel": "commentary"}),
        ("tool.call", {"name": "file.read", "arguments": {"path": "cart.py"}, "id": "call_1", "created_at": "2026-10-22T10:00:00.000+00:00"}),
        ("tool.result", {"name": "file.read", "arguments": {"path": "cart.py"}, "id": "call_1", "result": {"ok": True, "content": "x"}, "created_at": "2026-10-22T10:00:00.250+00:00"}),
        ("tool.call", {"name": "file.edit", "arguments": {"path": "cart.py"}, "id": "call_2"}),
        ("tool.result", {"name": "file.edit", "id": "call_2", "result": {"ok": True, "change_set_id": change, "lines_added": 1, "lines_removed": 1, "files": [{"path": "cart.py", "lines_added": 1, "lines_removed": 1}]}}),
        ("tool.call", {"name": "shell.run", "arguments": {"command": f"cd {ws} && pytest -q"}, "id": "call_3"}),
        ("tool.result", {"name": "shell.run", "id": "call_3", "result": {"ok": False, "returncode": 1, "stdout": f"FAILED {ws}/test_cart.py\nAPI_KEY={FAKE_KEY}", "stderr": ""}}),
        ("tool.call", {"name": "task.ask_user", "arguments": {"question": "Какую валюту показывать?"}, "id": "call_4"}),
        ("agent.input_required", {"question": "Какую валюту показывать?", "call_id": "call_4", "id": question, "kind": "question"}),
        ("message.delta", {"content": "Готово", "message_id": final, "turn_id": turn}),
        ("turn.completed", {"turn_id": turn}),
    ]
    await Mirror(env.control, FakeRun(env.chat, items), key, env.workspace).follow()
    await deliver(env.control)
    server = env.server
    events = server.events[key]
    kinds = server.kinds(key)
    # сообщение с компьютера загружено до начала хода, ход ссылается на него
    assert f"m{user}" in server.messages[key]
    start = events[kinds.index("turn_start")]
    assert start["data"]["user_client_id"] == f"m{user}" and "command_id" not in start["data"]
    assert server.sessions[key]["title"] == "Корзина: итог"
    # повтор того же статуса не шлётся
    assert [item["text"] for item in events if item["kind"] == "status"] == ["Думаю…"]
    plan = next(item for item in events if item["kind"] == "plan")
    assert plan["data"]["steps"] == [
        {"text": "Найти ошибку", "status": "completed"},
        {"text": "Исправить", "status": "in_progress"},
        {"text": "Выкатить - заблокировано", "status": "pending"},
    ]
    # дельты склеены, затем живой текст становится комментарием
    deltas = [item["text"] for item in events if item["kind"] == "delta"]
    assert deltas[0] == "Сначала посмотрю корзину."
    commentary = kinds.index("commentary")
    assert kinds[commentary - 1] == "reset" and events[commentary]["text"] == "Сначала посмотрю корзину."
    actions = [item for item in events if item["kind"] == "action"]
    by_id: dict[str, list[dict]] = {}
    for item in actions:
        by_id.setdefault(item["data"]["id"], []).append(item)
    # одна карточка на вызов, вопрос и план своими карточками
    assert sorted(by_id) == [f"{turn}-call_1", f"{turn}-call_2", f"{turn}-call_3"]
    read = by_id[f"{turn}-call_1"]
    assert [item["data"]["state"] for item in read] == ["running", "done"]
    assert read[0]["text"] == "Читаю файл" and read[1]["data"]["target"] == "cart.py" and read[1]["data"]["duration_ms"] == 250
    edit = by_id[f"{turn}-call_2"][-1]["data"]
    assert edit["added"] == 1 and edit["removed"] == 1 and edit["preview_kind"] == "diff" and "+total = sum(items)" in edit["preview"]
    shell = by_id[f"{turn}-call_3"][-1]
    assert shell["text"] == "Выполняю команду" and shell["data"]["state"] == "error" and shell["data"]["error"] == "Код выхода 1"
    flat = json.dumps(server.events, ensure_ascii=False) + json.dumps(server.messages, ensure_ascii=False)
    # полный путь проекта и секреты наружу не уходят
    assert ws not in flat and str(env.tmp) not in flat and "sk-secretvalue" not in flat
    assert shell["data"]["target"] == "cd . && pytest -q" and "FAILED ./test_cart.py" in shell["data"]["preview"]
    asked = next(item for item in events if item["kind"] == "question_request")
    assert asked["data"] == {"request_id": f"ar-{question}", "options": [], "allow_text": True} and asked["text"] == "Какую валюту показывать?"
    # вопрос без ответа к концу хода закрывается, ответ загружен сообщением до конца хода
    assert any(item["kind"] == "question_resolved" and item["data"]["request_id"] == f"ar-{question}" for item in events)
    assert server.messages[key][f"m{final}"]["content"] == "Готово, правка в ./cart.py"
    assert kinds[-1] == "turn_end" and events[-1]["data"] == {"message_client_id": f"m{final}"}
    seqs = [item["client_seq"] for item in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


@pytest.mark.asyncio
async def test_command_turn_and_denied_action(env):
    key = share(env)
    turn, _ = turn_rows(env.chat)
    approval = insert(
        "INSERT INTO agent_requests(turn_id, kind, payload_json, response_json) VALUES (?, 'approval', ?, ?)",
        turn, json.dumps({"id": "call_9", "name": "shell.run"}), json.dumps({"approved": False}),
    )
    env.control.loop = asyncio.get_running_loop()
    env.control.commands[env.chat] = {"id": "rc_1", "request_uuid": "u-1"}
    items = [
        ("turn.started", {"turn_id": turn}),
        ("tool.call", {"name": "shell.run", "arguments": {"command": "rm -rf build"}, "id": "call_9"}),
        ("agent.input_required", {"id": approval, "kind": "approval", "call_id": "call_9", "name": "shell.run",
                                  "action": {"title": "Выполнить команду", "detail": f"rm -rf {env.workspace}/build"}}),
        ("tool.result", {"name": "shell.run", "id": "call_9", "result": {"ok": False, "error": "Пользователь запретил действие."}}),
        ("turn.cancelled", {"chat_id": env.chat}),
    ]
    # команда достаётся ходу в момент запуска: следующая команда того же чата её не перепишет
    env.control.on_run(FakeRun(env.chat, items))
    task = env.control.mirrors[env.chat]
    assert env.chat not in env.control.commands
    env.control.commands[env.chat] = {"id": "rc_2"}
    await task
    assert env.control.commands[env.chat] == {"id": "rc_2"}, "конец прошлого хода не снимает чужую команду"
    await deliver(env.control)
    events = env.server.events[key]
    assert events[0]["kind"] == "turn_start" and events[0]["data"] == {"command_id": "rc_1", "request_uuid": "u-1"}
    # сообщение пользователя с телефона сервер уже хранит: ход его не загружает
    assert not any(cid.startswith("m") and env.server.messages[key][cid]["role"] == "user" for cid in env.server.messages.get(key, {}))
    request = next(item for item in events if item["kind"] == "approval_request")
    assert request["data"]["request_id"] == f"ar-{approval}" and request["data"]["detail"] == "rm -rf ./build"
    assert request["data"]["can_remember"] is False
    card = [item for item in events if item["kind"] == "action"][-1]["data"]
    assert card["state"] == "denied"
    assert events[-1]["kind"] == "turn_end" and events[-1]["data"]["stopped"] is True


@pytest.mark.asyncio
async def test_outbox_survives_network_break_and_restart(env):
    key = share(env)
    await deliver(env.control)
    for index in range(5):
        outbox.event(key, "status", f"шаг {index}")
    env.server.fail(lambda method, parts, body: parts[-1] == "events", RemoteFailure(503, "upstream_unavailable", "нет сети"))
    with pytest.raises(RemoteFailure):
        await deliver(env.control)
    assert outbox.count() == 5
    await deliver(env.control)
    assert [item["text"] for item in env.server.events[key]] == [f"шаг {index}" for index in range(5)]
    # пачка ушла, но ответ потерялся: повтор не даёт дублей
    outbox.event(key, "status", "после обрыва")
    rows = outbox.pending()
    await env.server.call("POST", "hosts", HOST, "sessions", key, "events", json={"events": [rows[0]["body"]]})
    await deliver(env.control)
    assert [item["text"] for item in env.server.events[key]].count("после обрыва") == 1
    # перезапуск приложения: номер продолжается, а не начинается с 1
    restarted = RemoteControl()
    restarted.host = {"id": HOST}
    seq = outbox.event(key, "status", "после перезапуска")
    await restarted.drain()
    assert seq == 7 and env.server.events[key][-1]["client_seq"] == 7


@pytest.mark.asyncio
async def test_history_once_and_after_disabled_turns(env):
    turn, user = turn_rows(env.chat, "Первый вопрос")
    insert("INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'assistant', 'Комментарий', ?)", env.chat, json.dumps({"turn_id": turn, "channel": "commentary"}))
    answer = insert("INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'assistant', 'Ответ', ?)", env.chat, json.dumps({"turn_id": turn, "channel": "final"}))
    insert("INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'user', 'С телефона', ?)", env.chat, json.dumps({"origin": "remote"}))
    key = share(env)
    await deliver(env.control)
    assert list(env.server.messages[key]) == [f"m{user}", f"m{answer}"]
    puts = [call for call in env.server.calls if call[0] == "PUT"]
    assert puts[0][2]["folder_label"] == "shop" and puts[0][2]["title"] == "Починить корзину"
    calls = len(env.server.calls)
    env.control.share(env.chat)
    await deliver(env.control)
    # повторная сверка шлёт только сессию, история уже загружена
    assert [call[1][-1] for call in env.server.calls[calls:]] == [key]
    later = insert("INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'user', 'Пока доступ был выключен', '{}')", env.chat)
    env.control.share(env.chat)
    await deliver(env.control)
    assert f"m{later}" in env.server.messages[key]


@pytest.mark.asyncio
async def test_server_errors_follow_the_protocol(env):
    key = share(env)
    await deliver(env.control)
    env.server.fail(lambda m, p, b: p[-1] == "events", RemoteFailure(410, "session_deleted", "Чат удалён"))
    outbox.event(key, "status", "x")
    await deliver(env.control)
    assert outbox.session(env.chat)["shared"] == 0 and outbox.count() == 0
    assert outbox.event(key, "status", "после удаления") is None
    # «Показать на телефоне» шлёт reshare
    env.control.set_shared(env.chat, True)
    await deliver(env.control)
    assert env.server.calls[-1][0] == "PUT" and env.server.calls[-1][2]["reshare"] is True
    # предел событий хода: мелкое выкидывается, итог остаётся
    for _ in range(3):
        outbox.event(key, "delta", "a")
    outbox.event(key, "turn_end")
    env.server.fail(lambda m, p, b: p[-1] == "events", RemoteFailure(429, "events_limit", "много"))
    await deliver(env.control)
    assert env.server.kinds(key)[-1] == "turn_end" and "delta" not in env.server.kinds(key)
    # отзыв компьютера и выход из аккаунта останавливают отправку
    from koddi.remote_control.bridge import LoginRequired, Revoked

    outbox.event(key, "status", "y")
    env.server.fail(lambda m, p, b: p[-1] == "events", RemoteFailure(410, "host_revoked", "Отозван"))
    with pytest.raises(Revoked):
        await deliver(env.control)
    env.server.fail(lambda m, p, b: p[-1] == "events", RemoteFailure(401, "invalid_token", "Войдите"))
    with pytest.raises(LoginRequired):
        await deliver(env.control)
    assert outbox.count() == 1
    # неверное событие снимается одно, остальные доходят
    outbox.event(key, "status", "z")
    env.server.fail(lambda m, p, b: p[-1] == "events" and len(b["events"]) == 1 and b["events"][0]["text"] == "y", RemoteFailure(422, "validation_error", "плохо"))
    env.server.fail(lambda m, p, b: p[-1] == "events" and len(b["events"]) == 2, RemoteFailure(422, "validation_error", "плохо"))
    await deliver(env.control)
    assert env.server.events[key][-1]["text"] == "z" and outbox.count() == 0


def hooks(env, calls: list):
    async def start_turn(chat_id: int, text: str) -> None:
        calls.append(("start", chat_id, text))
        if text == "занят":
            raise HTTPException(409, "Задача уже выполняется")

    async def cancel_turn(chat_id: int) -> bool:
        calls.append(("cancel", chat_id))
        return True

    def respond(request_id: int, payload: dict) -> dict:
        calls.append(("respond", request_id, payload))
        with storage.db() as conn:
            row = conn.execute("SELECT kind, response_json FROM agent_requests WHERE id=?", (request_id,)).fetchone()
            if row["response_json"] is not None:
                raise HTTPException(409, "Запрос уже закрыт")
            conn.execute("UPDATE agent_requests SET response_json=? WHERE id=?", (json.dumps(payload), request_id))
        env.control.resolved(request_id, row["kind"], payload)
        return {"ok": True}

    async def create_chat(project_id, title: str) -> dict:
        calls.append(("create", project_id, title))
        chat = insert("INSERT INTO chats(project_id, title, mode, owner_id) VALUES (?, ?, 'development', '__test__')", project_id, title)
        return {"id": chat}

    return Hooks(start_turn, cancel_turn, respond, create_chat)


def command(kind: str, key: str, cid: str, **payload) -> dict:
    return {"id": cid, "kind": kind, "session_key": key, "payload": payload}


@pytest.mark.asyncio
async def test_commands_from_phone(env, monkeypatch):
    from koddi.events import event_bus

    published = []

    async def publish(event, data=None):
        published.append(event)

    monkeypatch.setattr(event_bus, "publish", publish)
    key = share(env)
    await deliver(env.control)
    calls: list = []
    env.control.bind(hooks(env, calls))
    env.control.loop = asyncio.get_running_loop()
    await env.control.execute(command("message", key, "rc_m1", text="Сделай отчёт", request_uuid="u1"))
    # повтор той же команды не исполняет её снова, но подтверждение повторяется
    await env.control.execute(command("message", key, "rc_m1", text="Сделай отчёт", request_uuid="u1"))
    assert calls == [("start", env.chat, "Сделай отчёт")] and env.control.commands[env.chat]["id"] == "rc_m1"
    assert env.server.acks == [("rc_m1", "accepted", ""), ("rc_m1", "accepted", "")] and "remote.turn" in published
    await env.control.execute(command("message", key, "rc_m2", text="занят"))
    # отказ второй команды не стирает ожидание первой: её ход ещё не начался
    assert env.server.acks[-1] == ("rc_m2", "rejected", "Агент уже работает в этом чате")
    assert env.control.commands[env.chat]["id"] == "rc_m1"
    await env.control.execute(command("message", "chat-999", "rc_m3", text="чужой"))
    assert env.server.acks[-1] == ("rc_m3", "rejected", "Этот чат больше не открыт для телефона")
    await env.control.execute(command("stop", key, "rc_s1"))
    assert calls[-1] == ("cancel", env.chat) and env.server.acks[-1] == ("rc_s1", "done", "")
    turn, _ = turn_rows(env.chat)
    approval = insert("INSERT INTO agent_requests(turn_id, kind, payload_json) VALUES (?, 'approval', '{}')", turn)
    await env.control.execute(command("approval", key, "rc_a1", request_id=f"ar-{approval}", decision="allow", remember=False))
    assert calls[-1] == ("respond", approval, {"approved": True}) and env.server.acks[-1] == ("rc_a1", "done", "")
    # на компьютере уже ответили: команда закрывается без ошибки
    await env.control.execute(command("approval", key, "rc_a2", request_id=f"ar-{approval}", decision="deny"))
    assert env.server.acks[-1] == ("rc_a2", "done", "")
    question = insert("INSERT INTO agent_requests(turn_id, kind, payload_json) VALUES (?, 'question', '{}')", turn)
    await env.control.execute(command("answer", key, "rc_q1", request_id=f"ar-{question}", options=["Рубли"], option_indexes=[0], text="и центы"))
    assert calls[-1] == ("respond", question, {"answer": "Рубли; и центы"})
    # решение не отвечает на вопрос, а ответ - на разрешение
    await env.control.execute(command("approval", key, "rc_a4", request_id=f"ar-{question}", decision="allow"))
    assert env.server.acks[-1] == ("rc_a4", "rejected", "Запрос не найден")
    # итог команды теряется в сети: подтверждение повторяется, после accepted сервер её снова не выдаст
    env.control.ack_pauses = (0, 0, 0)
    env.server.fail(lambda m, p, b: p[-1] == "rc_s2" and b["status"] == "done", RemoteFailure(502, "upstream_unavailable", "Нет связи"))
    await env.control.execute(command("stop", key, "rc_s2"))
    assert [ack for ack in env.server.acks if ack[0] == "rc_s2"] == [("rc_s2", "accepted", ""), ("rc_s2", "done", "")]
    # запрос из другого чата команда этого чата не трогает
    other = insert("INSERT INTO chats(project_id, title, mode, owner_id) VALUES (?, 'Другой', 'development', '__test__')", env.project)
    foreign_turn = insert("INSERT INTO turns(chat_id, status) VALUES (?, 'running')", other)
    foreign = insert("INSERT INTO agent_requests(turn_id, kind, payload_json) VALUES (?, 'approval', '{}')", foreign_turn)
    await env.control.execute(command("approval", key, "rc_a3", request_id=f"ar-{foreign}", decision="allow"))
    assert env.server.acks[-1] == ("rc_a3", "rejected", "Запрос не найден")
    await env.control.execute({"id": "rc_n1", "kind": "new_session", "session_key": "", "payload": {"title": "С телефона"}})
    assert calls[-1] == ("create", env.project, "С телефона") and env.server.acks[-1] == ("rc_n1", "done", "")
    await deliver(env.control)
    kinds = env.server.kinds(key)
    assert kinds.count("approval_resolved") == 1 and "question_resolved" in kinds
    resolved = next(item for item in env.server.events[key] if item["kind"] == "approval_resolved")
    assert resolved["data"] == {"request_id": f"ar-{approval}", "decision": "allow"}
    assert any(call[0] == "PUT" and call[2].get("title") == "С телефона" for call in env.server.calls)
    # просроченную команду сервер не принимает: исполнять не нужно
    env.server.fail(lambda m, p, b: p[-1] == "rc_old", RemoteFailure(409, "command_expired", "Просрочена"))
    before = len(calls)
    await env.control.execute(command("stop", key, "rc_old"))
    assert len(calls) == before


def test_window_hooks_never_break_chat_actions(env, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("база закрыта")

    monkeypatch.setattr(env.control, "share", broken)
    monkeypatch.setattr(env.control, "retitle", broken)
    monkeypatch.setattr(outbox, "session", broken)
    assert env.control.chat_created(env.chat) is None
    assert env.control.chat_renamed(env.chat, "Новое") is None
    assert env.control.chat_deleted(env.chat) is None


@pytest.mark.asyncio
async def test_secret_file_edits_go_without_content(env):
    key = share(env)
    turn, _ = turn_rows(env.chat)
    change = insert("INSERT INTO change_sets(project_id, turn_id, description, diff) VALUES (?, ?, 'Правка', ?)", env.project, turn,
                    "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-TOKEN=old\n+TOKEN=new\n")
    items = [
        ("turn.started", {"turn_id": turn}),
        ("tool.call", {"name": "file.edit", "arguments": {"path": ".env"}, "id": "call_e"}),
        ("tool.result", {"name": "file.edit", "id": "call_e", "result": {"ok": True, "change_set_id": change, "lines_added": 1,
                                                                       "lines_removed": 1, "files": [{"path": ".env"}]}}),
        ("turn.completed", {"chat_id": env.chat}),
    ]
    await Mirror(env.control, FakeRun(env.chat, items), key, env.workspace).follow()
    await deliver(env.control)
    done = [item["data"] for item in env.server.events[key] if item["kind"] == "action" and item["data"]["state"] == "done"]
    assert done and done[0]["added"] == 1 and "preview" not in done[0], done
    assert "TOKEN=new" not in json.dumps(env.server.events[key], ensure_ascii=False)


def test_redaction_keeps_paths_relative_and_secrets_out(tmp_path, monkeypatch):
    home = tmp_path / "home" / "me"
    workspace = home / "work" / "shop"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert clean_path(str(workspace / "src" / "app.py"), workspace) == "src/app.py"
    assert clean_path(str(home / ".config" / "app.ini"), workspace) == "~/.config/app.ini"
    assert clean_path("/etc/nginx/sites/dokky.conf", workspace) == "…/sites/dokky.conf"
    assert clean_path(r"C:\Users\me\work\file.txt", None) == "…/work/file.txt"
    text = clean_text(f"Authorization: Bearer abc.def.ghi password=hunter2 in {workspace}2/x and {workspace}/y", workspace)
    assert "abc.def" not in text and "hunter2" not in text
    # соседняя папка с тем же началом имени - не папка проекта
    # на Windows разделитель в свободном тексте остаётся обратным
    assert "~/work/shop2/x" in text.replace("\\", "/") and " ./y" in text and str(home) not in text
    leaked = clean_text(
        "DATABASE_URL=postgres://app:s3cr3t@db/main\nexport STRIPE_SECRET_KEY=rk_live_abcdefghij1234\nDB_PASS=hunter3\n"
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ." + "c2lnbmF0dXJlMTIz\nKEYBOARD_LAYOUT=us"
    )
    assert not any(secret in leaked for secret in ("s3cr3t", "rk_live", "hunter3", "eyJhbGci")), leaked
    assert "KEYBOARD_LAYOUT=us" in leaked, "обычные переменные не прячутся"
    assert secret_file(".env.local") and secret_file("deploy/server.pem") and not secret_file("src/env.py")
    assert diff_files("--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n") == [".env", ".env"]
    big = fit({"id": "a", "state": "done", "preview": "строка\n" * 5000, "preview_kind": "diff"})
    assert len(json.dumps(big, ensure_ascii=False).encode()) <= 15 * 1024


def test_settings_endpoints_and_sharing(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "remote-api.sqlite3")
    from koddi.api import create_app
    from koddi.remote_control import remote_control

    server = FakeServer()
    monkeypatch.setattr(api, "call", server.call)
    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "Локальный"}).json()["id"]
        state = client.get("/api/remote-control").json()
        assert state["enabled"] is False and state["state"] == "off" and state["prevent_sleep"] == "working"
        assert client.put("/api/remote-control", json={"prevent_sleep": "always"}).status_code == 422
        assert client.put("/api/remote-control", json={"unknown": 1}).status_code == 422
        assert client.put("/api/remote-control", json={"prevent_sleep": "off"}).json()["prevent_sleep"] == "off"
        state = client.put("/api/remote-control", json={"enabled": True}).json()
        assert state["enabled"] is True
        for _ in range(100):
            if any(call[1] == ("hosts",) for call in server.calls) and outbox.session(chat):
                break
            client.get("/api/remote-control")
            asyncio.run(asyncio.sleep(0.02))
        register = next(call for call in server.calls if call[1] == ("hosts",))
        assert register[2]["state"]["prevent_sleep_enabled"] is False and len(register[2]["install_id"]) >= 16
        assert client.get("/api/remote-control").json()["shared"][str(chat)] is True
        assert client.put(f"/api/chats/{chat}/remote", json={"shared": "no"}).status_code == 422
        assert client.put(f"/api/chats/{chat}/remote", json={"shared": False}).json() == {"chat_id": chat, "shared": False}
        assert client.put("/api/chats/999999/remote", json={"shared": True}).status_code == 404
        assert client.put("/api/remote-control", json={"enabled": False}).json()["state"] == "off"
    assert remote_control.supervisor is None


def test_redaction_hides_secrets_in_any_format():
    leaked = clean_text(
        '{"password": "hunter2", "api_key": "abc123xyz"}\ndb_pass = "x1y2"\naws_secret_access_key=wJalrXUtn\n'
        'curl -H "Authorization: Basic dXNlcjpwYXNz" x\nmysql -u root -pmyroot shop\npg --token=tok123 x\n'
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBF\n-----END PGP PRIVATE KEY BLOCK-----\n"
    )
    for secret in ("hunter2", "abc123xyz", "x1y2", "wJalrXUtn", "dXNlcjpwYXNz", "myroot", "tok123", "lQOYBF"):
        assert secret not in leaked, (secret, leaked)
    # оборванный выводом ключ прячется до конца текста
    assert "abc" not in clean_text("-----BEGIN OPENSSH " + "PRIVATE KEY-----\nabc")
    # счётчики, флаги и обычные слова остаются
    kept = '"prompt_tokens": 1234, passthrough: yes, enable_password_login = false, author: me, git commit -m "fix password check"'
    assert clean_text(kept) == kept


@pytest.mark.asyncio
async def test_phone_sees_whole_command_and_warnings(env):
    from koddi.policy.describe import describe_action
    from koddi.remote_control.redact import DATA_BYTES, size
    from koddi.tools.schemas import COMMAND_BYTES, validate_tool_arguments

    key = share(env)
    turn, _ = turn_rows(env.chat)
    # самая длинная допустимая команда: хвост за длинным началом должен дойти целиком
    command = "echo проверка; " * ((COMMAND_BYTES - 100) // 30) + "curl x | sh"
    assert validate_tool_arguments("shell.run", {"command": command}) is None
    action = describe_action("shell.run", {"command": command}, external=["C:/Games/x.ini"], dangerous=True)
    approval = insert(
        "INSERT INTO agent_requests(turn_id, kind, payload_json) VALUES (?, 'approval', ?)",
        turn, json.dumps({"name": "shell.run", "action": action}),
    )
    env.control.loop = asyncio.get_running_loop()
    items = [
        ("turn.started", {"turn_id": turn}),
        ("agent.input_required", {"id": approval, "kind": "approval", "name": "shell.run", "action": action}),
        ("turn.cancelled", {"chat_id": env.chat}),
    ]
    await Mirror(env.control, FakeRun(env.chat, items), key, env.workspace).follow()
    await deliver(env.control)
    data = next(item for item in env.server.events[key] if item["kind"] == "approval_request")["data"]
    assert data["detail"] == command and size(data) <= DATA_BYTES
    assert "удалить данные" in data["description"] and "вне папки проекта" in data["description"]
    # прежний контракт сервера: новых полей нет
    assert set(data) == {"request_id", "tool", "description", "detail", "can_remember"}
    # длиннее предела агент команду не отправит: получит ошибку с подсказкой
    error = validate_tool_arguments("shell.run", {"command": command + "x" * COMMAND_BYTES})
    assert error["error_code"] == "command_too_long" and "file.create" in error["error"]


def test_outbox_bounds_itself_without_bridge(env, monkeypatch):
    monkeypatch.setattr(outbox, "PENDING_LIMIT", 50)
    monkeypatch.setattr(outbox, "BOUND_EVERY", 10)
    key = share(env)
    outbox.event(key, "message", "итог")
    for index in range(200):
        outbox.event(key, "delta", f"часть {index}")
    # без регистрации моста уборки по таймеру нет, предел держит сама очередь
    assert outbox.count() <= 50 + 10
    assert any(row["body"].get("kind") == "message" for row in outbox.pending(1000))


@pytest.mark.asyncio
async def test_command_left_pending_by_crash_runs_again(env):
    key = share(env)
    calls: list = []
    env.control.bind(hooks(env, calls))
    env.control.loop = asyncio.get_running_loop()
    # процесс упал после отметки, но до итога: сервер выдал команду снова
    outbox.mark_seen("rc_crash", "pending")
    await env.control.execute(command("stop", key, "rc_crash"))
    assert calls == [("cancel", env.chat)] and outbox.seen("rc_crash")["status"] == "done"


@pytest.mark.asyncio
async def test_new_sessions_from_phone_are_limited(env, monkeypatch):
    import koddi.remote_control.bridge as bridge

    monkeypatch.setattr(bridge, "NEW_SESSIONS_PER_HOUR", 2)
    share(env)
    calls: list = []
    env.control.bind(hooks(env, calls))
    env.control.loop = asyncio.get_running_loop()
    for index in range(3):
        await env.control.execute({"id": f"rc_ns{index}", "kind": "new_session", "session_key": "", "payload": {"title": f"Чат {index}"}})
    assert [call[0] for call in calls].count("create") == 2
    assert env.server.acks[-1][:2] == ("rc_ns2", "rejected")
