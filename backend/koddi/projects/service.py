from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from koddi.storage.db import db


HIDDEN_SUBPROCESS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
HOME_NAME = "Докки"


def home_directory() -> Path:
    """Папка чатов без проекта: в документах, рядом с проектами, на macOS и Windows одинаково."""
    return Path.home() / "Documents" / HOME_NAME


def home_project() -> dict:
    """Скрытый проект чатов без проекта. Через него у такого чата те же разрешения, журнал изменений и откат,
    что у проекта; файлы вне папки агент открывает по абсолютному пути через режим разрешений."""
    root = home_directory()
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    with db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE path=?", (str(root),)).fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO projects(name, path, hidden) VALUES (?, ?, 1) RETURNING *", (HOME_NAME, str(root))
            ).fetchone()
    return dict(row)


def is_home(workspace: Path | None) -> bool:
    if workspace is None:
        return False
    try:
        return workspace.resolve() == home_directory().resolve()
    except OSError:
        return False


def add_project(path: str, name: str | None = None) -> dict:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    project_name = name or root.name or "Workspace"
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO projects(name, path, last_opened_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET name=excluded.name, last_opened_at=CURRENT_TIMESTAMP, hidden=0
            RETURNING *
            """,
            (project_name, str(root)),
        )
        return dict(cur.fetchone())


def list_projects(*, include_status: bool = True) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE hidden=0 ORDER BY pinned DESC, last_opened_at DESC"
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
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=HIDDEN_SUBPROCESS,
        )
        dirty = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "status", "--porcelain"],
            cwd=path,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    """Идемпотентно: повтор с тем же путём и веткой (например, после сбоя) возвращает существующий worktree,
    а запись в базе сверяется с git."""
    from koddi.projects.gitsafe import ensure_worktree

    project = get_project(project_id)
    if not project:
        return {"ok": False, "error": "project not found"}
    root = Path(project["path"])
    target = Path(path).expanduser().resolve()
    if not (root / ".git").exists():
        return {"ok": False, "error": "project is not a Git repository"}
    created = ensure_worktree(root, target, branch)
    if not created["ok"]:
        return created
    with db() as conn:
        conn.execute(
            "INSERT INTO worktrees(project_id, name, path, branch, task_id) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET branch=excluded.branch, status='active'",
            (project_id, target.name, created["path"], created["branch"], task_id),
        )
        row = conn.execute("SELECT * FROM worktrees WHERE path=?", (created["path"],)).fetchone()
    return {"ok": True, "worktree": dict(row), "reused": created["reused"]}


def remove_worktree(worktree_id: int, force: bool = False) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM worktrees WHERE id=?", (worktree_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "worktree not found"}
    item = dict(row)
    project = get_project(item["project_id"])
    if not project:
        return {"ok": False, "error": "parent project not found"}
    from koddi.projects.gitsafe import remove_worktree as safe_remove

    result = safe_remove(Path(project["path"]), Path(item["path"]), force=force)
    if not result["ok"]:
        return {"ok": False, "error": result.get("error") or result.get("stderr", "").strip() or result.get("stdout", "").strip()}
    with db() as conn:
        conn.execute("DELETE FROM worktrees WHERE id=?", (worktree_id,))
    return {"ok": True, "id": worktree_id}
