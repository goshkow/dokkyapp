from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from koddi.automations import schedule as rules
from koddi.logs import log_event
from koddi.storage.db import db

_RUNNING: dict[int, threading.Event] = {}
_LOCK = threading.Lock()

MAX_PROMPT = 20000
MAX_COMMAND = 4000
# проверки, которые можно запускать без участия агента (прежний вид действия `{"tool": ...}`)
CHECKS = {"diagnostics.collect": "Собрать диагностику проекта", "devserver.detect": "Найти сервер разработки"}

AgentLauncher = Callable[[dict], Awaitable[dict]]


class AutomationError(ValueError):
    pass


def _project(project_id: Any) -> dict:
    if type(project_id) is not int:
        raise AutomationError("Выберите проект")
    with db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise AutomationError("Проект не найден")
    return dict(row)


def _dangerous(command: str) -> bool:
    from koddi.policy.engine import DANGEROUS_SHELL

    return any(marker.lower() in command.lower() for marker in DANGEROUS_SHELL)


def normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    """Приводит действие к одному из видов: `agent`, `command`, `tool`; лишние поля отбрасываются."""
    if not isinstance(action, dict):
        raise AutomationError("Не указано, что должна делать автоматизация")
    legacy = {"check_ci": "diagnostics.collect", "monitor_devserver": "devserver.detect"}
    kind = action.get("kind") or ("tool" if action.get("tool") or action.get("type") in legacy else "")
    if kind == "agent":
        prompt = str(action.get("prompt") or "").strip()
        if not prompt or len(prompt) > MAX_PROMPT:
            raise AutomationError(f"Задание для агента: от 1 до {MAX_PROMPT} знаков")
        _project(action.get("project_id"))
        return {"kind": "agent", "prompt": prompt, "project_id": action["project_id"]}
    if kind == "command":
        command = str(action.get("command") or "").strip()
        if not command or len(command) > MAX_COMMAND:
            raise AutomationError(f"Команда: от 1 до {MAX_COMMAND} знаков")
        if _dangerous(command):
            raise AutomationError("Команда выглядит опасной (удаление, форматирование, выключение). Такие команды в автоматизациях не запускаются: выполните её вручную")
        _project(action.get("project_id"))
        return {"kind": "command", "command": command, "project_id": action["project_id"]}
    if kind == "tool":
        tool = str(action.get("tool") or legacy.get(str(action.get("type")), ""))
        if not tool:
            raise AutomationError("Не указана проверка")
        value: dict[str, Any] = {"kind": "tool", "tool": tool, "arguments": action.get("arguments") or {}}
        if action.get("project_id") is not None:
            value["project_id"] = _project(action.get("project_id"))["id"]
        if action.get("workspace"):
            value["workspace"] = str(action["workspace"])
        return value
    raise AutomationError("Не указано, что должна делать автоматизация")


def create_automation(name: str, action: dict[str, Any], schedule: str = "manual") -> dict:
    name = str(name).strip()
    if not name or len(name) > 80:
        raise AutomationError("Название: от 1 до 80 знаков")
    try:
        rules.parse(schedule)
    except rules.ScheduleError as exc:
        raise AutomationError(str(exc)) from exc
    clean = normalize_action(action)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO automations(name, schedule, action_json) VALUES (?, ?, ?)",
            (name, schedule, json.dumps(clean, ensure_ascii=False)),
        )
        row = conn.execute("SELECT * FROM automations WHERE id=?", (cur.lastrowid,)).fetchone()
    return _automation_dict(row)


def update_automation(automation_id: int, changes: dict[str, Any]) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
        if not row:
            raise AutomationError("Автоматизация не найдена")
        name = str(changes.get("name", row["name"])).strip()
        if not name or len(name) > 80:
            raise AutomationError("Название: от 1 до 80 знаков")
        schedule = str(changes.get("schedule", row["schedule"]))
        try:
            rules.parse(schedule)
        except rules.ScheduleError as exc:
            raise AutomationError(str(exc)) from exc
        action = normalize_action(changes["action"]) if "action" in changes else json.loads(row["action_json"])
        enabled = int(bool(changes["enabled"])) if "enabled" in changes else row["enabled"]
        # запуск по расписанию отсчитывается от момента правки: старый «просроченный» слот не сработает
        reset = ", last_run_at=COALESCE(last_run_at, CURRENT_TIMESTAMP)" if schedule != row["schedule"] and not row["last_run_at"] else ""
        conn.execute(
            f"UPDATE automations SET name=?, schedule=?, action_json=?, enabled=?{reset} WHERE id=?",
            (name, schedule, json.dumps(action, ensure_ascii=False), enabled, automation_id),
        )
        updated = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
    return _automation_dict(updated)


def list_automations() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM automations ORDER BY id DESC").fetchall()
    return [_automation_dict(row) for row in rows]


def get_automation(automation_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
    return _automation_dict(row) if row else None


def record_result(automation_id: int, status: str, summary: str, chat_id: int | None = None) -> dict | None:
    """Итог запуска для списка: статус, одна строка и чат агента, если он был."""
    with db() as conn:
        conn.execute(
            "UPDATE automations SET status=?, last_result=?, last_chat_id=COALESCE(?, last_chat_id), last_run_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, summary[:300], chat_id, automation_id),
        )
        row = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
    return _automation_dict(row) if row else None


def is_running(automation_id: int) -> bool:
    with _LOCK:
        return automation_id in _RUNNING


def run_automation(automation_id: int) -> dict:
    """Запуск проверки или команды; задания для агента запускаются отдельно (`run_any`), им нужен чат."""
    with _LOCK:
        if automation_id in _RUNNING:
            return {"ok": False, "error": "Автоматизация уже выполняется"}
        cancelled = threading.Event()
        _RUNNING[automation_id] = cancelled
    try:
        return _execute(automation_id, cancelled)
    finally:
        with _LOCK:
            _RUNNING.pop(automation_id, None)


async def run_any(automation_id: int, launch_agent: AgentLauncher | None) -> dict:
    """Запускает автоматизацию любого вида; ошибка запуска агента (нет доступа, нет проекта) попадает в итог, а не падает."""
    item = get_automation(automation_id)
    if not item:
        return {"ok": False, "error": "Автоматизация не найдена"}
    if item["action"].get("kind") != "agent":
        return await asyncio.to_thread(run_automation, automation_id)
    if launch_agent is None:
        return {"ok": False, "error": "Запуск агента сейчас недоступен"}
    if is_running(automation_id):
        return {"ok": False, "error": "Автоматизация уже выполняется"}
    with _LOCK:
        _RUNNING[automation_id] = threading.Event()
    try:
        record_result(automation_id, "running", "Запускаю агента…")
        try:
            result = await launch_agent(item)
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None)
            message = detail.get("message") if isinstance(detail, dict) else detail
            result = {"ok": False, "error": str(message or exc)}
        if result.get("ok"):
            updated = record_result(automation_id, "completed", "Агент запущен: ход работы в чате", result.get("chat", {}).get("id"))
        else:
            updated = record_result(automation_id, "failed", str(result.get("error") or "Не удалось запустить"))
        return {**result, "automation": updated}
    finally:
        with _LOCK:
            _RUNNING.pop(automation_id, None)


def _execute(automation_id: int, cancelled: threading.Event) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "automation not found"}
        conn.execute("UPDATE automations SET status='running' WHERE id=?", (automation_id,))
    item = _automation_dict(row)
    action = item["action"]
    try:
        kind = action.get("kind")
        if kind == "command":
            tool_name, arguments = "shell.run", {"command": action["command"]}
            workspace = _project(action.get("project_id"))["path"]
        else:
            tool_name = str(action.get("tool") or "")
            arguments = action.get("arguments") or {}
            workspace = action.get("workspace")
            if action.get("project_id") is not None:
                workspace = _project(action["project_id"])["path"]
        if not tool_name:
            raise ValueError("automation action requires tool and arguments")
        from koddi.tools import ToolRunner

        # команду человек сам записал в автоматизацию (или подтвердил, когда её создал агент): отдельного вопроса при запуске нет;
        # опасные команды отсекаются при создании и ещё раз здесь
        if kind == "command" and _dangerous(arguments["command"]):
            raise ValueError("Опасная команда не запускается автоматически")
        result = ToolRunner(Path(workspace) if workspace else None, cancel_event=cancelled).run(tool_name, arguments, approved=kind == "command")
        status = "cancelled" if cancelled.is_set() else "completed" if result.get("ok") else "failed"
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
        status = "failed"
    summary = _summary(status, result)
    updated = record_result(automation_id, status, summary)
    log_event("INFO" if result.get("ok") else "ERROR", "automation.run", item["name"], result)
    return {"ok": bool(result.get("ok")), "automation": updated, "result": result}


def _summary(status: str, result: dict) -> str:
    if status == "completed":
        return "Выполнено"
    if status == "cancelled":
        return "Остановлено"
    return str(result.get("error") or result.get("reason") or result.get("stderr") or "Завершилось с ошибкой").strip()[:300]


def cancel_automation(automation_id: int) -> dict:
    with _LOCK:
        active = _RUNNING.get(automation_id)
        if active:
            active.set()
    with db() as conn:
        row = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "automation not found"}
        conn.execute("UPDATE automations SET status='cancelled' WHERE id=?", (automation_id,))
        updated = conn.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
    return {"ok": True, "automation": _automation_dict(updated)}


def cancel_all() -> None:
    with _LOCK:
        for event in _RUNNING.values():
            event.set()


def due_automations(now: datetime | None = None) -> list[dict]:
    """Включённые автоматизации с расписанием, у которых наступил запланированный момент."""
    now = now or datetime.now()
    due = []
    for item in list_automations():
        if not item["enabled"] or item["schedule"] == "manual" or item["status"] == "running" or is_running(item["id"]):
            continue
        base = rules.to_local(item.get("last_run_at")) or rules.to_local(item.get("created_at"))
        if rules.is_due(item["schedule"], base, now):
            due.append(item)
    return due


async def run_scheduler(launch_agent: AgentLauncher, *, period: float = 30.0) -> None:
    """Фоновый цикл: раз в полминуты запускает то, что пора. Работает, пока открыто приложение."""
    while True:
        try:
            for item in await asyncio.to_thread(due_automations):
                # сначала отметка, потом запуск: сбой или долгий запуск не вызовет повтор в соседнем проходе
                record_result(item["id"], "idle", "Запуск по расписанию…")
                await run_any(item["id"], launch_agent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log_event("ERROR", "automation.scheduler", str(exc), {})
        await asyncio.sleep(period)


def _automation_dict(row) -> dict:
    item = dict(row)
    item["action"] = json.loads(item.pop("action_json") or "{}")
    item["enabled"] = bool(item.get("enabled", 1))
    action = item["action"]
    item["kind"] = action.get("kind") or ("tool" if action.get("tool") or action.get("type") else "")
    item["project_id"] = action.get("project_id")
    try:
        item["schedule_text"] = rules.describe(item["schedule"])
        upcoming = rules.next_run(
            item["schedule"], rules.to_local(item.get("last_run_at")) or rules.to_local(item.get("created_at")), datetime.now()
        ) if item["enabled"] else None
        item["next_run_at"] = upcoming.isoformat(timespec="minutes") if upcoming else None
    except rules.ScheduleError:
        item["schedule_text"], item["next_run_at"] = item["schedule"], None
    item["check_title"] = CHECKS.get(str(action.get("tool") or ""), "")
    return item
