from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

READ_ONLY_TOOLS = {
    "computer.status",
    "workspace.inspect",
    "project.map",
    "task.history",
    'attachment.read',
    "tool.output_read",
    "project.instructions",
    "directory.tree",
    "directory.list",
    "file.read",
    "file.read_many",
    "file.metadata",
    "file.read_range",
    "file.read_chunk",
    "ignore.rules_read",
    "search.text",
    "search.files",
    "symbol.search",
    "code.outline",
    "code.references",
    "code.definition",
    "code.impact",
    "context.retrieve",
    "skill.load",
    "git.status",
    "git.diff",
    "git.log",
    "git.branch",
    "logs.list",
    "changes.latest",
    "secret.scan",
    "diff.review",
    "safety.review",
    "test_gap.analysis",
    "diagnostics.collect",
    "verification.run",
    "web.fetch",
    "web.search",
    "document.parse",
    "vision.inspect",
    "ocr.run",
    "memory.get",
    "task.report",
    "app.theme_check",
    "app.i18n_check",
    "app.update_check",
    "app.diagnostics",
    "devserver.detect",
    "devserver.output",
    "patch.preview",
    "test.detect",
    "build.detect",
    "git.worktree_list",
    "process.list",
    # только читают: локальные ссылки на удалённые ветки и данные GitHub через gh пользователя
    "git.remote",
    "git.fetch",
    "github.status",
    "github.pr_list",
    "github.pr_view",
    "github.pr_checks",
    "github.issue_list",
    "github.issue_view",
    "github.run_list",
    "github.run_view",
}
WRITE_TOOLS = {
    "workspace.open",
    "workspace.create",
    "workspace.snapshot",
    "file.create",
    "file.write_patch",
    "file.edit",
    "file.search_replace",
    "file.delete",
    "file.move",
    "file.copy",
    "file.restore",
    "patch.apply",
    "format.run",
    "refactor.rename",
    "refactor.extract",
    "code.symbol_edit",
    "git.checkout",
    "git.create_branch",
    "git.worktree_create",
    "git.worktree_remove",
    "git.add",
    "git.commit",
    "git.restore_file",
    "git.pull",
    "git.merge",
    "git.stash",
    # уходят наружу: каждый раз по режиму, разрешение на объект их не покрывает
    "git.push",
    "github.pr_create",
    "memory.set",
    "context.compact",
    "task.create",
    "task.plan",
    "task.update_status",
    "task.checklist",
    "task.ask_user",
    "task.spawn_parallel",
    "task.cancel",
    "task.resume",
    "automation.create",
    "automation.run",
    "skill.apply",
    "skill.create",
    "orchestrator.start_turn",
    "orchestrator.spawn_child",
    "orchestrator.join_child",
    "changes.rollback",
    "app.diagnostics",
    "mcp.tool_call",
    "mcp.tools_list",
    "ide.open_file",
    "ide.open_diff",
    "terminal.open",
    "devserver.start",
    "devserver.stop",
    "issue.import",
    "issue.update",
}
# смотреть на открытую страницу можно всегда; действия над ней и открытие решает _decide_browser
BROWSER_PASSIVE_TOOLS = {
    "browser.snapshot",
    "browser.screenshot",
    "browser.dom",
    "browser.inspect",
    "browser.console",
    "browser.network",
    "browser.wait",
    "browser.scroll",
    "browser.resize",
    "browser.close",
}
BROWSER_ACTIVE_TOOLS = {
    "browser.click",
    "browser.type",
    "browser.press",
    "browser.hover",
    "browser.select",
    "browser.eval",
}
# программы, которые сами по себе удаляют, форматируют, выключают или повышают права
DANGEROUS_PROGRAMS = frozenset({
    "rm", "rmdir", "rd", "del", "erase", "unlink", "shred", "dd", "format", "fdisk", "diskutil", "diskpart", "bcdedit",
    "shutdown", "reboot", "halt", "poweroff", "sudo", "su", "doas", "takeown",
    "remove-item", "ri", "clear-content", "clear-disk", "format-volume", "stop-computer", "restart-computer",
    "set-executionpolicy",
})
# обёртки: настоящая команда стоит после них и их флагов
_COMMAND_WRAPPERS = frozenset({
    "env", "command", "exec", "nohup", "time", "timeout", "gtimeout", "nice", "ionice", "caffeinate", "busybox", "builtin",
    "then", "do", "else", "if", "while", "!", "powershell", "pwsh", "cmd",
})
# выполняют переданные им слова как команду: на этом или другом компьютере
_RUNS_ARGUMENTS = frozenset({"xargs", "parallel", "watch", "ssh", "docker", "podman", "kubectl"})
_SHELLS = r"(?:sh|bash|zsh|dash|ksh|fish)"
_SCRIPT_HOSTS = rf"(?:{_SHELLS}|eval|cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?)"
# граница команды; строку после sh -c, cmd /c, powershell -Command и eval разбираем как отдельную команду
_COMMAND_BREAK = re.compile(
    r"&&|\|\||[;&|\n(){}`]|\$\(|\beval\s+"
    rf"|\b{_SHELLS}\s+(?:-\w+\s+)*-\w*c\s+"
    r"|\bcmd(?:\.exe)?\s+/[cCkK]\s+"
    r"|\b(?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+)*-(?:c|C|Command|command)\s+"
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SKIPPED_ARGUMENT = re.compile(r"^(?:-.*|\d+[smhd]?)$")
_SIMPLE_WORD = re.compile(r"^[\w./\\-]+$")
_FIND_DELETES = re.compile(r"\s-(?:delete\b|exec(?:dir)?\s+(?:\S*/)?(?:rm|shred|unlink)\b)")
_CHANGES_TREE = re.compile(r"\s-[a-zA-Z]*R[a-zA-Z]*\b")
# скачанное сразу выполняется: curl ... | sh, bash <(curl ...), sh -c "$(curl ...)", iex (iwr ...)
_DOWNLOAD = r"(?:curl|wget|fetch|iwr|irm|invoke-webrequest|invoke-restmethod)"
_RUNNER = rf"(?:sudo\s+)?(?:\S*/)?(?:{_SHELLS}|python[0-9.]*|node|perl|ruby|php|pwsh|powershell|iex|invoke-expression)"
_RUNS_DOWNLOAD = (
    re.compile(rf"\b{_DOWNLOAD}\b[^\n]*\|\s*{_RUNNER}\b", re.IGNORECASE),
    re.compile(rf"\b{_RUNNER}\b[^\n|]*(?:<\(|\$\(|`|\()\s*{_DOWNLOAD}\b", re.IGNORECASE),
)
# что подают оболочке на вход, заранее не видно
_INTO_SHELL = re.compile(rf"\|\s*(?:sudo\s+)?(?:\S*/)?{_SHELLS}(?:\s|$)", re.IGNORECASE)
_DEVICE_WRITE = re.compile(r">\s*/dev/(?:r?disk|sd|nvme|hd|mmcblk)", re.IGNORECASE)
_HOSTED_SCRIPT = re.compile(rf"\b{_SCRIPT_HOSTS}\b[^;&|\n]*?\x00(\d+)\x00", re.IGNORECASE)
_SUBSTITUTION = re.compile(r"\$\(([^)]*)\)|`([^`]*)`")
_GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _unquote(command: str) -> tuple[str, list[str], list[str]]:
    """Строка без кавычек: простое слово остаётся словом, остальное заменяется меткой, чтобы текст вроде сообщения коммита не считался командой."""
    outer: list[str] = []
    texts: list[str] = []
    expanded: list[str] = []
    index, size = 0, len(command)
    while index < size:
        char = command[index]
        if char == "\\":
            outer.append(command[index:index + 2])
            index += 2
        elif char in "\"'":
            end = index + 1
            while end < size and command[end] != char:
                end += 2 if char == '"' and command[end] == "\\" else 1
            text = command[index + 1:end]
            # r''m для оболочки то же самое, что rm
            if not text or _SIMPLE_WORD.match(text):
                outer.append(text)
            else:
                outer.append(f"\x00{len(texts)}\x00")
                texts.append(text)
                if char == '"':
                    expanded.extend(first or second for first, second in _SUBSTITUTION.findall(text))
            index = end + 1
        else:
            outer.append(char)
            index += 1
    return "".join(outer), texts, expanded


def _dangerous_git(words: list[str]) -> bool:
    index = 1
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in _GIT_VALUE_OPTIONS else 1
    if index >= len(words):
        return False
    action, rest = words[index], words[index + 1:]
    forced = any(word == "--force" or re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", word) for word in rest)
    if action in {"rm", "filter-branch", "filter-repo"}:
        return True
    if action == "reset":
        return "--hard" in rest
    if action == "clean":
        return forced
    if action == "push":
        return forced or any(
            word.startswith(("--force", "+", ":")) or word in {"--delete", "-d", "--mirror"} for word in rest
        )
    if action == "branch":
        return "-D" in rest or ("--delete" in rest and forced)
    if action == "stash":
        return rest[:1] in (["drop"], ["clear"])
    if action == "checkout":
        return forced or "." in rest or "--" in rest
    if action == "switch":
        return forced or "--discard-changes" in rest
    if action == "restore":
        return "--staged" not in rest or "--worktree" in rest or "-W" in rest
    if action == "update-ref":
        return "-d" in rest
    if action == "worktree":
        return rest[:1] == ["remove"] and forced
    return False


def _dangerous_segment(segment: str) -> bool:
    words = segment.split()
    while words:
        word = words[0].strip("\\")
        name = word.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        name = name[:-4] if name.endswith(".exe") else name
        if _ASSIGNMENT.match(word):
            words.pop(0)
        elif name in _COMMAND_WRAPPERS:
            words.pop(0)
            while words and (_SKIPPED_ARGUMENT.match(words[0]) or _ASSIGNMENT.match(words[0])):
                words.pop(0)
        else:
            break
    if not words:
        return False
    line = " ".join(words)
    if name in DANGEROUS_PROGRAMS or name.startswith("mkfs"):
        return True
    if name == "git":
        return _dangerous_git(words)
    if name == "find":
        return bool(_FIND_DELETES.search(line))
    if name in {"chmod", "chown"}:
        return bool(_CHANGES_TREE.search(line))
    if name == "reg":
        return words[1:2] != [] and words[1].casefold() == "delete"
    if name in _RUNS_ARGUMENTS:
        return any(word.rsplit("/", 1)[-1].casefold() in DANGEROUS_PROGRAMS for word in words[1:])
    return False


def dangerous_command(command: str) -> bool:
    """Разбор по программам, а не по подстрокам: «format» в обычном слове и текст в кавычках не срабатывают, rm через табуляцию срабатывает.

    Это предохранитель от ошибки модели, а не песочница: команду, собранную из переменных, он не увидит.
    """
    if any(pattern.search(command) for pattern in _RUNS_DOWNLOAD):
        return True
    outer, texts, expanded = _unquote(command)
    if _INTO_SHELL.search(outer) or _DEVICE_WRITE.search(outer):
        return True
    # строка в кавычках тоже бывает командой: bash -c "...", echo "$(...)"
    hosted = [texts[int(number)] for number in _HOSTED_SCRIPT.findall(outer)]
    if any(dangerous_command(text) for text in (*hosted, *expanded)):
        return True
    # r\m для оболочки то же самое, что rm, но обратная черта бывает и разделителем пути Windows: смотрим оба варианта
    return any(
        _dangerous_segment(segment) or ("\\" in segment and _dangerous_segment(segment.replace("\\", "")))
        for segment in _COMMAND_BREAK.split(outer)
    )


# команды, которые только читают: идут без вопросов в любом режиме
_READ_PROGRAMS = frozenset({
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "wc", "grep", "egrep", "rg", "ag", "ack", "findstr", "tree",
    "file", "stat", "du", "df", "which", "where", "whereis", "uname", "whoami", "hostname", "date", "echo", "printf",
    "basename", "dirname", "realpath", "readlink", "sort", "uniq", "cut", "diff", "cmp", "jq",
    "get-childitem", "gci", "get-content", "gc", "select-string", "sls", "get-location", "test-path", "get-item",
    "resolve-path", "get-command", "measure-object",
    # ничего не читают и не меняют: ожидание, пустые команды, сведения о системе
    "sleep", "true", "false", "test", "[", "seq", "uptime", "nproc", "sw_vers", "start-sleep",
})
_READ_GIT = frozenset({
    "status", "log", "diff", "show", "blame", "shortlog", "describe", "rev-parse", "ls-files", "ls-tree",
    "cat-file", "grep", "reflog", "for-each-ref", "name-rev", "merge-base",
})
_READ_VERSION = re.compile(r"^[\w.+-]+(?:\.exe)?\s+(?:--version|-v|-V|version)$")
# вывод в файл, подстановки и фон делают «чтение» записью или запуском чего угодно;
# последовательность (;, &&, ||, перенос строки) из одних чтений - по-прежнему чтение
# скобки в PowerShell выполняют команду: Get-Content (New-Item x)
_WRITE_SYNTAX = re.compile(r"[&<>`(]")
_SEQUENCE = re.compile(r"&&|\|\||[;\n]")
# вывод в никуда ничего не пишет: 2>/dev/null, >/dev/null, 2>&1, >$null и > NUL в Windows
_DISCARD = re.compile(
    r"\s*(?:(?:[12]|&)?>\s*(?:/dev/null|\$null" + (r"|nul" if sys.platform == "win32" else "") + r")|2>&1)(?=\s|$|[;|])",
    re.IGNORECASE,
)
_SENSITIVE_PARTS = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".gcloud", "keychains", ".password-store", ".netrc",
    ".pgpass", ".git-credentials", "credentials", "koddi", ".npmrc", ".pypirc",
})
_SENSITIVE_NAME = re.compile(r"(?:^\.env(?:\..*)?$|^id_[a-z0-9]+|\.(?:pem|key|p12|pfx|keychain|kdbx)$)", re.IGNORECASE)


def sensitive_path(path: str) -> bool:
    """Ключи, пароли, токены и данные самой Докки: чтение вне проекта таких мест всегда спрашивается."""
    parts = Path(path).expanduser().parts
    lowered = [part.casefold() for part in parts]
    if any(part in _SENSITIVE_PARTS for part in lowered):
        return True
    if "gh" in lowered and ".config" in lowered:
        return True
    return bool(parts and _SENSITIVE_NAME.search(parts[-1]))


def _short_flag(word: str, letter: str) -> bool:
    """Короткий флаг в слове, в том числе склеенный с другими: -ro, -oout."""
    return word.startswith("-") and not word.startswith("--") and letter in word[1:]


def _read_segment(segment: str) -> bool:
    words = segment.split()
    if not words:
        return False
    name = words[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    name = name[:-4] if name.endswith(".exe") else name
    if name == "git":
        index = 1
        while index < len(words) and words[index].startswith("-"):
            if words[index] in _GIT_VALUE_OPTIONS:
                return False
            index += 1
        return index < len(words) and words[index] in _READ_GIT and "--output" not in " ".join(words)
    if name == "find":
        return not re.search(r"\s-(?:delete|exec|execdir|ok|okdir|fprint\w*|fls)\b", " ".join(words))
    if name == "sed":
        return all(not word.startswith("-i") and word != "--in-place" for word in words[1:])
    if name == "sort":
        # -o пишет в файл и в склейке (-ro, -oout), длинные опции GNU берёт и сокращёнными (--out, --compress)
        return not any(_short_flag(word, "o") or word.startswith(("--o", "--com")) for word in words[1:])
    if name == "uniq":
        # второй файл у uniq - куда писать
        return len([word for word in words[1:] if not word.startswith("-")]) <= 1
    if name in {"rg", "tree"}:
        # rg --pre запускает программу, tree -o пишет в файл
        return not any(word.startswith("--pre") or (name == "tree" and (_short_flag(word, "o") or word.startswith("--o"))) for word in words[1:])
    return name in _READ_PROGRAMS or bool(_READ_VERSION.match(segment.strip()))


def read_only_command(command: str) -> bool:
    """Команда, которая ничего не меняет: чтение, поиск, git status/log/diff, версии программ. Можно конвейером."""
    if dangerous_command(command):
        return False
    outer, texts, expanded = _unquote(command)
    steps = [step.strip() for step in _SEQUENCE.split(_DISCARD.sub("", outer))]
    if expanded or any(_WRITE_SYNTAX.search(step) for step in steps):
        return False
    segments = [part.strip() for step in steps for part in step.split("|")]
    return bool(segments) and all(segment and _read_segment(segment) for segment in segments)


# файлы вне проекта открываются только этим инструментам и только по абсолютному пути
EXTERNAL_PATH_TOOLS = {
    "file.read",
    "file.read_many",
    "file.read_range",
    "file.read_chunk",
    "file.metadata",
    "file.create",
    "file.write_patch",
    "file.edit",
    "file.search_replace",
}


@dataclass(frozen=True)
class Decision:
    """allow - выполнить; ask - спросить человека; review - сначала проверка Докки (режим «Автоматически»),
    при отказе проверки спросить человека; block - нельзя ни в каком режиме."""

    action: str
    reason: str
    external: tuple[str, ...] = ()
    dangerous: bool = False


def quality_runs_processes(args: dict) -> bool:
    """quality.run запускает тесты проекта только для мутаций и сбора покрытия; без явных gates - по бюджету."""
    if args.get("report_id") is not None:
        return False
    if args.get("coverage") == "collect":
        return True
    gates = args.get("gates")
    if gates:
        return "mutation" in gates
    return not (args.get("decisions") or args.get("architecture_proposal") or args.get("suggest_layers"))


def _confirm(profile: str, reason: str, **extra) -> Decision:
    """Действие, которое меняет что-то или выходит за проект: решение зависит от режима подтверждений."""
    if profile == "full":
        return Decision("allow", "full access granted by the user", **extra)
    if profile == "trusted":
        return Decision("review", reason, **extra)
    return Decision("ask", reason, **extra)


def _decide_fetch(args: dict, profile: str) -> Decision:
    """Адрес страницы уходит чужому серверу, и в нём можно вынести данные проекта. Вопрос зависит от режима."""
    if profile == "full":
        return Decision("allow", "full access granted by the user")
    if profile == "trusted":
        try:
            parts = urlsplit(str(args.get("url") or ""))
        except ValueError:
            return Decision("ask", "the address could not be checked")
        if not parts.query and "@" not in parts.netloc:
            return Decision("allow", "page address without request parameters")
        return Decision("ask", "the address carries request parameters: data could leave the computer")
    return Decision("ask", "opens a site the user has not allowed in this chat")


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _local_target(url: str) -> bool:
    from koddi.browser.service import is_loopback_url

    value = url.strip()
    if "://" not in value and not value.lower().startswith("file:"):
        return True  # путь внутри проекта, проверяется при открытии
    return value.lower().startswith("file:") or is_loopback_url(value)


def _decide_browser(tool_name: str, args: dict, workspace: Path | None, profile: str) -> Decision:
    """Свой localhost и файлы проекта открываются сразу; чужие сайты и действия на них - по профилю."""
    from koddi.browser.service import live_sessions, session_info

    if tool_name in BROWSER_PASSIVE_TOOLS:
        return Decision("allow", "browser view")
    if profile == "read-only":
        return Decision("block", "read-only permission profile")
    if workspace is None:
        return Decision("block", "workspace is required")
    by_profile = _confirm(profile, f"profile={profile}")
    if tool_name == "browser.open":
        return Decision("allow", "local preview") if _local_target(str(args.get("url", ""))) else by_profile
    info = session_info(args.get("session_id") or None)
    if info is None:
        sessions = live_sessions()
        info = sessions[-1] if sessions else None
    local = bool(info and info.get("local"))
    if tool_name == "browser.navigate":
        url = args.get("url")
        if url and not _local_target(str(url)):
            return by_profile
        return Decision("allow", "local preview") if local else by_profile
    if tool_name in BROWSER_ACTIVE_TOOLS:
        return Decision("allow", "local preview") if local else by_profile
    return Decision("block", f"unknown tool {tool_name}")


def _plugin_script(command: str) -> bool:
    """Команда обращается к файлам установленного плагина (по пути папки данных или через переменную плагина)."""
    from koddi.plugins.store import plugins_root

    # кавычки и экранированные пробелы (Application\ Support) не должны прятать путь
    text = command.replace("\\ ", " ").replace('"', "").replace("'", "").replace("\\", "/").casefold()
    root = plugins_root().resolve()
    home = Path.home().resolve().as_posix().casefold()
    roots = set()
    # папка данных вместе со словом plugins - переход в папку данных и запуск plugins/... относительным путём
    for folder in (root.as_posix().casefold(), root.parent.as_posix().casefold()):
        roots.add(folder)
        if folder.startswith(home + "/"):
            roots |= {"~" + folder[len(home):], "$home" + folder[len(home):], "${home}" + folder[len(home):]}
    near_data = any(item in text for item in roots) and "plugins" in text
    return "claude_plugin_root" in text or "dokky_plugin_root" in text or "plugin://" in text or near_data


def _mcp_servers_enabled() -> bool:
    from koddi.integrations.mcp import _load_servers

    try:
        servers = _load_servers()
    except (ValueError, OSError):
        return False
    return any(not server.get("disabled", False) for server in servers.values())


def decide(tool_name: str, args: dict, workspace: Path | None, project_id: int | None = None) -> Decision:
    from koddi.policy.grants import permission_profile

    profile = permission_profile(project_id)
    path_tools = {
        "workspace.inspect",
        "project.instructions",
        "directory.tree",
        "directory.list",
        "file.read",
        "file.read_many",
        "file.metadata",
        "file.read_range",
        "file.read_chunk",
        "file.create",
        "file.write_patch",
        "file.edit",
        "file.search_replace",
        "file.delete",
        "file.move",
        "file.copy",
        "patch.apply",
        "patch.preview",
        "code.outline",
        "code.references",
        "code.definition",
        "code.symbol_edit",
        "code.impact",
        "context.retrieve",
        "docs.lookup",
        "refactor.rename",
        "refactor.extract",
        "document.parse",
        "vision.inspect",
        "ocr.run",
        "verification.run",
        "qa.run",
        "quality.run",
        "ide.open_file",
        "git.restore_file",
        "git.add",
        "git.worktree_create",
        "git.worktree_remove",
    }
    external: list[str] = []
    if tool_name in path_tools:
        if workspace is None:
            return Decision("block", "workspace is required")
        from koddi.policy.paths import PathAccessError, WorkspacePaths, external_path

        try:
            paths = WorkspacePaths(workspace)
            values = [args[key] for key in ("path", "source", "destination") if key in args]
            values.extend(args.get("paths", []))
            if "patch" in args and tool_name in {"patch.apply", "patch.preview"}:
                from koddi.tools.patches import parse_patch

                for patch in parse_patch(args["patch"]):
                    values.extend(p for p in (patch.source, patch.destination) if p)
            for value in values:
                try:
                    paths.resolve(value)
                except PathAccessError as exc:
                    if tool_name not in EXTERNAL_PATH_TOOLS or "escapes workspace" not in str(exc):
                        raise
                    external.append(str(external_path(value)))
        except (ValueError, OSError) as exc:
            return Decision("block", str(exc))
    if tool_name == "computer.use":
        if workspace is None or profile == "read-only":
            return Decision("block", "computer use requires a writable project")
        from koddi.computer_use.service import consent_enabled

        if not consent_enabled():
            return Decision("block", "enable Computer Use in settings first")
        from koddi.computer_use.service import READ_COMMANDS

        # список приложений, окон и экранов, справка и статус ничего не трогают
        if args.get("command") in READ_COMMANDS or profile == "full":
            return Decision("allow", "full access granted by the user" if profile == "full" else "computer overview")
        # одно «да» на приложение покрывает остальное в нём (policy/scopes.py); автопроверка тут не решает
        return Decision("ask", "working with a local application needs the user's approval once per app")
    if tool_name.startswith("browser."):
        return _decide_browser(tool_name, args, workspace, profile)
    if tool_name == "ui.verify":
        # шаги нажимают в странице проекта, как browser.click; чужие сайты ui.verify не открывает
        if profile == "read-only":
            return Decision("block", "read-only permission profile")
        if workspace is None or not _local_target(str(args.get("url", ""))):
            return Decision("block", "ui.verify checks only project pages (localhost or project files)")
        return Decision("allow", "local preview")
    if external:
        reads = tool_name in READ_ONLY_TOOLS
        if profile == "read-only" and not reads:
            return Decision("block", "read-only permission profile")
        # чтение без вопросов в любом режиме, кроме мест, где лежат ключи и пароли
        if reads and not any(sensitive_path(item) for item in external):
            return Decision("allow", "reading a file outside the project", external=tuple(external))
        if profile == "read-only":
            return Decision("ask", "file outside the project", external=tuple(external))
        return _confirm(profile, "file outside the project", external=tuple(external))
    if tool_name == "skill.load" and not args.get("file"):
        from koddi.skills.selector import needs_consent

        # навык из скачанного плагина - чужая инструкция: агент предлагает, решает человек (кроме «Разрешить всё»)
        if profile != "full" and needs_consent(str(args.get("name") or ""), project_id, workspace):
            return Decision("ask", "uses a skill from a plugin the user installed: the user decides each time")
    if tool_name == "web.fetch":
        return _decide_fetch(args, profile)
    if tool_name in {"cloud.image", "cloud.document", "cloud.video"}:
        if profile == "read-only":
            return Decision("block", "read-only permission profile")
        if workspace is None and tool_name != "cloud.video":
            return Decision("block", "workspace is required")
        # стоит денег пользователя, текст запроса и перечисленные файлы проекта уходят на сервер Докки
        return _confirm(profile, "cloud task at the user's expense; the request and listed project files go to the Dokky server")
    if tool_name in READ_ONLY_TOOLS:
        return Decision("allow", "read-only tool")
    if tool_name == "docs.lookup":
        if not args.get("remote"):
            return Decision("allow", "local package documentation")
        if profile == "read-only":
            return Decision("block", "read-only permission profile")
        # Context7 - внешний MCP-сервер: как любой вызов MCP, по режиму подтверждений
        return _confirm(profile, "documentation request to Context7 through MCP")
    if tool_name == "project.knowledge":
        # поиск и сверка заметок в данных Докки; выгрузка в папку проекта включается только пользователем
        return Decision("allow", "project knowledge") if workspace else Decision("block", "workspace is required")
    if tool_name == "project.map_update":
        # заметки агента лежат в данных Докки и не меняют файлы проекта, как и план хода
        return Decision("allow", "agent notes in the project map") if workspace else Decision("block", "workspace is required")
    if tool_name == "quality.run":
        if workspace is None:
            return Decision("block", "workspace is required")
        if not quality_runs_processes(args):
            # разбор кода, отчёты и предложения слоёв не запускают ничего из проекта
            return Decision("allow", "static analysis of project code")
        if profile == "read-only":
            return Decision("block", "read-only permission profile")
        return _confirm(profile, "run project tests for coverage or mutation testing")
    if tool_name == "mcp.tools_list" and not _mcp_servers_enabled():
        # без включённых серверов список ничего не запускает, спрашивать не о чем
        return Decision("allow", "no MCP servers configured")
    if profile == "read-only":
        return Decision("block", "read-only permission profile")
    if workspace is None and tool_name not in {
        "logs.list",
        "changes.latest",
        "web.fetch",
        "web.search",
        "workspace.open",
        "workspace.create",
        "app.update_check",
        "app.i18n_check",
        "app.theme_check",
        "app.diagnostics",
        "diagnostics.collect",
        "process.list",
    }:
        return Decision("block", "workspace is required")
    command = args.get("command")
    if isinstance(command, str) and tool_name != "safety.review" and profile != "full" and _plugin_script(command):
        # скрипты плагинов запускает только человек: вопрос в любом режиме, кроме «Разрешить всё»,
        # и для любого инструмента с командой (test.run, build.run, devserver.start и т.д.)
        return Decision("ask", "runs a file of an installed plugin: every run needs the user's approval")
    if tool_name == "qa.run":
        # команды находит Докки, но тесты и скрипты проекта - это выполнение кода проекта
        return _confirm(profile, "run project checks: lint, types, tests")
    if tool_name in WRITE_TOOLS or tool_name.startswith(("test.", "build.", "lint.", "process.")):
        return _confirm(profile, f"profile={profile}")
    if tool_name == "shell.run":
        command = str(args.get("command", ""))
        if read_only_command(command):
            return Decision("allow", "read-only command")
        if dangerous_command(command):
            return _confirm(profile, "dangerous shell command", dangerous=True)
        return _confirm(profile, f"profile={profile}")
    return Decision("block", f"unknown tool {tool_name}")
