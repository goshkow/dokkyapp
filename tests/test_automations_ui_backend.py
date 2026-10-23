import asyncio
import importlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.automations import AutomationError, create_automation, get_automation, run_any, update_automation
from koddi.automations import schedule as rules
from koddi.automations.service import due_automations


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "automations.sqlite3")
    storage.init_db()


def project(path="C:/work/demo"):
    with storage.db() as conn:
        return int(conn.execute("INSERT INTO projects(name, path) VALUES ('demo', ?)", (path,)).lastrowid)


def test_schedule_grammar_and_wording():
    assert rules.describe("manual") == "Вручную"
    assert rules.describe("hourly") == "Каждый час"
    assert rules.describe("interval@90") == "Каждые 90 мин" and rules.describe("interval@120") == "Каждые 2 ч"
    assert rules.describe("daily@09:30") == "Каждый день в 09:30"
    assert rules.describe("weekdays@08:00") == "По будням в 08:00"
    assert rules.describe("weekly@5@18:00") == "По пятницам в 18:00"
    for bad in ("", "daily", "daily@25:00", "interval@5", "interval@2000", "weekly@8@10:00", "cron * * * * *"):
        with pytest.raises(rules.ScheduleError):
            rules.parse(bad)


def test_is_due_runs_each_slot_once_and_catches_up_after_downtime():
    at = lambda day, hour, minute=0: datetime(2026, 10, day, hour, minute)  # noqa: E731
    # 2026-10-19 понедельник
    assert not rules.is_due("manual", at(19, 8), at(20, 8))
    assert rules.is_due("hourly", at(19, 9, 30), at(19, 10, 1))
    assert not rules.is_due("hourly", at(19, 10, 5), at(19, 10, 59))
    assert rules.is_due("daily@09:00", at(18, 10), at(19, 9, 1))
    assert not rules.is_due("daily@09:00", at(19, 9, 0), at(19, 23))
    # приложение было закрыто: пропущенный слот выполняется один раз при запуске
    assert rules.is_due("daily@09:00", at(15, 9), at(19, 20))
    assert not rules.is_due("daily@09:00", at(19, 20), at(19, 21))
    # будни и неделя
    assert not rules.is_due("weekdays@09:00", at(17, 9, 5), at(18, 12))  # суббота и воскресенье пропускаются
    assert rules.is_due("weekdays@09:00", at(16, 9, 5), at(19, 9, 5))
    assert rules.is_due("weekly@1@10:00", at(12, 10, 0), at(19, 10, 0))
    assert not rules.is_due("weekly@2@10:00", at(19, 10, 0), at(19, 23))
    assert rules.is_due("interval@30", at(19, 10, 0), at(19, 10, 30))
    assert not rules.is_due("interval@30", at(19, 10, 0), at(19, 10, 29))


def test_next_run_is_in_the_future():
    now = datetime(2026, 10, 19, 12, 0)
    assert rules.next_run("daily@09:00", None, now) == datetime(2026, 10, 20, 9, 0)
    assert rules.next_run("weekdays@09:00", None, datetime(2026, 10, 23, 12, 0)) == datetime(2026, 10, 26, 9, 0)
    assert rules.next_run("hourly", None, now) == datetime(2026, 10, 19, 13, 0)
    assert rules.next_run("manual", None, now) is None


def test_actions_are_validated_and_normalized():
    pid = project()
    agent = create_automation("Проверка PR", {"kind": "agent", "prompt": "  Посмотри новые коммиты  ", "project_id": pid, "лишнее": 1}, "daily@10:00")
    assert agent["action"] == {"kind": "agent", "prompt": "Посмотри новые коммиты", "project_id": pid}
    assert agent["kind"] == "agent" and agent["schedule_text"] == "Каждый день в 10:00" and agent["enabled"] is True
    assert create_automation("Тесты", {"kind": "command", "command": "pytest -q", "project_id": pid})["kind"] == "command"
    assert create_automation("CI", {"type": "check_ci"})["action"]["tool"] == "diagnostics.collect"
    for bad_action in ({}, {"kind": "agent", "prompt": "", "project_id": pid}, {"kind": "agent", "prompt": "x", "project_id": 999}, {"kind": "command", "command": "x"}, {"kind": "boom"}):
        with pytest.raises(AutomationError):
            create_automation("Плохая", bad_action)
    with pytest.raises(AutomationError):
        create_automation("Плохая", {"kind": "command", "command": "ls", "project_id": pid}, "каждую минуту")
    with pytest.raises(AutomationError):
        create_automation(" ", {"kind": "command", "command": "ls", "project_id": pid})


def test_update_toggle_and_schedule_do_not_trigger_an_old_slot():
    pid = project()
    item = create_automation("Ночная", {"kind": "command", "command": "pytest", "project_id": pid})
    changed = update_automation(item["id"], {"schedule": "daily@03:00", "enabled": False})
    assert changed["schedule"] == "daily@03:00" and changed["enabled"] is False and changed["next_run_at"] is None
    assert due_automations(datetime(2099, 1, 1, 12, 0)) == []  # выключенная не запускается
    enabled = update_automation(item["id"], {"enabled": True})
    assert [entry["id"] for entry in due_automations(datetime(2099, 1, 1, 12, 0))] == [item["id"]]
    assert enabled["next_run_at"]
    with pytest.raises(AutomationError):
        update_automation(item["id"], {"schedule": "никогда"})
    with pytest.raises(AutomationError):
        update_automation(424242, {"enabled": True})


def test_agent_automation_records_the_result_and_chat():
    pid = project()
    item = create_automation("Агент", {"kind": "agent", "prompt": "Сделай обзор", "project_id": pid})
    calls = []

    async def launcher(entry):
        calls.append(entry["action"]["prompt"])
        return {"ok": True, "chat": {"id": 77}}

    result = asyncio.run(run_any(item["id"], launcher))
    assert result["ok"] and calls == ["Сделай обзор"]
    saved = get_automation(item["id"])
    assert saved["status"] == "completed" and saved["last_chat_id"] == 77 and saved["last_run_at"]

    async def denied(entry):
        raise RuntimeError("Нужен Plus")

    failed = asyncio.run(run_any(item["id"], denied))
    assert failed["ok"] is False and get_automation(item["id"])["last_result"] == "Нужен Plus"
    assert asyncio.run(run_any(item["id"], None))["ok"] is False


def test_api_creates_patches_and_explains_errors():
    pid = project()
    client = TestClient(create_app())
    made = client.post("/api/automations", json={"name": "Утро", "schedule": "weekdays@09:00", "action": {"kind": "agent", "prompt": "Что нового", "project_id": pid}})
    assert made.status_code == 200 and made.json()["schedule_text"] == "По будням в 09:00"
    bad = client.post("/api/automations", json={"name": "Плохая", "schedule": "daily@99:00", "action": {"kind": "agent", "prompt": "x", "project_id": pid}})
    assert bad.status_code == 422 and "расписание" in bad.json()["detail"].lower()
    done = client.patch(f"/api/automations/{made.json()['id']}", json={"enabled": False})
    assert done.status_code == 200 and done.json()["enabled"] is False
    assert client.patch("/api/automations/999", json={"enabled": True}).status_code == 422


def test_assist_opens_a_chat_with_a_ready_instruction(monkeypatch):
    pid = project()
    client = TestClient(create_app())
    queued = []
    app_module = importlib.import_module("koddi.api.app")
    monkeypatch.setattr(app_module.request_queue, "add", lambda chat_id, payload, key: queued.append((chat_id, payload)) or {"items": []})
    answer = client.post("/api/automations/assist", json={"project_id": pid, "request": "Каждое утро проверяй, что тесты проходят"})
    assert answer.status_code == 200 and answer.json()["chat"]["title"] == "Новая автоматизация"
    content = queued[0][1]["content"]
    assert "automation.create" in content and f"project_id={pid}" in content and "Каждое утро проверяй" in content
    assert client.post("/api/automations/assist", json={"project_id": 999, "request": "что-то"}).status_code == 422


def test_runs_list_combines_turns_and_queue_with_owner_filter():
    pid = project()
    client = TestClient(create_app())
    chat = client.post("/api/chats", json={"title": "Задача А", "project_id": pid, "mode": "development", "model": "dokky-code"}).json()
    with storage.db() as conn:
        turn = conn.execute("INSERT INTO turns(chat_id, status, summary) VALUES (?, 'completed', 'ok')", (chat["id"],)).lastrowid
        conn.execute("INSERT INTO messages(chat_id, role, content, metadata_json) VALUES (?, 'user', 'Почини таймер', ?)", (chat["id"], '{"turn_id": %d}' % turn))
        failed = conn.execute("INSERT INTO turns(chat_id, status, summary, error_json) VALUES (?, 'failed', 'Нужен Plus', '{\"code\": \"plan_required\"}')", (chat["id"],)).lastrowid
    runs = client.get("/api/runs").json()
    by_status = {item["status"]: item for item in runs}
    assert by_status["completed"]["request"] == "Почини таймер" and by_status["completed"]["project_name"] == "demo"
    assert by_status["failed"]["error"] == "plan_required" and by_status["failed"]["summary"] == "Нужен Plus"
    assert failed and client.get(f"/api/runs?project_id={pid + 1}").json() == []


def test_command_automation_runs_without_a_question_but_dangerous_commands_are_refused(tmp_path):
    from koddi.automations import run_automation

    pid = project(str(tmp_path))
    item = create_automation("Эхо", {"kind": "command", "command": "echo auto-ok", "project_id": pid})
    result = run_automation(item["id"])
    assert result["ok"] is True, result
    assert get_automation(item["id"])["status"] == "completed"
    with pytest.raises(AutomationError):
        create_automation("Опасная", {"kind": "command", "command": "Remove-Item -Recurse C:/x", "project_id": pid})
    with pytest.raises(AutomationError):
        create_automation("Опасная 2", {"kind": "command", "command": "sudo reboot", "project_id": pid})


def test_the_development_agent_can_create_automations():
    from koddi.agents.modes import list_modes

    tools = next(mode for mode in list_modes() if mode["id"] == "development")["tools"]
    assert "automation.create" in tools
