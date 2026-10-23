from __future__ import annotations

import json
from typing import Any

from koddi.storage.db import db


VALID_ROLES = {"parent", "researcher", "coder", "tester", "reviewer", "safety"}


def create_task(
    *,
    title: str,
    project_id: int | None = None,
    chat_id: int | None = None,
    mode: str = "build",
    role: str = "parent",
    parent_task_id: int | None = None,
) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"Unsupported agent role: {role}")
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks(project_id, chat_id, parent_task_id, title, mode, role, status)
            VALUES (?, ?, ?, ?, ?, ?, 'queued')
            """,
            (project_id, chat_id, parent_task_id, title, mode, role),
        )
        task_id = int(cur.lastrowid)
        if parent_task_id:
            conn.execute(
                "INSERT INTO task_edges(parent_task_id, child_task_id) VALUES (?, ?)",
                (parent_task_id, task_id),
            )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _task_dict(row)


def update_task_status(task_id: int, status: str, progress: dict[str, Any] | None = None) -> dict:
    with db() as conn:
        if progress is None:
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status=?, progress_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, json.dumps(progress, ensure_ascii=False), task_id),
            )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _task_dict(row)


def set_checklist(task_id: int, checklist: list[dict[str, Any]]) -> dict:
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET checklist_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(checklist, ensure_ascii=False), task_id),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _task_dict(row)


def list_tasks(project_id: int | None = None) -> list[dict]:
    with db() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY id DESC", (project_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 300").fetchall()
    return [_task_dict(row) for row in rows]


def task_graph(project_id: int | None = None) -> dict:
    tasks = list_tasks(project_id)
    with db() as conn:
        edges = conn.execute("SELECT * FROM task_edges").fetchall()
    allowed_ids = {task["id"] for task in tasks}
    return {
        "nodes": tasks,
        "edges": [
            dict(edge)
            for edge in edges
            if edge["parent_task_id"] in allowed_ids or edge["child_task_id"] in allowed_ids
        ],
    }


def spawn_child(parent_task_id: int, title: str, role: str) -> dict:
    with db() as conn:
        parent = conn.execute("SELECT * FROM tasks WHERE id=?", (parent_task_id,)).fetchone()
    if not parent:
        raise ValueError("parent task not found")
    return create_task(
        title=title,
        project_id=parent["project_id"],
        chat_id=parent["chat_id"],
        mode=parent["mode"],
        role=role,
        parent_task_id=parent_task_id,
    )


def _task_dict(row: Any) -> dict:
    item = dict(row)
    item["progress"] = json.loads(item.pop("progress_json") or "{}")
    item["checklist"] = json.loads(item.pop("checklist_json") or "[]")
    item["result"] = json.loads(item.pop("result_json") or "{}")
    return item
