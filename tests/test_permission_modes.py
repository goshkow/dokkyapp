"""Режимы подтверждений: вручную, автоматически (проверка Докки), разрешить всё; файлы вне проекта и их откат."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.orchestrator import AgentOrchestrator
from koddi.api import create_app
from koddi.changes.service import rollback_change_set
from koddi.config import data_dir
from koddi.policy.describe import describe_action
from koddi.policy.engine import decide
from koddi.policy.grants import permission_profile
from koddi.policy.review import REVIEW_PROMPT, parse_verdict, review_action, review_request
from koddi.tools import ToolRunner


@pytest.fixture
def places(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "modes.sqlite3")
    storage.init_db()
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "game"
    outside.mkdir()
    settings = outside / "settings.ini"
    settings.write_text("[video]\nquality=low\n", encoding="utf-8")
    return workspace, settings


def profile(value):
    storage.set_setting("permission_profile", value)


@pytest.mark.parametrize(("mode", "expected"), [("ask", "ask"), ("trusted", "review"), ("full", "allow")])
def test_each_mode_decides_project_writes_and_commands(places, mode, expected):
    workspace, _ = places
    profile(mode)
    assert decide("file.create", {"path": "a.py", "content": ""}, workspace).action == expected
    assert decide("shell.run", {"command": "python -V"}, workspace).action == expected
    dangerous = decide("shell.run", {"command": "Remove-Item build -Recurse"}, workspace)
    assert dangerous.action == expected and dangerous.dangerous
    assert decide("file.read", {"path": "a.py"}, workspace).action == "allow"


@pytest.mark.parametrize(("mode", "expected"), [("ask", "ask"), ("trusted", "review"), ("full", "allow")])
def test_files_outside_project_follow_the_mode(places, mode, expected):
    workspace, settings = places
    profile(mode)
    read = decide("file.read", {"path": str(settings)}, workspace)
    edit = decide("file.edit", {"path": str(settings), "old_text": "low", "new_text": "high"}, workspace)
    assert read.action == expected and read.external == (str(settings.resolve()),)
    assert edit.action == expected and edit.external


def test_outside_access_stays_closed_where_it_must(places):
    workspace, settings = places
    profile("full")
    # относительный выход за проект и инструменты без поддержки внешних путей закрыты
    assert decide("file.read", {"path": "../game/settings.ini"}, workspace).action == "block"
    assert decide("file.delete", {"path": str(settings)}, workspace).action == "block"
    # папка данных Докки (сессия, разрешения) закрыта в любом режиме
    secret = data_dir() / "dokky-session.keychain"
    assert decide("file.read", {"path": str(secret)}, workspace).action == "block"
    assert decide("file.write_patch", {"path": str(secret), "content": "x"}, workspace).action == "block"
    profile("read-only")
    assert decide("file.read", {"path": str(settings)}, workspace).action == "ask"
    assert decide("file.edit", {"path": str(settings), "old_text": "a", "new_text": "b"}, workspace).action == "block"


def test_outside_edit_is_backed_up_and_rolls_back(places):
    workspace, settings = places
    profile("ask")
    runner = ToolRunner(workspace)
    args = {"path": str(settings), "old_text": "quality=low", "new_text": "quality=high"}
    asked = runner.run("file.edit", args)
    assert asked["approval_required"] and asked["external"] == [str(settings.resolve())]
    assert "low" in settings.read_text(encoding="utf-8")
    done = runner.run("file.edit", args, approved=True)
    assert done["ok"] and done["outside_project"] and done["path"] == str(settings.resolve())
    assert "quality=high" in settings.read_text(encoding="utf-8")
    with storage.db() as conn:
        row = conn.execute("SELECT project_id FROM change_sets WHERE id=?", (done["change_set_id"],)).fetchone()
    assert row["project_id"] is None
    restored = rollback_change_set(done["change_set_id"])
    assert restored["rolled_back"], restored
    assert settings.read_text(encoding="utf-8") == "[video]\nquality=low\n"
    # одобрение одного вызова не открывает тот же файл следующему
    assert runner.run("file.read", {"path": str(settings)})["approval_required"]


def test_full_mode_reads_and_writes_outside_without_questions(places):
    workspace, settings = places
    profile("full")
    runner = ToolRunner(workspace, review_actions=True)
    assert "quality=low" in runner.run("file.read", {"path": str(settings)})["content"]
    created = runner.run("file.create", {"path": str(settings.parent / "new.cfg"), "content": "fps=144\n"})
    assert created["ok"] and (settings.parent / "new.cfg").read_text(encoding="utf-8") == "fps=144\n"


def test_review_decision_only_inside_agent_loop(places):
    workspace, _ = places
    profile("trusted")
    agent = ToolRunner(workspace, review_actions=True).run("file.create", {"path": "a.py", "content": "x = 1\n"})
    assert agent["approval_required"] and agent["review"] and not (workspace / "a.py").exists()
    # прямой вызов из интерфейса делает сам человек
    direct = ToolRunner(workspace).run("file.create", {"path": "a.py", "content": "x = 1\n"})
    assert direct["ok"] and (workspace / "a.py").exists()


def test_verdict_thresholds_are_enforced_locally():
    allowed = parse_verdict('{"necessity": 9, "safety": 8, "decision": "allow", "reason": "Нужно для задачи"}')
    assert allowed["allowed"] and allowed["reason"] == "Нужно для задачи"
    # модель сказала allow, но оценки ниже порога
    assert not parse_verdict('{"necessity": 4, "safety": 9, "decision": "allow", "reason": "x"}')["allowed"]
    assert not parse_verdict('{"necessity": 9, "safety": 7, "decision": "allow", "reason": "x"}', dangerous=True)["allowed"]
    assert not parse_verdict('{"necessity": 9, "safety": 9, "decision": "ask", "reason": "x"}')["allowed"]
    for broken in ("", "allow", '{"necessity": "9", "safety": 9, "decision": "allow"}', '{"necessity": 11, "safety": 9}'):
        with pytest.raises(ValueError):
            parse_verdict(broken)


def test_review_request_is_small_and_marks_untrusted_content():
    content = "x" * 50_000
    action = describe_action("file.write_patch", {"path": "big.txt", "content": content})
    messages = review_request(task="Сделай файл", plan=[{"title": "Шаг"}], workspace="C:/p",
                              tool="file.write_patch", args={"path": "big.txt", "content": content}, action=action)
    assert messages[0]["content"] == REVIEW_PROMPT and "untrusted" in REVIEW_PROMPT
    assert len(messages[1]["content"]) < 6000


class ReviewClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        yield {"content": self.reply}


async def passthrough(iterator):
    async for item in iterator:
        yield item


@pytest.mark.asyncio
async def test_review_failure_falls_back_to_the_user():
    action = describe_action("shell.run", {"command": "npm test"})
    common = dict(model="dokky-code", responsive=passthrough, task="Прогони тесты", plan=[], workspace=None,
                  tool="shell.run", args={"command": "npm test"}, action=action)
    good = ReviewClient('{"necessity": 9, "safety": 8, "decision": "allow", "reason": "Тесты нужны"}')
    verdict = await review_action(good, **common)
    assert verdict["allowed"] and good.calls[0]["tools"] == []
    assert good.calls[0]["options"]["max_tokens"] <= 300
    for client in (ReviewClient(error=RuntimeError("network")), ReviewClient("не JSON")):
        verdict = await review_action(client, **common)
        assert not verdict["allowed"] and not verdict["available"]


def test_describe_action_is_readable_without_json():
    command = describe_action("shell.run", {"command": "pip install requests"})
    assert command["title"] == "Выполнить команду" and command["kind"] == "command"
    assert command["detail"] == "pip install requests"
    edit = describe_action("file.edit", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})
    assert edit["title"] == "Изменить файл" and "«x = 1»" in edit["detail"]
    unknown = describe_action("custom.tool", {"alpha": 1, "beta": "два"})
    assert "{" not in unknown["detail"] and "alpha: 1" in unknown["detail"]


def test_full_access_requires_explicit_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "api.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "a"})
    with storage.db() as conn:
        project = conn.execute("INSERT INTO projects(name,path) VALUES (?,?)", ("p", str(tmp_path))).lastrowid
    monkeypatch.delenv("KODDI_INTERNAL_TEST")
    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky.store, "load", lambda: {"access_token": "test-only"})
    client = TestClient(create_app())
    assert client.put("/api/settings", json={"project_id": project, "permission_profile": "full"}).status_code == 422
    assert permission_profile(project) == "trusted"
    assert client.put("/api/settings", json={"project_id": project, "permission_profile": "allow-all"}).status_code == 422
    saved = client.put("/api/settings", json={"project_id": project, "permission_profile": "full", "confirm_full_access": True})
    assert saved.status_code == 200 and saved.json()["permission_profile"] == "full"
    assert permission_profile(project) == "full"
    assert client.put("/api/settings", json={"permission_profile": "full", "confirm_full_access": True}).status_code == 200


class AgentClient:
    """Модель агента создаёт файл; запрос проверки отличается системным промптом."""

    def __init__(self, verdict):
        self.verdict, self.steps, self.reviews = verdict, 0, 0

    async def stream(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        if system == REVIEW_PROMPT:
            self.reviews += 1
            yield {"content": self.verdict}
            return
        if system.startswith("Generate only a concise chat title"):
            yield {"content": "Файл"}
            return
        self.steps += 1
        if self.steps == 1:
            yield {"tool_call": {"id": "c1", "name": "file.create", "arguments": json.dumps({"path": "a.py", "content": "x = 1\n"})}}
        else:
            yield {"content": "Готово"}


async def run_agent(client, workspace, chat):
    events = []
    async for event in AgentOrchestrator(client).run(
        chat_id=chat, project_id=None, workspace=workspace, user_message="Создай a.py", model="test"
    ):
        events.append(event)
        if "event: agent.input_required" in event:
            data = json.loads(event.split("data: ", 1)[1])
            events.append(data)
            with storage.db() as conn:
                conn.execute("UPDATE agent_requests SET response_json=? WHERE id=?", ('{"approved": true}', data["id"]))
        await asyncio.sleep(0)
    return events


def new_chat():
    with storage.db() as conn:
        return int(conn.execute("INSERT INTO chats(title,mode,model) VALUES ('T','development','test')").lastrowid)


@pytest.mark.asyncio
async def test_automatic_mode_runs_reviewed_action_without_question(places):
    workspace, _ = places
    profile("trusted")
    client = AgentClient('{"necessity": 10, "safety": 9, "decision": "allow", "reason": "Файл просили создать"}')
    events = await run_agent(client, workspace, new_chat())
    assert client.reviews == 1 and (workspace / "a.py").exists()
    assert not any(isinstance(item, dict) for item in events)
    assert any("Докки проверил и разрешил" in item for item in events if isinstance(item, str))


@pytest.mark.asyncio
async def test_doubtful_review_asks_user_with_readable_action(places):
    workspace, _ = places
    profile("trusted")
    client = AgentClient('{"necessity": 3, "safety": 9, "decision": "ask", "reason": "Не видно, зачем это нужно"}')
    events = await run_agent(client, workspace, new_chat())
    request = next(item for item in events if isinstance(item, dict))
    assert request["action"]["title"] == "Создать файл" and request["action"]["target"] == "a.py"
    assert request["review"]["reason"] == "Не видно, зачем это нужно"
    assert (workspace / "a.py").exists()


@pytest.mark.asyncio
async def test_manual_mode_never_calls_reviewer(places):
    workspace, _ = places
    profile("ask")
    client = AgentClient("{}")
    events = await run_agent(client, workspace, new_chat())
    assert client.reviews == 0
    request = next(item for item in events if isinstance(item, dict))
    assert request["action"]["title"] == "Создать файл" and request["review"] is None
