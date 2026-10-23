from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from koddi.storage.db import db


HIDDEN_SUBPROCESS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def add_project(path: str, name: str | None = None) -> dict:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    project_name = name or root.name or "Workspace"
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO projects(name, path, last_opened_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET name=excluded.name, last_opened_at=CURRENT_TIMESTAMP
            RETURNING *
            """,
            (project_name, str(root)),
        )
        return dict(cur.fetchone())


def list_projects(*, include_status: bool = True) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY pinned DESC, last_opened_at DESC"
        ).fetchall()
    projects: list[dict] = []
    for row in rows:
        item = dict(row)
        item.update(
            project_status(Path(item["path"]))
            if include_status
            else {"exists": Path(item["path"]).is_dir()}
        )
        projects.append(item)
    return projects


def get_project(project_id: int | None) -> dict | None:
    if project_id is None:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row else None


def project_status(path: Path) -> dict:
    status = {"git_branch": None, "dirty_files": 0, "exists": path.exists()}
    if not (path / ".git").exists():
        return status
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=HIDDEN_SUBPROCESS,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=HIDDEN_SUBPROCESS,
        )
        status["git_branch"] = branch.stdout.strip() if branch.returncode == 0 else None
        status["dirty_files"] = len([line for line in dirty.stdout.splitlines() if line.strip()])
    except Exception:
        pass
    return status


def create_worktree(project_id: int, path: str, branch: str, task_id: int | None = None) -> dict:
    project = get_project(project_id)
    if not project:
        return {"ok": False, "error": "project not found"}
    root = Path(project["path"])
    target = Path(path).expanduser().resolve()
    if not (root / ".git").exists():
        return {"ok": False, "error": "project is not a Git repository"}
    completed = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(target)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
        creationflags=HIDDEN_SUBPROCESS,
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr.strip() or completed.stdout.strip()}
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO worktrees(project_id, name, path, branch, task_id) VALUES (?, ?, ?, ?, ?)",
            (project_id, target.name, str(target), branch, task_id),
        )
        row = conn.execute("SELECT * FROM worktrees WHERE id=?", (cursor.lastrowid,)).fetchone()
    return {"ok": True, "worktree": dict(row)}


def remove_worktree(worktree_id: int, force: bool = False) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM worktrees WHERE id=?", (worktree_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "worktree not found"}
    item = dict(row)
    project = get_project(item["project_id"])
    if not project:
        return {"ok": False, "error": "parent project not found"}
    command = ["git", "worktree", "remove"] + (["--force"] if force else []) + [item["path"]]
    completed = subprocess.run(
        command,
        cwd=project["path"],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
        creationflags=HIDDEN_SUBPROCESS,
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr.strip() or completed.stdout.strip()}
    with db() as conn:
        conn.execute("DELETE FROM worktrees WHERE id=?", (worktree_id,))
    return {"ok": True, "id": worktree_id}
