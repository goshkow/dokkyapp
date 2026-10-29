"""Навыки проекта из файлов SKILL.md (`.dokky/skills`, `.claude/skills`, `.agents/skills`).

Чужой файл в репозитории - непроверенная инструкция: навык виден агенту только после того, как человек
подтвердил именно эту версию файла (по sha256). Любое изменение файла снова требует подтверждения.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from koddi.policy.paths import WorkspacePaths
from koddi.storage.db import db

ROOTS = (".dokky/skills", ".claude/skills", ".agents/skills")
MAX_SKILLS = 50
MAX_BYTES = 64 * 1024
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


class ProjectSkillError(ValueError):
    pass


def _scalar(value: str) -> object:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [str(_scalar(item)) for item in value[1:-1].split(",") if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Плоские поля YAML-фронтматтера: строки, кавычки, блоки `>`/`|`, списки `[a, b]` и `- a`, true/false.

    Вложенные объекты пропускаются: из чужого файла берём только простые поля, YAML не исполняется.
    """
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, object] = {}
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line[0] in " \t#" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        block = []
        while index < len(lines) and (not lines[index].strip() or lines[index][0] in " \t"):
            block.append(lines[index].strip())
            index += 1
        if value[:1] in {">", "|"}:
            meta[key] = (" " if value[0] == ">" else "\n").join(block).strip()
        elif not value and block and all(item.startswith("- ") or not item for item in block):
            meta[key] = [str(_scalar(item[2:])) for item in block if item]
        elif value and not value.startswith("#"):
            meta[key] = _scalar(value)
    return meta, match.group(2)


def parse_skill(text: str, fallback_name: str) -> dict:
    """Имя, описание и текст навыка из SKILL.md (фронтматтер YAML с name и description необязателен)."""
    meta, body = frontmatter(text)
    name = re.sub(r"\s+", " ", str(meta.get("name") or "") or fallback_name).strip()[:80]
    description = re.sub(r"\s+", " ", str(meta.get("description") or "")).strip()[:500]
    if not description:
        first = next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")
        description = first[:300]
    return {"name": name, "description": description, "instructions": body.strip(), "version": str(meta.get("version") or "")}


def discover(root: Path) -> list[dict]:
    access = WorkspacePaths(root)
    found = []
    for base in ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*/SKILL.md"))[:MAX_SKILLS]:
            try:
                if path.is_symlink() or not access.accessible(path) or path.stat().st_size > MAX_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            parsed = parse_skill(data.decode("utf-8", errors="replace"), path.parent.name)
            found.append({"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), **parsed})
            if len(found) >= MAX_SKILLS:
                return found
    return found


def sync(project_id: int, owner: str, root: Path) -> list[dict]:
    """Список навыков проекта с состоянием доверия; новый или изменённый файл - pending."""
    current = discover(root)
    with db() as conn:
        stored = {row["path"]: dict(row) for row in conn.execute(
            "SELECT * FROM project_skills WHERE project_id=? AND owner_id=?", (project_id, owner))}
        for item in current:
            previous = stored.get(item["path"])
            if previous and previous["status"] == "rejected" and previous["sha256"] == item["sha256"]:
                status = "rejected"
            elif previous and previous.get("trusted_sha256") == item["sha256"]:
                status = "trusted"
            else:
                status = "pending"
            conn.execute(
                "INSERT INTO project_skills(project_id,owner_id,path,name,description,sha256,status,trusted_sha256) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,owner_id,path) DO UPDATE SET name=excluded.name,"
                "description=excluded.description,sha256=excluded.sha256,status=excluded.status,updated_at=CURRENT_TIMESTAMP",
                (project_id, owner, item["path"], item["name"], item["description"], item["sha256"], status,
                 previous.get("trusted_sha256") if previous else None),
            )
            item["status"] = status
        gone = set(stored) - {item["path"] for item in current}
        conn.executemany("DELETE FROM project_skills WHERE project_id=? AND owner_id=? AND path=?",
                         [(project_id, owner, path) for path in gone])
    # текст нужен человеку, чтобы решить, доверять ли этой версии файла
    return [{key: item[key] for key in ("path", "name", "description", "sha256", "status", "version", "instructions")}
            for item in current]


def decide(project_id: int, owner: str, root: Path, path: str, sha256: str, trust: bool) -> dict:
    """Решение человека по конкретной версии файла: при несовпадении хеша файл успел измениться."""
    listed = {item["path"]: item for item in sync(project_id, owner, root)}
    item = listed.get(path)
    if item is None:
        raise ProjectSkillError("Навык не найден в папке проекта")
    if item["sha256"] != sha256:
        raise ProjectSkillError("Файл навыка изменился: просмотрите его снова")
    with db() as conn:
        conn.execute(
            "UPDATE project_skills SET status=?, trusted_sha256=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE project_id=? AND owner_id=? AND path=?",
            ("trusted" if trust else "rejected", sha256 if trust else None, project_id, owner, path),
        )
    return {**item, "status": "trusted" if trust else "rejected"}


def trusted(project_id: int | None, owner: str, root: Path | None) -> list[dict]:
    """Подтверждённые навыки с текстом; файл перечитывается и сверяется с подтверждённым хешем."""
    if project_id is None or root is None:
        return []
    with db() as conn:
        rows = conn.execute(
            "SELECT path,trusted_sha256 FROM project_skills WHERE project_id=? AND owner_id=? AND status='trusted'",
            (project_id, owner)).fetchall()
    result = []
    access = WorkspacePaths(root)
    for row in rows:
        path = root / row["path"]
        try:
            if path.is_symlink() or not access.accessible(path) or path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() != row["trusted_sha256"]:
            continue  # файл изменили после подтверждения: до нового решения человека не используется
        result.append({"path": row["path"], "source": "project", **parse_skill(data.decode("utf-8", errors="replace"), path.parent.name)})
    return result
