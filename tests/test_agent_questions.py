from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("KODDI_DATA_DIR", os.path.abspath(".test-data-api"))
os.environ.setdefault("koddi_PORTABLE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from koddi.agents import questions  # noqa: E402
from koddi.agents.orchestrator import CONTROL_TOOLS  # noqa: E402
from koddi.api import create_app  # noqa: E402
from koddi.storage.db import db  # noqa: E402

ASKED = {
    "questions": [
        {"question": "Где ошибка?", "header": "Экран", "options": [{"label": "Лента"}, {"label": "Поле", "description": "внизу"}, {"label": "лента"}]},
        {"question": "Какие ОС?", "options": [{"label": "Windows"}, {"label": "macOS"}], "multi_select": True},
        {"question": "Что ещё?"},
    ]
}


def test_questions_are_normalized_and_old_single_question_still_works():
    asked = questions.normalize(ASKED)
    assert [item["question"] for item in asked] == ["Где ошибка?", "Какие ОС?", "Что ещё?"]
    # повтор варианта без учёта регистра отбрасывается, multi_select без вариантов не имеет смысла
    assert [option["label"] for option in asked[0]["options"]] == ["Лента", "Поле"]
    assert asked[1]["multi_select"] is True and asked[2]["multi_select"] is False
    assert questions.normalize({"question": "  Какой порт?  "})[0]["question"] == "Какой порт?"
    assert questions.normalize({"question": "  "}) == [] and questions.normalize({"questions": []}) == []


def test_tool_schema_accepts_several_questions():
    from jsonschema import Draft202012Validator

    schema = next(tool["function"]["parameters"] for tool in CONTROL_TOOLS if tool["function"]["name"] == "task.ask_user")
    assert not list(Draft202012Validator(schema).iter_errors(ASKED))
    assert list(Draft202012Validator(schema).iter_errors({"questions": [{"question": "q"}] * 5}))


def test_answers_are_checked_against_the_questions():
    asked = questions.normalize(ASKED)
    good = questions.accept(asked, {"answers": [
        {"selected": ["Поле"], "text": ""},
        {"selected": ["Windows", "macOS"], "text": "и Linux"},
        {"selected": [], "text": "  всё  "},
    ]})
    assert good["answers"][2]["text"] == "всё"
    assert good["answer"] == "Где ошибка?\n→ Поле\n\nКакие ОС?\n→ Windows; macOS; и Linux\n\nЧто ещё?\n→ всё"
    for bad in (
        {"answers": [{"selected": ["Поле"], "text": ""}]},
        {"answers": [{"selected": ["Нет такого"], "text": ""}, {"selected": ["Windows"], "text": ""}, {"selected": [], "text": "x"}]},
        {"answers": [{"selected": ["Лента", "Поле"], "text": ""}, {"selected": ["Windows"], "text": ""}, {"selected": [], "text": "x"}]},
        {"answers": [{"selected": [], "text": " "}, {"selected": ["Windows"], "text": ""}, {"selected": [], "text": "x"}]},
        {"answers": [{"selected": [], "text": "x" * 4001}, {"selected": ["Windows"], "text": ""}, {"selected": [], "text": "x"}]},
        {"answer": ""},
        "строка",
    ):
        with pytest.raises(ValueError):
            questions.accept(asked, bad)
    # телефон отвечает одним текстом на все вопросы
    assert questions.accept(asked, {"answer": "сделай как считаешь"})["answers"][0]["text"] == "сделай как считаешь"


def test_respond_endpoint_rejects_answers_that_do_not_fit():
    client = TestClient(create_app())
    chat = client.post("/api/chats", json={"title": "Вопросы", "mode": "development", "model": "test"}).json()
    with db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?, 'running')", (chat["id"],)).lastrowid
        payload = {"question": "x", "questions": questions.normalize(ASKED)}
        request = conn.execute(
            "INSERT INTO agent_requests(turn_id,kind,payload_json) VALUES (?, 'question', ?)", (turn, json.dumps(payload, ensure_ascii=False))
        ).lastrowid
    url = f"/api/agent/requests/{request}/respond"
    assert client.post(url, json={"answers": [{"selected": ["Лента"], "text": ""}]}).status_code == 422
    answers = [{"selected": ["Лента"], "text": ""}, {"selected": ["macOS"], "text": ""}, {"selected": [], "text": "нет"}]
    assert client.post(url, json={"answers": answers}).status_code == 200
    with db() as conn:
        stored = json.loads(conn.execute("SELECT response_json FROM agent_requests WHERE id=?", (request,)).fetchone()[0])
    assert stored["answers"][1]["selected"] == ["macOS"] and "→ нет" in stored["answer"]
    assert client.post(url, json={"answers": answers}).status_code == 409


def test_answer_for_another_turn_does_not_reach_a_reused_request_id():
    client = TestClient(create_app())
    chat = client.post("/api/chats", json={"title": "Чужой ход", "mode": "development", "model": "test"}).json()
    with db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?, 'running')", (chat["id"],)).lastrowid
        request = conn.execute(
            "INSERT INTO agent_requests(turn_id,kind,payload_json) VALUES (?, 'question', ?)", (turn, json.dumps({"question": "Порт?"}))
        ).lastrowid
    url = f"/api/agent/requests/{request}/respond"
    assert client.post(url, json={"answer": "8080", "turn_id": turn + 1000}).status_code == 409
    assert client.post(url, json={"answer": "8080", "turn_id": turn}).status_code == 200
    with db() as conn:
        stored = json.loads(conn.execute("SELECT response_json FROM agent_requests WHERE id=?", (request,)).fetchone()[0])
    assert "turn_id" not in stored and stored["answer"].endswith("8080")
