"""Разрешение на объект, а не на каждый вызов.

Человек отвечает «да» один раз на файл, приложение, сайт, сервер MCP или узкую команду, и дальше все такие же
действия с этим объектом в этом чате идут без вопросов. Удаление, опасные команды и вызовы без понятного объекта
сюда не попадают: их спрашивают каждый раз.
Разрешения живут в памяти до перезапуска приложения и привязаны к владельцу и чату человека (вложенные агенты
пользуются разрешениями родительского чата).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# правки файла: одно «да» на файл покрывает все эти инструменты
FILE_EDIT_TOOLS = frozenset({
    "file.create", "file.write_patch", "file.edit", "file.search_replace", "patch.apply", "code.symbol_edit",
})
# прогоны проекта: «да» на вид прогона (тесты, сборка, проверки) в этом проекте
RUN_TOOLS = {
    "test.run": "тесты проекта",
    "build.run": "сборку проекта",
    "lint.run": "проверку стиля",
    "format.run": "форматирование кода",
    "qa.run": "проверки проекта",
    "quality.run": "мутационные тесты и покрытие",
    "devserver.start": "запуск dev-сервера",
    "devserver.stop": "остановку dev-сервера",
    "verification.run": "проверку проекта",
}
# локальные git-действия, которые не уходят наружу и откатываются: одно «да» на репозиторий
GIT_LOCAL_TOOLS = frozenset({
    "git.add", "git.commit", "git.checkout", "git.create_branch", "git.restore_file", "git.worktree_create",
    "git.stash", "git.pull", "git.merge",
})
# программы, которым узкое разрешение не выдаётся: через них можно выполнить что угодно
_OPEN_PROGRAMS = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "powershell", "pwsh", "eval", "exec", "env", "sudo", "su",
    "doas", "xargs", "parallel", "watch", "ssh", "scp", "sftp", "rsync", "curl", "wget", "nc", "ncat", "telnet",
    "osascript", "open", "start", "invoke-expression", "iex", "python", "python3", "node", "ruby", "perl", "php",
    "lua",
})
# у этих программ подкоманда задаёт действие: npm test и npm publish - разные разрешения
_SUBCOMMAND_PROGRAMS = frozenset({
    "npm", "pnpm", "yarn", "bun", "npx", "pip", "pip3", "uv", "poetry", "cargo", "go", "git", "gh", "docker",
    "podman", "kubectl", "make", "gradle", "gradlew", "mvn", "mvnw", "dotnet", "swift", "xcodebuild", "flutter",
    "dart", "brew", "composer", "bundle", "rake", "mix", "deno", "ruff", "vite", "next", "expo",
})
# подкоманды, которые публикуют или уходят наружу: только каждый раз
_OUTWARD_SUBCOMMANDS = frozenset({
    "publish", "push", "deploy", "release", "login", "logout", "adduser", "token", "unpublish", "owner", "upload",
})
_SHELL_SYNTAX = re.compile(r"[;&|<>`\n]|\$\(|\$\{")
_SAFE_WORD = re.compile(r"^[\w.:@/+=,-]+$")


@dataclass(frozen=True)
class Scope:
    kind: str
    key: str
    label: str


# флаги ssh со значением: значение идёт следующим словом
_SSH_VALUE_FLAGS = frozenset({"-p", "-i", "-l", "-o", "-J", "-F", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-m", "-S"})
_SSH_HOST = re.compile(r"^(?:[\w.-]+@)?[\w.-]+$")


def _ssh_scope(words: list[str]) -> Scope | None:
    """ssh [флаги] [user@]host команда: разрешение на сервер, а не на каждую команду на нём."""
    index = 1
    while index < len(words) and words[index].startswith("-"):
        if words[index] in {"-o", "-F", "-J"} or words[index].startswith(("-o", "-F", "-J")):
            # свои настройки и прокси-команда меняют, куда и как идёт подключение
            return None
        index += 2 if words[index] in _SSH_VALUE_FLAGS else 1
    if index >= len(words) or not _SSH_HOST.match(words[index]):
        return None
    host = words[index].casefold()
    return Scope("host", host, f"команды на сервере {host} по SSH")


def _command_scope(command: str) -> Scope | None:
    """Узкий шаблон команды: программа и подкоманда без операторов оболочки и подстановок."""
    text = command.strip()
    if not text or _SHELL_SYNTAX.search(text):
        return None
    words = text.split()
    if any(not _SAFE_WORD.match(word) for word in words):
        return None
    program = words[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if program in {"ssh", "ssh.exe"}:
        return _ssh_scope(words)
    program = program[:-4] if program.endswith((".exe", ".cmd", ".bat")) else program
    if program.startswith("./"):
        program = program[2:]
    if program in _OPEN_PROGRAMS or program.startswith(("python", "pip")) and program not in _SUBCOMMAND_PROGRAMS:
        return None
    pattern = [program]
    if program in _SUBCOMMAND_PROGRAMS:
        rest = [word for word in words[1:] if not word.startswith("-")]
        if program in {"npm", "pnpm", "yarn", "bun"} and rest[:1] == ["run"] and len(rest) > 1:
            pattern += rest[:2]
        elif rest:
            pattern.append(rest[0].casefold())
        if any(word in _OUTWARD_SUBCOMMANDS for word in pattern[1:]):
            return None
        if program == "npx":
            # npx скачивает и запускает чужой пакет: разрешение только на точное имя пакета
            pattern = [program, *(rest[:1] or [])]
    text_pattern = " ".join(pattern)
    return Scope("cmd", text_pattern, f"команды «{text_pattern} …»")


def _site(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""


def _relative(path: str, workspace: Path | None) -> str:
    if workspace is None:
        return path
    try:
        return Path(path).resolve().relative_to(workspace.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _file_keys(tool: str, args: dict, workspace: Path | None) -> list[str]:
    """Пути, которые меняет вызов, в нормальном виде (абсолютные, без учёта регистра на Windows и macOS)."""
    values: list[str] = []
    for key in ("path", "source", "destination"):
        if isinstance(args.get(key), str):
            values.append(args[key])
    values.extend(item for item in args.get("paths") or [] if isinstance(item, str))
    if tool == "patch.apply" and isinstance(args.get("patch"), str):
        from koddi.tools.patches import parse_patch

        try:
            for patch in parse_patch(args["patch"]):
                # удаление файла патчем - не правка, разрешение на файл его не покрывает
                if patch.destination is None:
                    return []
                values.extend(item for item in (patch.source, patch.destination) if item)
        except (ValueError, TypeError):
            return []
    keys = []
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            if workspace is None:
                return []
            candidate = workspace / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return []
        keys.append(resolved.as_posix().casefold())
    return keys


def action_scopes(tool: str, args: dict, workspace: Path | None, *, dangerous: bool = False,
                  chat_id: int | None = None) -> list[Scope]:
    """Объекты, на которые человек может выдать разрешение одним ответом. Пусто - спрашивать каждый раз."""
    if dangerous:
        return []
    if tool == "computer.use":
        from koddi.computer_use.service import app_key, sensitive_app, target_app

        app = target_app(args, chat_id)
        # терминалы, пароли, настройки системы и сама Докки - только по одному действию
        if not app or sensitive_app(app):
            return []
        return [Scope("app", app_key(app, chat_id), f"приложение {app}")]
    if tool in FILE_EDIT_TOOLS:
        keys = _file_keys(tool, args, workspace)
        return [Scope("file", key, f"правки файла {_relative(key, workspace)}") for key in keys]
    if tool in RUN_TOOLS and workspace is not None:
        return [Scope("run", f"{tool}:{workspace.resolve().as_posix().casefold()}", RUN_TOOLS[tool])]
    if tool in GIT_LOCAL_TOOLS and workspace is not None:
        return [Scope("repo", workspace.resolve().as_posix().casefold(), "локальные действия git в этом проекте")]
    if tool == "shell.run":
        scope = _command_scope(str(args.get("command") or ""))
        return [scope] if scope else []
    if tool == "web.fetch" or tool.startswith("browser."):
        host = _site(str(args.get("url") or ""))
        return [Scope("site", host, f"сайт {host}")] if host else []
    if tool == "mcp.tool_call":
        server = str(args.get("server") or "")
        return [Scope("mcp", server, f"сервер MCP {server}")] if server else []
    if tool == "docs.lookup":
        return [Scope("mcp", "context7", "документацию Context7")]
    if tool == "cloud.video":
        return [Scope("cloud", tool, "разбор видео в облаке Докки")]
    if tool in {"cloud.image", "cloud.document"}:
        # файлы проекта уходят на сервер: разрешение не распространяется на другие наборы файлов
        sent = args.get("references") if tool == "cloud.image" else args.get("inputs")
        if sent:
            return []
        folder = str(args.get("save_to") or "dokky-cloud").strip().strip("/\\").casefold() or "dokky-cloud"
        label = "создание картинок в облаке Докки" if tool == "cloud.image" else "наброски документов в облаке Докки"
        # одно «да» - на одну папку результата: в другие папки проекта без вопроса не пишем
        return [Scope("cloud", f"{tool}:{folder}", f"{label} в папку {folder}")]
    return []


def project_edits(tool: str, args: dict, workspace: Path | None) -> str | None:
    """Ключ разрешения «все правки файлов проекта»: только если вызов правит файлы внутри папки проекта."""
    if tool not in FILE_EDIT_TOOLS or workspace is None:
        return None
    root = workspace.resolve().as_posix().casefold()
    keys = _file_keys(tool, args, workspace)
    if not keys or any(key != root and not key.startswith(root + "/") for key in keys):
        return None
    return root


_lock = threading.Lock()
_granted: dict[tuple[str, int], set[tuple[str, str]]] = {}


def covered(owner: str, chat_id: int | None, scopes: list[Scope]) -> bool:
    if chat_id is None or not scopes:
        return False
    with _lock:
        granted = _granted.get((owner, chat_id), set())
        return all((scope.kind, scope.key) in granted for scope in scopes)


def grant(owner: str, chat_id: int | None, scopes: list[Scope]) -> None:
    if chat_id is None or not scopes:
        return
    with _lock:
        _granted.setdefault((owner, chat_id), set()).update((scope.kind, scope.key) for scope in scopes)


def grant_kind(owner: str, chat_id: int | None, kind: str, key: str) -> None:
    grant(owner, chat_id, [Scope(kind, key, "")])


def granted(owner: str, chat_id: int | None, kind: str, key: str) -> bool:
    return covered(owner, chat_id, [Scope(kind, key, "")])


def forget_chat(owner: str, chat_id: int) -> None:
    with _lock:
        _granted.pop((owner, chat_id), None)


def describe(scopes: list[Scope]) -> str:
    """Подпись для вопроса человеку: на что распространится «Разрешить»."""
    labels = list(dict.fromkeys(scope.label for scope in scopes if scope.label))
    if not labels:
        return ""
    if len(labels) > 3:
        return ", ".join(labels[:3]) + f" и ещё {len(labels) - 3}"
    return ", ".join(labels)
