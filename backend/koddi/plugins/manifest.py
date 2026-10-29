"""Состав плагина Claude Code или Codex: навыки, команды, агенты, хуки и MCP-серверы.

Читаются `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` (или общий `plugin.json`) и стандартные папки
`skills/`, `commands/`, `agents/`, `hooks/hooks.json`, `.mcp.json`. Пути из манифеста - только внутри плагина.
Здесь ничего не исполняется: хуки и MCP только описываются, запуск - после решения человека.
"""

from __future__ import annotations

import json
import re

from koddi.plugins.github import PluginSourceError, clean_path
from koddi.skills.project import frontmatter, parse_skill

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_MANIFEST = 256 * 1024
MAX_SKILL_BYTES = 256 * 1024
MAX_ITEMS = 200
HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop", "SessionEnd",
               "Notification", "PreCompact"}
# события, которые Докки умеет исполнять после подтверждения человека; остальные только показываются
SUPPORTED_HOOKS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}


def hook_supported(event: str, kind: str, command: str) -> bool:
    return event in SUPPORTED_HOOKS and kind == "command" and bool(command)


def _json(files: dict[str, bytes], path: str) -> dict | None:
    data = files.get(path)
    if data is None:
        return None
    if len(data) > MAX_MANIFEST:
        raise PluginSourceError(f"{path} слишком большой")
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PluginSourceError(f"{path}: неверный JSON") from exc
    if not isinstance(value, dict):
        raise PluginSourceError(f"{path}: ожидается объект")
    return value


def _rel(value: object) -> str:
    """Путь из манифеста (`./skills/`) - относительно корня плагина и без выхода из него."""
    if not isinstance(value, str) or not value.strip():
        raise PluginSourceError("Путь в манифесте плагина должен быть строкой")
    text = value.strip()
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", ".")
    if text.startswith("/") or "://" in text or "\\" in text:
        raise PluginSourceError("Путь в манифесте плагина должен быть относительным")
    return clean_path(re.sub(r"^(\./)+", "", text))


def _paths(value: object, named: dict[str, str] | None = None) -> list[str]:
    """Пути компонента: строка, список или объект `{"имя": {"source": "./файл.md"}}` (имя запоминается в `named`)."""
    if value is None:
        return []
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            source = item.get("source") if isinstance(item, dict) else item
            # запись с текстом прямо в манифесте (`content`) без файла пропускаем: файлом её не проверить
            if source is None and isinstance(item, dict):
                continue
            path = _rel(source)
            if named is not None:
                named[path] = str(key)
            result.append(path)
        items = result
    else:
        items = [_rel(item) for item in (value if isinstance(value, list) else [value])]
    if len(items) > MAX_ITEMS:
        raise PluginSourceError("Слишком много путей в манифесте")
    return items


def _text(data: bytes) -> str:
    return data[:MAX_SKILL_BYTES].decode("utf-8", errors="replace")


def _under(path: str, base: str) -> str | None:
    if not base:
        return path
    return path[len(base) + 1:] if path.startswith(base + "/") else None


def _entry(path: str, base: str) -> str | None:
    """Путь файла относительно папки компонента; манифест может указать и один файл `.md`."""
    if base.endswith(".md"):
        return path.rpartition("/")[2] if path == base else None
    return _under(path, base)


def _skills(files: dict[str, bytes], bases: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for base in bases:
        for path in sorted(files):
            rest = _under(path, base)
            if rest is None:
                continue
            parts = rest.split("/")
            # skills/<имя>/SKILL.md, или путь в манифесте указывает прямо на папку навыка
            if parts == ["SKILL.md"] or (len(parts) == 2 and parts[1] == "SKILL.md"):
                folder = path.rpartition("/")[0]
                meta, _ = frontmatter(_text(files[path]))
                parsed = parse_skill(_text(files[path]), folder.rpartition("/")[2] or "skill")
                key = _slug(parsed["name"])
                if key and key not in found:
                    found[key] = {"name": key, "description": parsed["description"], "path": path, "dir": folder,
                                  "model_invocation": meta.get("disable-model-invocation") is not True,
                                  "allowed_tools": _list(meta.get("allowed-tools"))}
            if len(found) >= MAX_ITEMS:
                raise PluginSourceError("В плагине слишком много навыков")
    return list(found.values())


def _commands(files: dict[str, bytes], bases: list[str], named: dict[str, str] | None = None) -> list[dict]:
    found: dict[str, dict] = {}
    named = named or {}
    for base in bases:
        for path in sorted(files):
            rest = _entry(path, base)
            if rest is None or not rest.endswith(".md") or rest.count("/") > 2:
                continue
            meta, body = frontmatter(_text(files[path]))
            # подпапки команд в Claude Code становятся пространством имён: frontend/build.md -> frontend:build
            key = _slug(named.get(path) or rest[:-3].replace("/", ":"), allow_colon=True)
            if not key or key in found or not body.strip():
                continue
            description = re.sub(r"\s+", " ", str(meta.get("description") or "")).strip()[:500]
            if not description:
                description = next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")[:300]
            found[key] = {"name": key, "description": description, "path": path,
                          "argument_hint": str(meta.get("argument-hint") or "")[:200],
                          "model_invocation": meta.get("disable-model-invocation") is not True,
                          "allowed_tools": _list(meta.get("allowed-tools"))}
            if len(found) >= MAX_ITEMS:
                raise PluginSourceError("В плагине слишком много команд")
    return list(found.values())


def _agents(files: dict[str, bytes], bases: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for base in bases:
        for path in sorted(files):
            rest = _entry(path, base)
            if rest is None or "/" in rest or not rest.endswith(".md"):
                continue
            meta, _ = frontmatter(_text(files[path]))
            key = _slug(str(meta.get("name") or rest[:-3]))
            if key and key not in found:
                found[key] = {"name": key, "description": re.sub(r"\s+", " ", str(meta.get("description") or "")).strip()[:300],
                              "path": path, "tools": _list(meta.get("tools"))}
    return list(found.values())[:MAX_ITEMS]


def _hooks(config: object, source: str) -> list[dict]:
    if not isinstance(config, dict):
        return []
    events = config.get("hooks", config)
    if not isinstance(events, dict):
        raise PluginSourceError(f"{source}: hooks должен быть объектом")
    result = []
    for event, groups in events.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                kind = str(hook.get("type") or "command")
                command = str(hook.get("command") or hook.get("prompt") or "")[:4000]
                timeout = hook.get("timeout")
                result.append({
                    "event": str(event)[:60], "matcher": str(group.get("matcher") or "")[:200], "type": kind,
                    "command": command, "timeout": int(timeout) if isinstance(timeout, (int, float)) and 0 < timeout <= 600 else 60,
                    "supported": hook_supported(event, kind, command), "source": source,
                })
                if len(result) > MAX_ITEMS:
                    raise PluginSourceError("В плагине слишком много хуков")
    return result


def _mcp(config: object, source: str) -> dict[str, dict]:
    if not isinstance(config, dict):
        return {}
    servers = config.get("mcpServers", config)
    if not isinstance(servers, dict):
        raise PluginSourceError(f"{source}: mcpServers должен быть объектом")
    result = {}
    for name, server in list(servers.items())[:50]:
        if not isinstance(server, dict) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", str(name)):
            continue
        clean = {key: server[key] for key in ("command", "args", "env", "cwd", "type", "transport", "url", "headers", "timeout_seconds")
                 if key in server}
        result[str(name)] = clean
    return result


def _list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item)[:100] for item in value][:50]
    if isinstance(value, str) and value.strip():
        return [item.strip()[:100] for item in value.split(",") if item.strip()][:50]
    return []


def _slug(value: str, *, allow_colon: bool = False) -> str:
    allowed = r"[^a-z0-9._:-]+" if allow_colon else r"[^a-z0-9._-]+"
    return re.sub(allowed, "-", value.strip().lower()).strip("-.:")[:64]


def read(files: dict[str, bytes], *, fallback_name: str) -> dict:
    """Описание плагина по его файлам. Пустой плагин (нет навыков, команд, агентов, хуков и MCP) - ошибка."""
    claude = _json(files, ".claude-plugin/plugin.json")
    codex = _json(files, ".codex-plugin/plugin.json")
    shared = _json(files, "plugin.json")
    manifest = claude or codex or shared or {}
    formats = [name for name, value in (("claude", claude), ("codex", codex)) if value is not None]
    if not formats:
        formats = ["skills"]
    raw_name = str(manifest.get("name") or (codex or {}).get("name") or fallback_name)
    name = _slug(raw_name)
    if not NAME_RE.match(name or ""):
        raise PluginSourceError("Имя плагина: латиница в нижнем регистре, цифры, точка, дефис")
    interface = (codex or {}).get("interface") if isinstance((codex or {}).get("interface"), dict) else {}
    title = str(manifest.get("displayName") or interface.get("displayName") or raw_name)[:120]
    description = re.sub(r"\s+", " ", str(manifest.get("description") or interface.get("shortDescription") or "")).strip()[:500]

    named: dict[str, str] = {}

    def components(key: str, default: str) -> list[str]:
        declared = []
        for item in (claude, codex):
            if item is not None:
                declared += _paths(item.get(key), named if key == "commands" else None)
        # пути манифеста дополняют стандартную папку, как в Claude Code
        return list(dict.fromkeys(declared + ([default] if any(_under(path, default) is not None for path in files) else [])))

    skills = _skills(files, components("skills", "skills"))
    commands = _commands(files, components("commands", "commands"), named)
    agents = _agents(files, components("agents", "agents"))

    hooks: list[dict] = []
    hook_sources = []
    for item in (claude, codex):
        if item is None or item.get("hooks") is None:
            continue
        value = item["hooks"]
        if isinstance(value, dict):
            hooks += _hooks(value, "plugin.json")
        else:
            hook_sources += _paths(value)
    if "hooks/hooks.json" in files:
        hook_sources.append("hooks/hooks.json")
    for path in dict.fromkeys(hook_sources):
        config = _json(files, path)
        if config is None:
            raise PluginSourceError(f"Файл хуков {path} не найден в плагине")
        hooks += _hooks(config, path)

    mcp: dict[str, dict] = {}
    mcp_sources = []
    for item in (claude, codex):
        if item is None or item.get("mcpServers") is None:
            continue
        value = item["mcpServers"]
        if isinstance(value, dict):
            mcp.update(_mcp(value, "plugin.json"))
        else:
            mcp_sources += _paths(value)
    if ".mcp.json" in files:
        mcp_sources.append(".mcp.json")
    for path in dict.fromkeys(mcp_sources):
        config = _json(files, path)
        if config is None:
            raise PluginSourceError(f"Файл MCP {path} не найден в плагине")
        mcp.update(_mcp(config, path))

    if not (skills or commands or agents or hooks or mcp):
        raise PluginSourceError("В папке нет плагина: не найдены навыки, команды, агенты, хуки или MCP")
    author = manifest.get("author")
    return {
        "name": name, "title": title, "description": description, "version": str(manifest.get("version") or "")[:40],
        "formats": formats, "license": str(manifest.get("license") or "")[:60],
        "author": str(author.get("name") if isinstance(author, dict) else author or "")[:120],
        "homepage": str(manifest.get("homepage") or "")[:300],
        "skills": skills, "commands": commands, "agents": agents, "hooks": hooks, "mcp": mcp,
    }


def marketplace(files: dict[str, bytes]) -> list[dict]:
    """Плагины из `.claude-plugin/marketplace.json` или `.agents/plugins/marketplace.json` с путём внутри репозитория."""
    result = []
    for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
        try:
            config = _json(files, path)
        except PluginSourceError:
            continue
        for item in (config or {}).get("plugins") or []:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            if isinstance(source, dict):
                source = source.get("path")
            if not isinstance(source, str):
                continue
            try:
                folder = _rel(source) if source.strip() not in {".", "./"} else ""
            except PluginSourceError:
                continue
            entry = {"name": _slug(str(item.get("name") or folder)), "path": folder,
                     "description": re.sub(r"\s+", " ", str(item.get("description") or "")).strip()[:300]}
            if entry["name"] and entry not in result:
                result.append(entry)
    return result[:MAX_ITEMS]
