from __future__ import annotations

import json
from typing import Any

from koddi.storage.db import db

MAX_INSTRUCTIONS = 20000
MAX_SELECTED = 5


class SkillError(ValueError):
    pass


def ensure_builtin() -> None:
    """Стандартные навыки появляются сами и обновляются с версией каталога; пользовательские с тем же именем не трогаются."""
    from koddi.skills.catalog import BUILTIN, VERSION

    with db() as conn:
        rows = {row["name"]: json.loads(row["workflow_json"] or "{}") for row in conn.execute("SELECT name, workflow_json FROM skills")}
        for item in BUILTIN:
            existing = rows.get(item["name"])
            if existing is not None and not existing.get("builtin"):
                continue
            if existing is not None and existing.get("version") == VERSION and existing.get("key") == item["key"]:
                continue
            workflow = {
                "builtin": True, "key": item["key"], "version": VERSION, "category": item["category"],
                "instructions": item["instructions"], "mode": "development",
            }
            conn.execute(
                """
                INSERT INTO skills(name, description, workflow_json) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET description=excluded.description, workflow_json=excluded.workflow_json
                """,
                (item["name"], item["description"], json.dumps(workflow, ensure_ascii=False)),
            )


def create_skill(
    name: str, description: str, workflow: dict[str, Any], task_id: int | None = None, *, skill_id: int | None = None
) -> dict:
    """Создаёт или меняет пользовательский навык. Стандартные менять нельзя: их можно скопировать под другим именем."""
    name = str(name).strip()
    if not name or len(name) > 80:
        raise SkillError("Название навыка: от 1 до 80 знаков")
    workflow = dict(workflow or {})
    workflow.pop("builtin", None)
    instructions = str(workflow.get("instructions") or "").strip()
    if len(instructions) > MAX_INSTRUCTIONS:
        raise SkillError(f"Инструкция навыка не длиннее {MAX_INSTRUCTIONS} знаков")
    if instructions:
        workflow["instructions"] = instructions
    with db() as conn:
        clash = conn.execute("SELECT id, workflow_json FROM skills WHERE name=?", (name,)).fetchone()
        if clash and json.loads(clash["workflow_json"] or "{}").get("builtin"):
            raise SkillError("Так называется стандартный навык. Выберите другое название")
        if skill_id is not None:
            current = conn.execute("SELECT workflow_json FROM skills WHERE id=?", (skill_id,)).fetchone()
            if not current:
                raise SkillError("Навык не найден")
            if json.loads(current["workflow_json"] or "{}").get("builtin"):
                raise SkillError("Стандартный навык нельзя менять: сделайте его копию")
            if clash and clash["id"] != skill_id:
                raise SkillError("Навык с таким названием уже есть")
            conn.execute(
                "UPDATE skills SET name=?, description=?, workflow_json=? WHERE id=?",
                (name, description, json.dumps(workflow, ensure_ascii=False), skill_id),
            )
            row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        else:
            conn.execute(
                """
                INSERT INTO skills(name, description, workflow_json, created_from_task_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET description=excluded.description, workflow_json=excluded.workflow_json
                """,
                (name, description, json.dumps(workflow, ensure_ascii=False), task_id),
            )
            row = conn.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
    return _skill_dict(row)


def delete_skill(skill_id: int) -> None:
    with db() as conn:
        row = conn.execute("SELECT workflow_json FROM skills WHERE id=?", (skill_id,)).fetchone()
        if row and json.loads(row["workflow_json"] or "{}").get("builtin"):
            raise SkillError("Стандартный навык удалить нельзя")
        conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))


def list_skills() -> list[dict]:
    ensure_builtin()
    with db() as conn:
        rows = conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
    return [_skill_dict(row) for row in rows]


def skill_instruction(skill: dict) -> str:
    """Текст навыка для запроса: своя инструкция, а у старых навыков - список шагов."""
    workflow = skill.get("workflow") or {}
    text = str(workflow.get("instructions") or "").strip()
    if text:
        return text
    steps = workflow.get("checklist") or workflow.get("steps") or []
    lines = [str(step.get("title") if isinstance(step, dict) else step) for step in steps]
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1) if line) or str(skill.get("description") or "")


def skill_block(skill_ids: list[int]) -> tuple[str, list[str]]:
    """Блок инструкций выбранных навыков для начала пользовательского сообщения и список их названий.

    Это указание пользователя, а не системное: оно действует в рамках прав приложения и не отменяет правил безопасности.
    Блок стоит в самом сообщении, поэтому история до него не меняется и кэш префикса сохраняется.
    """
    if not skill_ids:
        return "", []
    if len(skill_ids) > MAX_SELECTED:
        raise SkillError(f"Можно подключить не больше {MAX_SELECTED} навыков")
    by_id = {item["id"]: item for item in list_skills()}
    parts, names = [], []
    for skill_id in dict.fromkeys(skill_ids):
        skill = by_id.get(skill_id)
        if not skill:
            raise SkillError("Выбранный навык не найден")
        names.append(skill["name"])
        parts.append(f'<skill name="{skill["name"]}">\n{skill_instruction(skill)}\n</skill>')
    head = (
        "Пользователь подключил навыки к этому запросу. Это его инструкции: следуй им, пока они не противоречат "
        "правилам безопасности и правам приложения."
    )
    return head + "\n\n" + "\n\n".join(parts), names


def apply_skill(skill_id: int, task: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "skill not found"}
    skill = _skill_dict(row)
    workflow = skill["workflow"]
    raw_steps = workflow.get("checklist") or workflow.get("steps") or []
    checklist = [
        step if isinstance(step, dict) else {"title": str(step), "done": False}
        for step in raw_steps
    ]
    from koddi.agent_runtime import create_task, set_checklist

    created = create_task(title=task or skill["name"], mode=str(workflow.get("mode") or "build"))
    if checklist:
        created = set_checklist(created["id"], checklist)
    return {"ok": True, "skill": skill, "task": created}


def _skill_dict(row) -> dict:
    item = dict(row)
    item["workflow"] = json.loads(item.pop("workflow_json") or "{}")
    item["builtin"] = bool(item["workflow"].get("builtin"))
    item["category"] = str(item["workflow"].get("category") or ("Мои навыки" if not item["builtin"] else "Прочее"))
    return item
