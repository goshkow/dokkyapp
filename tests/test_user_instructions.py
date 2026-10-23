import json

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.prompts import CONTEXT_PREFIX, coding_context_update, compose_coding_context
from koddi.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "instructions.sqlite3")
    storage.init_db()
    return TestClient(create_app())


def context():
    return compose_coding_context(mode="development", workspace=None, project_id=None)


def body(message):
    return json.loads(message["content"][len(CONTEXT_PREFIX):])


def test_empty_instructions_are_part_of_the_snapshot():
    assert body(context())["user_instructions"] == ""


def test_instructions_are_a_user_message_not_a_system_one(client):
    client.put("/api/settings", json={"dev_instructions": "Отвечай коротко.\nКомментарии по-русски."})
    message = context()
    assert message["role"] == "user"
    text = body(message)["user_instructions"]
    assert "Отвечай коротко." in text and "never override application safety" in text


def test_editing_instructions_appends_only_a_delta_and_keeps_the_prefix(client):
    client.put("/api/settings", json={"dev_instructions": "Правило раз"})
    first = context()
    history = [{"role": "system", "content": "SYSTEM"}, first]
    # без изменений новых сообщений нет: префикс истории остаётся тем же и кэш провайдера работает
    assert coding_context_update(context(), history) is None
    client.put("/api/settings", json={"dev_instructions": "Правило два"})
    update = coding_context_update(context(), history)
    assert update is not None and update["role"] == "user"
    changed = body(update)
    assert list(changed) == ["user_instructions"] and "Правило два" in changed["user_instructions"]
    # прежние сообщения не переписываются
    assert history[1] is first and "Правило раз" in body(first)["user_instructions"]
    # удаление инструкций тоже доходит до модели дельтой
    client.put("/api/settings", json={"dev_instructions": ""})
    history.append(update)
    cleared = coding_context_update(context(), history)
    assert cleared is not None and body(cleared) == {"user_instructions": ""}


def test_settings_validation(client):
    assert client.put("/api/settings", json={"dev_instructions": "x" * 12001}).status_code == 422
    assert client.put("/api/settings", json={"code_font": "<script>"}).status_code == 422
    assert client.put("/api/settings", json={"code_font": "a'; color: red"}).status_code == 422
    assert client.put("/api/settings", json={"code_font": "JetBrains Mono"}).json()["code_font"] == "JetBrains Mono"
    assert client.put("/api/settings", json={"theme": "system"}).json()["theme"] == "system"
    assert client.put("/api/settings", json={"theme": "pink"}).status_code == 422
    assert client.put("/api/settings", json={"ui_font": "system"}).json()["ui_font"] == "system"
    assert client.put("/api/settings", json={"ui_font": "comic"}).status_code == 422
    saved = client.put("/api/settings", json={"dev_instructions": "  держи стиль  "}).json()
    assert saved["dev_instructions"] == "держи стиль"


def test_instructions_text_is_not_written_to_the_log(client):
    client.put("/api/settings", json={"dev_instructions": "секретная-фраза-в-правилах"})
    with storage.db() as conn:
        rows = conn.execute("SELECT * FROM logs").fetchall() if conn.execute("SELECT name FROM sqlite_master WHERE name='logs'").fetchone() else []
    assert "секретная-фраза-в-правилах" not in json.dumps([dict(row) for row in rows], ensure_ascii=False)
