"""Файлы проекта для агента в формате Claude Code: навыки (`*/SKILL.md` в `.dokky/skills`, `.claude/skills`,
`.agents/skills`), команды (`.dokky/commands`, `.claude/commands`), агенты (`.dokky/agents`, `.claude/agents`)
и хуки (`hooks` в `.dokky/settings.json`, `.claude/settings.json`, `.claude/settings.local.json`).

Чужой файл в репозитории - непроверенная инструкция или команда: он действует только после того, как человек
подтвердил именно эту версию файла (по sha256). Любое изменение файла снова требует подтверждения.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path

from koddi.policy.paths import WorkspacePaths
from koddi.storage.db import db

ROOTS = (".dokky/skills", ".claude/skills", ".agents/skills")
COMMAND_ROOTS = (".dokky/commands", ".claude/commands")
AGENT_ROOTS = (".dokky/agents", ".claude/agents")
HOOK_FILES = (".dokky/settings.json", ".claude/settings.json", ".claude/settings.local.json")
KINDS = ("skill", "command", "agent", "hooks")
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


def kind_of(path: str) -> str:
    """Вид файла по его месту в проекте: путь в таблице доверия один на все виды."""
    if path.endswith("/SKILL.md"):
        return "skill"
    if path in HOOK_FILES:
        return "hooks"
    if path.startswith(AGENT_ROOTS):
        return "agent"
    return "command"


def _name(value: object, fallback: str) -> str:
    return re.sub(r"\s+", " ", str(value or "") or fallback).strip()[:80]


def _hooks_text(hooks: list[dict]) -> str:
    lines = []
    for hook in hooks:
        matcher = f" [{hook['matcher']}]" if hook["matcher"] else ""
        lines.append(f"{hook['event']}{matcher}: {hook['command']}")
    return "\n".join(lines)


def parse_hooks(text: str) -> list[dict]:
    """Хуки из настроек проекта в формате Claude Code; неисполняемые Докки (не command, другие события) отбрасываются."""
    from koddi.plugins.manifest import PluginSourceError, _hooks

    try:
        data = json.loads(text)
        hooks = _hooks(data.get("hooks") if isinstance(data, dict) else None, "settings")
    except (ValueError, PluginSourceError):
        return []
    return [hook for hook in hooks if hook["supported"]]


def parse_file(kind: str, text: str, path: str) -> dict:
    """Имя, описание и текст файла проекта; у агента ещё инструменты, у хуков - список команд."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    if kind == "skill":
        return parse_skill(text, path.rsplit("/", 2)[-2])
    if kind == "hooks":
        hooks = parse_hooks(text)
        events = sorted({hook["event"] for hook in hooks})
        return {"name": f"Хуки {path}", "description": f"{len(hooks)} шт.: {', '.join(events)}" if hooks else "",
                "instructions": _hooks_text(hooks), "version": "", "hooks": hooks}
    meta, body = frontmatter(text)
    description = re.sub(r"\s+", " ", str(meta.get("description") or "")).strip()[:500]
    if not description:
        description = next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")[:300]
    item = {"name": _name(meta.get("name") if kind == "agent" else None, stem), "description": description,
            "instructions": body.strip(), "version": ""}
    if kind == "agent":
        tools = meta.get("tools")
        item["tools"] = [str(tool).strip() for tool in (tools if isinstance(tools, list) else str(tools or "").split(","))
                         if str(tool).strip()]
    else:
        item["argument_hint"] = str(meta.get("argument-hint") or "")[:200]
    return item


def _scripts(root: Path, hooks: list[dict]) -> list[Path]:
    """Файлы проекта, которые запускают команды хуков (`bash .claude/hooks/check.sh`): они входят в доверие."""
    found: dict[str, Path] = {}
    base = root.resolve()
    for hook in hooks:
        command = hook["command"].replace("${CLAUDE_PROJECT_DIR}", str(base)).replace("$CLAUDE_PROJECT_DIR", str(base))
        try:
            words = shlex.split(command)
        except ValueError:
            words = command.split()
        for word in words:
            if not word or word.startswith("-") or ("/" not in word and "\\" not in word and "." not in word):
                continue
            candidate = Path(word) if Path(word).is_absolute() else base / word
            try:
                resolved = candidate.resolve()
                resolved.relative_to(base)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                found[resolved.as_posix()] = resolved
    return [found[key] for key in sorted(found)][:20]


def digest(kind: str, data: bytes, root: Path, parsed: dict) -> str:
    """Отпечаток версии: у хуков - файл настроек и скрипты проекта, которые он запускает."""
    if kind != "hooks":
        return hashlib.sha256(data).hexdigest()
    total = hashlib.sha256(data)
    base = root.resolve()
    for script in _scripts(root, parsed.get("hooks") or []):
        try:
            content = script.read_bytes() if script.stat().st_size <= MAX_BYTES else b"<too large>"
        except OSError:
            content = b"<unreadable>"
        total.update(b"\0" + script.relative_to(base).as_posix().encode() + b"\0" + hashlib.sha256(content).digest())
    return total.hexdigest()


def _candidates(root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for base in ROOTS:
        found += [("skill", path) for path in sorted((root / base).glob("*/SKILL.md"))[:MAX_SKILLS]]
    for base in COMMAND_ROOTS:
        # подпапки команд, как в Claude Code, только для порядка: имя команды - имя файла
        found += [("command", path) for path in sorted([*(root / base).glob("*.md"), *(root / base).glob("*/*.md")])[:MAX_SKILLS]]
    for base in AGENT_ROOTS:
        found += [("agent", path) for path in sorted((root / base).glob("*.md"))[:MAX_SKILLS]]
    found += [("hooks", root / name) for name in HOOK_FILES]
    return found


def discover(root: Path) -> list[dict]:
    access = WorkspacePaths(root)
    found = []
    counts = dict.fromkeys(KINDS, 0)
    for kind, path in _candidates(root):
        if counts[kind] >= MAX_SKILLS:
            continue
        try:
            if not path.is_file() or path.is_symlink() or not access.accessible(path) or path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        parsed = parse_file(kind, data.decode("utf-8", errors="replace"), relative)
        if kind == "hooks" and not parsed["hooks"]:
            continue  # настройки без хуков доверия не требуют
        counts[kind] += 1
        if kind == "hooks":
            # человек видит, какие файлы проекта войдут в доверие вместе с настройками
            scripts = [path.relative_to(root.resolve()).as_posix() for path in _scripts(root, parsed["hooks"])]
            if scripts:
                parsed["instructions"] += "\n\nСкрипты проекта, которые они запускают: " + ", ".join(scripts)
        found.append({"path": relative, "kind": kind, "sha256": digest(kind, data, root, parsed), **parsed})
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
    return [{key: item[key] for key in ("path", "kind", "name", "description", "sha256", "status", "version", "instructions")}
            for item in current]


def decide(project_id: int, owner: str, root: Path, path: str, sha256: str, trust: bool) -> dict:
    """Решение человека по конкретной версии файла: при несовпадении хеша файл успел измениться."""
    listed = {item["path"]: item for item in sync(project_id, owner, root)}
    item = listed.get(path)
    if item is None:
        raise ProjectSkillError("Файл не найден в папке проекта")
    if item["sha256"] != sha256:
        raise ProjectSkillError("Файл изменился: просмотрите его снова")
    with db() as conn:
        conn.execute(
            "UPDATE project_skills SET status=?, trusted_sha256=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE project_id=? AND owner_id=? AND path=?",
            ("trusted" if trust else "rejected", sha256 if trust else None, project_id, owner, path),
        )
    return {**item, "status": "trusted" if trust else "rejected"}


def trusted(project_id: int | None, owner: str, root: Path | None, kind: str = "skill") -> list[dict]:
    """Подтверждённые файлы одного вида с текстом; файл перечитывается и сверяется с подтверждённым хешем."""
    if project_id is None or root is None:
        return []
    with db() as conn:
        rows = conn.execute(
            "SELECT path,trusted_sha256 FROM project_skills WHERE project_id=? AND owner_id=? AND status='trusted'",
            (project_id, owner)).fetchall()
    result = []
    access = WorkspacePaths(root)
    for row in rows:
        if kind_of(row["path"]) != kind:
            continue
        path = root / row["path"]
        try:
            if path.is_symlink() or not access.accessible(path) or path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        parsed = parse_file(kind, data.decode("utf-8", errors="replace"), row["path"])
        if digest(kind, data, root, parsed) != row["trusted_sha256"]:
            continue  # файл или скрипт хука изменили после подтверждения: до нового решения человека не используется
        result.append({"path": row["path"], "source": "project", "sha256": row["trusted_sha256"], **parsed})
    return result
