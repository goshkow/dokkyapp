import importlib

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.skills import SkillError, create_skill, delete_skill, list_skills, skill_block
from koddi.skills.catalog import BUILTIN
from koddi.skills.service import MAX_INSTRUCTIONS


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "skills.sqlite3")
    storage.init_db()


def test_catalog_has_eighteen_complete_skills():
    assert len(BUILTIN) == 18
    assert len({item["key"] for item in BUILTIN}) == 18 and len({item["name"] for item in BUILTIN}) == 18
    for item in BUILTIN:
        assert 30 < len(item["description"]) < 200, item["key"]
        assert 800 < len(item["instructions"]) <= MAX_INSTRUCTIONS, item["key"]
        assert "Формат ответа" in item["instructions"] or "Результат:" in item["instructions"], item["key"]
        # навыки переписаны под Докки: чужих названий продуктов и ссылок на каталоги нет
        for foreign in ("Claude", "claudeskills", "Anthropic", "references/"):
            assert foreign not in item["instructions"] + item["description"], (item["key"], foreign)


def test_builtin_skills_appear_by_themselves_and_are_not_duplicated():
    first = list_skills()
    second = list_skills()
    assert len(first) == len(second) == 18 and all(item["builtin"] for item in first)
    assert {item["category"] for item in first} >= {"Код", "Архитектура", "Маркетинг", "Дизайн", "Git"}


def test_builtin_skills_are_read_only_but_can_be_copied():
    review = next(item for item in list_skills() if item["name"] == "Ревью кода")
    with pytest.raises(SkillError):
        delete_skill(review["id"])
    with pytest.raises(SkillError):
        create_skill("Ревью кода", "своё", {"instructions": "подмена"})
    with pytest.raises(SkillError):
        create_skill("Другое имя", "", {"instructions": "x"}, skill_id=review["id"])
    copy = create_skill("Ревью кода (мой)", "С правками", {"instructions": review["workflow"]["instructions"] + "\nЕщё проверяй доступность."})
    assert copy["builtin"] is False and copy["category"] == "Мои навыки"
    updated = create_skill("Ревью кода (мой)", "Новое", {"instructions": "Коротко."}, skill_id=copy["id"])
    assert updated["workflow"]["instructions"] == "Коротко."
    delete_skill(copy["id"])
    assert len(list_skills()) == 18


def test_user_skill_cannot_claim_to_be_builtin_or_exceed_limits():
    skill = create_skill("Мой навык", "", {"builtin": True, "instructions": "Делай так."})
    assert skill["builtin"] is False
    with pytest.raises(SkillError):
        create_skill("Огромный", "", {"instructions": "я" * (MAX_INSTRUCTIONS + 1)})
    with pytest.raises(SkillError):
        create_skill("  ", "", {})


def test_skill_block_is_a_user_instruction_with_selected_names_only():
    skills = {item["name"]: item["id"] for item in list_skills()}
    text, names = skill_block([skills["Ревью кода"], skills["Безопасный рефакторинг"], skills["Ревью кода"]])
    assert names == ["Ревью кода", "Безопасный рефакторинг"]
    assert text.count("<skill name=") == 2 and "Это его инструкции" in text
    assert skill_block([]) == ("", [])
    with pytest.raises(SkillError):
        skill_block([999999])
    with pytest.raises(SkillError):
        skill_block(list(skills.values())[:6])


def test_legacy_checklist_skill_still_works_as_instruction():
    skill = create_skill("Старый", "Проверка релиза", {"steps": ["линтер", "тесты"]})
    text, _ = skill_block([skill["id"]])
    assert "1. линтер" in text and "2. тесты" in text


def test_selected_skills_travel_in_the_user_message_but_not_in_the_visible_text(monkeypatch):
    app_module = importlib.import_module("koddi.api.app")
    seen = {}

    class Recorder:
        async def run(self, **kwargs):
            seen.update(kwargs)
            return
            yield  # noqa: B901

    monkeypatch.setattr(app_module, "AgentOrchestrator", Recorder)
    client = TestClient(create_app())
    chat = client.post("/api/chats", json={"title": "Чат", "mode": "development", "model": "dokky-code"}).json()
    skill = next(item for item in client.get("/api/skills").json() if item["name"] == "Написание тестов")
    answer = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Покрой модуль оплаты", "skill_ids": [skill["id"]]})
    assert answer.status_code == 200
    assert seen["user_message"].startswith("Пользователь подключил навыки")
    assert skill["workflow"]["instructions"][:60] in seen["user_message"]
    assert seen["user_message"].endswith("Покрой модуль оплаты")
    assert seen["display_message"] == "Навыки: Написание тестов\n\nПокрой модуль оплаты"
    broken = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "x", "skill_ids": [424242]})
    assert broken.status_code == 422
    assert client.delete(f"/api/skills/{skill['id']}").status_code == 409
