"""Хуки плагинов, которые человек разрешил для этой версии: `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PostToolUse` и `Stop`, с тем же протоколом, что в Claude Code (JSON на stdin, код выхода 2 или JSON-решение).

Хук может только добавить контекст или запретить действие: разрешить то, что политика Докки не пустила, он не может.
Неразрешённый или изменённый хук не запускается. В журнал пишется только факт запуска и код выхода.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from koddi.plugins import store

MAX_OUTPUT = 8000
MAX_TIMEOUT = 60
# хуку достаётся только то, что нужно для запуска программ: ни токенов, ни настроек Докки
SAFE_ENV = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TMPDIR", "TMP", "TEMP", "TERM", "TZ",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "APPDATA", "LOCALAPPDATA", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "OS", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
}


def _env(root: Path, workspace: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV or key.startswith("LC_")}
    env.update({"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PROJECT_DIR": str(workspace), "DOKKY_PLUGIN_ROOT": str(root)})
    return env


def _matches(matcher: str, value: str) -> bool:
    if not matcher or matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, value) is not None
    except re.error:
        return matcher == value


def _context(stdout: str) -> str:
    text = stdout.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict):
            specific = data.get("hookSpecificOutput")
            if isinstance(specific, dict) and isinstance(specific.get("additionalContext"), str):
                return specific["additionalContext"].strip()
            return str(data.get("systemMessage") or "").strip()
    return text


def _quote_states(text: str) -> str:
    """Состояние кавычек в конце текста по правилам оболочки: "" - вне кавычек, "'" или '"' - внутри."""
    state, index = "", 0
    while index < len(text):
        char = text[index]
        if state == "'":
            # в PowerShell '' внутри одинарных кавычек - экранированная кавычка, состояние не меняется
            if char == "'":
                state = ""
        elif state == '"':
            if char == "\\" and os.name != "nt":
                index += 1
            elif char == "`" and os.name == "nt":
                index += 1
            elif char == '"':
                state = ""
        elif char == "\\" and os.name != "nt":
            index += 1
        elif char in "'\"":
            state = char
        index += 1
    return state


def expand_root(command: str, root: Path) -> str:
    """`${CLAUDE_PLUGIN_ROOT}` -> путь плагина с учётом кавычек вокруг: в папке данных macOS есть пробел,
    а путь не должен ни разбиться на слова, ни закрыть кавычку команды."""
    marker = "${CLAUDE_PLUGIN_ROOT}"
    raw = str(root)
    parts = command.split(marker)
    result = parts[0]
    for part in parts[1:]:
        state = _quote_states(result)
        if os.name == "nt":
            value = {"'": raw.replace("'", "''"), '"': re.sub(r'([`$"])', r"`\1", raw)}.get(state)
            if value is None:
                # в PowerShell 'путь'/a.js - это два аргумента: в кавычки берём весь токен до пробела
                token = re.match(r"[^\s;|&<>(){}'\"`,]*", part).group(0)
                value, part = "'" + (raw + token).replace("'", "''") + "'", part[len(token):]
                # строка в кавычках первой в команде PowerShell только выводится, запускает её оператор &
                if re.search(r"(^|[;|&\n{(])\s*$", result):
                    value = "& " + value
        else:
            value = {"'": raw.replace("'", "'\\''"), '"': re.sub(r'([\\$"`])', r"\\\1", raw)}.get(state) \
                or shlex.quote(raw)
        result += value + part
    return result


def run_command(command: str, *, root: Path, workspace: Path, payload: dict, timeout: int) -> tuple[int, str]:
    code, stdout, _ = _execute(command, root=root, workspace=workspace, payload=payload, timeout=timeout)
    return code, stdout


def _execute(command: str, *, root: Path, workspace: Path, payload: dict, timeout: int) -> tuple[int, str, str]:
    from koddi.hidden_process import hidden_flags
    from koddi.tools.runner import shell_argv

    command = expand_root(command, root)
    try:
        result = subprocess.run(
            shell_argv(command), cwd=workspace, input=json.dumps(payload), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=min(max(timeout, 1), MAX_TIMEOUT), env=_env(root, workspace),
            creationflags=hidden_flags(), check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, "", ""
    except OSError:
        return -2, "", ""
    return result.returncode, result.stdout[: MAX_OUTPUT * 2], result.stderr[:MAX_OUTPUT]


def _audit(item: dict, event: str, code: int) -> None:
    from koddi.logs import log_event as audit_event

    audit_event("INFO" if code == 0 else "WARNING", "plugins.hook",
                f"{item['name']} {event} exit={code}", {"plugin": item["name"], "event": event, "exit_code": code})


def _run(item: dict, event: str, workspace: Path, payload: dict) -> list[str]:
    root = store.plugin_dir_for(item)
    outputs = []
    for hook in item["manifest"].get("hooks", []):
        if hook["event"] != event or not hook.get("supported"):
            continue
        if event == "SessionStart" and not _matches(hook.get("matcher", ""), "startup"):
            continue
        code, stdout = run_command(hook["command"], root=root, workspace=workspace,
                                   payload={**payload, "hook_event_name": event}, timeout=int(hook.get("timeout") or 60))
        _audit(item, event, code)
        if code == 0:
            text = _context(stdout)
            if text:
                outputs.append(text)
    return outputs


def turn_context(owner: str, project_id: int | None, workspace: Path, chat_id: int | None, prompt: str) -> list[dict]:
    """Контекст от разрешённых хуков: SessionStart один раз на чат (вывод сохраняется), UserPromptSubmit на каждый ход."""
    from koddi.logs.service import redact
    from koddi.policy.grants import permission_profile

    if permission_profile(project_id) == "read-only":
        return []
    result = []
    for item in store.approved(owner):
        events = {hook["event"] for hook in item["manifest"].get("hooks", []) if hook.get("supported")}
        if not events:
            continue
        payload = {"session_id": f"dokky-chat-{chat_id}" if chat_id else "", "cwd": str(workspace)}
        texts = []
        if "SessionStart" in events and chat_id is not None:
            texts.append(_session_start(item, workspace, chat_id, {**payload, "source": "startup"}))
        if "UserPromptSubmit" in events:
            texts += _run(item, "UserPromptSubmit", workspace, {**payload, "prompt": prompt[:20000]})
        text = "\n\n".join(value for value in texts if value)
        if text:
            result.append({"plugin": item["name"], "context": redact(text)[:MAX_OUTPUT]})
    return result


def _session_start(item: dict, workspace: Path, chat_id: int, payload: dict) -> str:
    from koddi.storage.db import db

    with db() as conn:
        row = conn.execute("SELECT digest, output FROM plugin_hook_outputs WHERE plugin_id=? AND chat_id=?",
                           (item["id"], chat_id)).fetchone()
    if row is not None and row["digest"] == item["exec_digest"]:
        return row["output"]
    text = "\n\n".join(_run(item, "SessionStart", workspace, payload))[:MAX_OUTPUT]
    with db() as conn:
        conn.execute(
            "INSERT INTO plugin_hook_outputs(plugin_id, chat_id, digest, output) VALUES (?,?,?,?) "
            "ON CONFLICT(plugin_id, chat_id) DO UPDATE SET digest=excluded.digest, output=excluded.output",
            (item["id"], chat_id, item["exec_digest"], text))
    return text


# имена инструментов Claude Code, на которые рассчитаны матчеры хуков; матчер сверяется и с именем Докки
CLAUDE_TOOLS = {
    "shell.run": "Bash", "file.create": "Write", "file.edit": "Edit", "file.search_replace": "Edit",
    "file.write_patch": "Edit", "patch.apply": "Edit", "code.symbol_edit": "Edit", "refactor.rename": "Edit",
    "file.read": "Read", "file.read_range": "Read", "file.read_chunk": "Read", "file.read_many": "Read",
    "search.text": "Grep", "search.files": "Glob", "directory.list": "LS", "directory.tree": "LS",
    "web.fetch": "WebFetch", "web.search": "WebSearch", "plan.update": "TodoWrite",
}
MAX_STOP_BLOCKS = 3


def _owner() -> str | None:
    from fastapi import HTTPException

    from koddi.policy.account import local_owner

    try:
        return local_owner()
    except HTTPException:
        return None


def _active(event: str, project_id: int | None) -> list[dict]:
    from koddi.policy.grants import permission_profile

    owner = _owner()
    if owner is None or permission_profile(project_id) == "read-only":
        return []
    return [item for item in store.approved(owner)
            if any(hook["event"] == event and hook.get("supported") for hook in item["manifest"].get("hooks", []))]


def _verdict(code: int, stdout: str, stderr: str) -> tuple[bool, str, str]:
    """(запрет, причина, контекст для модели). Разрешающие решения хука игнорируются: права даёт только политика."""
    if code == 2:
        return True, stderr.strip() or "blocked by a plugin hook", ""
    if code != 0:
        return False, "", ""
    text = stdout.strip()
    if not text.startswith("{"):
        return False, "", ""
    try:
        data = json.loads(text)
    except ValueError:
        return False, "", ""
    if not isinstance(data, dict):
        return False, "", ""
    specific = data.get("hookSpecificOutput") if isinstance(data.get("hookSpecificOutput"), dict) else {}
    context = specific.get("additionalContext") if isinstance(specific.get("additionalContext"), str) else ""
    if specific.get("permissionDecision") == "deny":
        return True, str(specific.get("permissionDecisionReason") or "blocked by a plugin hook"), context
    if data.get("decision") == "block":
        return True, str(data.get("reason") or "blocked by a plugin hook"), context
    if data.get("continue") is False:
        return True, str(data.get("stopReason") or "stopped by a plugin hook"), context
    return False, "", context


def _fire(event: str, project_id: int | None, workspace: Path, payload: dict, target: str = "",
          names: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """Запуск хуков события у всех разрешённых плагинов: (причины запрета, добавочный контекст)."""
    from koddi.logs.service import redact

    reasons, contexts = [], []
    for item in _active(event, project_id):
        root = store.plugin_dir_for(item)
        for hook in item["manifest"].get("hooks", []):
            if hook["event"] != event or not hook.get("supported"):
                continue
            if names and not any(_matches(hook.get("matcher", ""), name) for name in names):
                continue
            code, stdout, stderr = _execute(hook["command"], root=root, workspace=workspace,
                                            payload={**payload, "hook_event_name": event},
                                            timeout=int(hook.get("timeout") or 60))
            _audit(item, event, code)
            blocked, reason, context = _verdict(code, stdout, stderr)
            if blocked:
                reasons.append(f"{item['name']}: {redact(reason)[:2000]}")
            if context:
                contexts.append(f"{item['name']}: {redact(context)[:MAX_OUTPUT]}")
    return reasons, contexts


def _tool_payload(name: str, args: dict, workspace: Path, chat_id: int | None, mcp: tuple[str, str] | None) -> tuple[dict, tuple]:
    claude = f"mcp__{mcp[0]}__{mcp[1]}" if mcp else CLAUDE_TOOLS.get(name, name)
    tool_input = dict(args)
    # хуки Claude Code ждут абсолютный file_path
    if isinstance(args.get("path"), str) and "file_path" not in tool_input:
        tool_input["file_path"] = str((workspace / args["path"]).resolve()) if not Path(args["path"]).is_absolute() else args["path"]
    payload = {"session_id": f"dokky-chat-{chat_id}" if chat_id else "", "cwd": str(workspace),
               "tool_name": claude, "tool_input": tool_input}
    return payload, (claude, name)


def pre_tool(project_id: int | None, workspace: Path | None, chat_id: int | None, name: str, args: dict,
             mcp: tuple[str, str] | None = None) -> str | None:
    """Причина запрета от хуков PreToolUse или None. Хук не может разрешить действие в обход политики."""
    if workspace is None or not _active("PreToolUse", project_id):
        return None
    payload, names = _tool_payload(name, args, workspace, chat_id, mcp)
    reasons, _ = _fire("PreToolUse", project_id, workspace, payload, names=names)
    return "; ".join(reasons) or None


def post_tool(project_id: int | None, workspace: Path | None, chat_id: int | None, name: str, args: dict, result: dict,
              mcp: tuple[str, str] | None = None) -> list[str]:
    """Замечания хуков PostToolUse к результату: модель увидит их рядом с результатом инструмента."""
    if workspace is None or not _active("PostToolUse", project_id):
        return []
    payload, names = _tool_payload(name, args, workspace, chat_id, mcp)
    response = json.dumps(result, ensure_ascii=False, default=str)
    payload["tool_response"] = result if len(response) <= 20000 else {"truncated": True, "excerpt": response[:20000]}
    reasons, contexts = _fire("PostToolUse", project_id, workspace, payload, names=names)
    return reasons + contexts


def stop(project_id: int | None, workspace: Path | None, chat_id: int | None, blocks: int) -> str | None:
    """Причина продолжить работу от хуков Stop или None. После MAX_STOP_BLOCKS отказов хук больше не держит ход."""
    if workspace is None or blocks >= MAX_STOP_BLOCKS or not _active("Stop", project_id):
        return None
    payload = {"session_id": f"dokky-chat-{chat_id}" if chat_id else "", "cwd": str(workspace),
               "stop_hook_active": blocks > 0}
    reasons, _ = _fire("Stop", project_id, workspace, payload)
    return "; ".join(reasons) or None
