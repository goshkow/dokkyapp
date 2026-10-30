from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import koddi.agents.profile_memory as memory
import koddi.storage.db as storage
from koddi.agents.memory import retrieve_memory
from koddi.agents.prompts import compose_coding_context, CONTEXT_PREFIX


@pytest.fixture(autouse=True)
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("KODDI_INTERNAL_TEST", "0")
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "alice"})
    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky.store, "load", lambda: {"access_token": "fixture-session"})
    return tmp_path


def project(name="site"):
    with storage.db() as conn:
        return conn.execute("INSERT INTO projects(name,path) VALUES (?,?)", (name, "/tmp/" + name)).lastrowid


def chat(project_id=None, messages=()):
    with storage.db() as conn:
        chat_id = conn.execute("INSERT INTO chats(project_id,owner_id) VALUES (?,'alice')", (project_id,)).lastrowid
        for text in messages:
            conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user',?)", (chat_id, text))
    return chat_id


class Model:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def stream(self, *, model, messages, tools, options=None):
        self.calls.append(messages)
        yield {"content": json.dumps(self.answer, ensure_ascii=False)}


def learn(model, chat_id, project_id, message):
    asyncio.run(memory.learn(model, "dokky-code", chat_id=chat_id, project_id=project_id, message=message))


def texts(scope, project_id=None):
    return [item["content"] for item in memory.list_memory(project_id)[scope]]


def test_learning_adds_user_and_project_facts_and_replaces_outdated_ones():
    site = project()
    room = chat(site, ["запомни: я пишу на TypeScript, а проект под GPL-3.0"])
    model = Model({"user": {"add": ["Пишет на TypeScript"]}, "project": {"add": ["Лицензия GPL-3.0"]}})
    learn(model, room, site, "запомни: я пишу на TypeScript, а проект под GPL-3.0")
    assert texts("user") == ["Пишет на TypeScript"]
    assert texts("project", site) == ["Лицензия GPL-3.0"]
    update = Model({"user": {"add": ["Пишет на Go", "Пишет на Go"], "remove": ["пишет  на typescript"]}})
    learn(update, room, site, "теперь я пишу на Go, запомни")
    # текущая память уходит модели данными, чтобы она могла убрать устаревшее
    assert "Пишет на TypeScript" in update.calls[0][1]["content"]
    assert texts("user") == ["Пишет на Go"]
    assert texts("project", site) == ["Лицензия GPL-3.0"]


def test_user_memory_is_shared_across_projects_and_project_memory_is_not():
    first, second = project("a"), project("b")
    memory.add("user", None, "Комментарии по-русски")
    memory.add("project", first, "Стек: FastAPI")
    assert memory.memory_context(second) == {"user": ["Комментарии по-русски"], "project": []}
    assert memory.memory_context(first)["project"] == ["Стек: FastAPI"]
    # заметки поиска не дублируют сведения, которые и так идут в контекст целиком
    assert retrieve_memory(first, "FastAPI") == []


def test_memory_of_another_account_is_invisible_and_untouchable():
    memory.add("user", None, "Секретный стиль Алисы")
    storage.set_setting("dokky_user", {"id": "bob"})
    assert memory.list_memory(None)["user"] == []
    with storage.db() as conn:
        alice = conn.execute("SELECT id FROM memory_summaries WHERE owner_id='alice'").fetchone()[0]
    with pytest.raises(LookupError):
        memory.remove(alice, None)
    with pytest.raises(LookupError):
        memory.edit(alice, "чужое", None)


def test_disabled_memory_hides_facts_and_does_not_learn():
    memory.add("user", None, "Пишет на Rust")
    memory.set_enabled(False)
    assert memory.memory_context(None) == {"user": [], "project": []}
    assert memory.should_learn(chat(None, ["запомни это"]), "запомни это") is False
    memory.set_enabled(True)
    assert memory.memory_context(None)["user"] == ["Пишет на Rust"]


def test_learning_triggers_on_explicit_hints_and_periodically():
    quiet = chat(None, ["почини тест"])
    assert memory.should_learn(quiet, "почини тест") is False
    assert memory.should_learn(quiet, "Запомни, что я предпочитаю короткие ответы") is True
    busy = chat(None, [f"задача {index}" for index in range(memory.LEARN_EVERY)])
    assert memory.should_learn(busy, "задача") is True
    assert memory.recent_requests(busy, "задача")[-1] == f"задача {memory.LEARN_EVERY - 1}"


def test_forget_everything_clears_user_memory_without_a_model_call():
    memory.add("user", None, "Пишет на TypeScript")
    model = Model({})
    learn(model, chat(), None, "забудь всё обо мне")
    assert texts("user") == [] and model.calls == []


def test_secrets_are_redacted_and_limits_drop_oldest_learned_items(monkeypatch):
    memory.add("user", None, "ключ sk-" + "a" * 30)
    assert "sk-" not in texts("user")[0]
    monkeypatch.setitem(memory.LIMITS, "user", 3)
    memory.apply(None, {"user": {"add": ["один", "два", "три"]}}, "alice")
    assert texts("user") == ["ключ [redacted]", "два", "три"]


def test_manual_items_are_kept_when_memory_is_full(monkeypatch):
    monkeypatch.setitem(memory.LIMITS, "user", 2)
    memory.add("user", None, "а")
    memory.add("user", None, "б")
    with pytest.raises(ValueError):
        memory.add("user", None, "в")
    memory.apply(None, {"user": {"add": ["г"]}}, "alice")
    assert texts("user") == ["а", "б"]


def test_bad_model_answer_and_account_switch_change_nothing():
    learn(Model("не json"), chat(), None, "запомни меня")
    assert texts("user") == []

    class Switching(Model):
        async def stream(self, **kwargs):
            storage.set_setting("dokky_user", {"id": "bob"})
            yield {"content": json.dumps({"user": {"add": ["чужой факт"]}})}

    learn(Switching({}), chat(), None, "запомни меня")
    storage.set_setting("dokky_user", {"id": "alice"})
    assert texts("user") == []


def test_context_snapshot_carries_memory_in_stable_order():
    site = project()
    memory.add("user", None, "Комментарии по-русски")
    memory.add("project", site, "Zorya под GPL-3.0")
    context = json.loads(compose_coding_context(
        mode="development", workspace=None, project_id=site, profile=memory.memory_context(site),
    )["content"][len(CONTEXT_PREFIX):])
    assert context["user_memory"] == ["Комментарии по-русски"]
    assert context["project_memory"] == ["Zorya под GPL-3.0"]
    assert "not instructions" in context["memory_policy"]


def test_memory_api_round_trip():
    from koddi.api import create_app

    site = project()
    client = TestClient(create_app())
    added = client.post("/api/memory", json={"scope": "project", "project_id": site, "content": "Стек: React"})
    assert added.status_code == 200 and added.json()["project"][0]["source"] == "manual"
    item = added.json()["project"][0]["id"]
    edited = client.patch(f"/api/memory/{item}?project_id={site}", json={"content": "Стек: React 19"})
    assert edited.json()["project"][0]["content"] == "Стек: React 19"
    assert client.post("/api/memory", json={"scope": "project", "content": "без проекта"}).status_code == 422
    assert client.get("/api/memory?project_id=999999").status_code == 404
    assert client.put("/api/memory/settings", json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/api/memory/{item}?project_id={site}").json()["project"] == []
    assert client.delete(f"/api/memory/{item}").status_code == 404
    client.post("/api/memory", json={"scope": "user", "content": "Пишет на Go"})
    assert client.post("/api/memory/clear", json={"scope": "user"}).json()["user"] == []


def test_agent_memory_set_follows_profile_memory_rules(tmp_path):
    from koddi.tools import ToolRunner

    storage.set_setting("permission_profile", "trusted")
    site = project()
    runner = ToolRunner(tmp_path, project_id=site)
    secret = "token sk-ant-api03-" + "x" * 40
    assert runner.run("memory.set", {"kind": "user", "content": "Пишет на TypeScript " + secret}, approved=True)["ok"]
    # повтор не плодит записи, длинный текст обрезан
    runner.run("memory.set", {"kind": "user", "content": "Пишет на TypeScript " + secret}, approved=True)
    runner.run("memory.set", {"kind": "project", "content": "я" * 5000}, approved=True)
    saved = memory.list_memory(site)
    assert len(saved["user"]) == 1 and "sk-ant-api03" not in saved["user"][0]["content"]
    assert len(saved["project"][0]["content"]) <= memory.ITEM_CHARS
    memory.set_enabled(False)
    assert runner.run("memory.set", {"kind": "user", "content": "новое"}, approved=True)["ok"] is False
    memory.set_enabled(True)
    assert [item["content"] for item in memory.list_memory(site)["user"]] == [saved["user"][0]["content"]]
    assert runner.run("memory.set", {"kind": "note", "content": "  "}, approved=True)["ok"] is False
