from __future__ import annotations

import base64
import json
import hashlib
import fnmatch
import atexit
import os
import re
import regex
import shutil
import signal
import subprocess
import threading
import time
import tempfile
from dataclasses import replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from koddi.agent_runtime import (
    create_task,
    set_checklist,
    spawn_child,
    task_graph,
    update_task_status,
)
from koddi.automations import create_automation, run_automation
from koddi.browser import (
    browser_event,
    browser_open,
    file_url_for,
    is_loopback_url,
    local_preview_allowed,
)
from koddi.changes import latest_change_set
from koddi.config import settings
from koddi.dokky.web import web_fetch, web_search
from koddi.installer import update_check
from koddi.i18n import check_locales
from koddi.integrations import (
    issue_import,
    issue_update,
    mcp_tool_call_sync,
    mcp_tools_list,
    open_diff,
    open_file,
    terminal_open,
)
from koddi.logs import list_logs, log_event
from koddi.logs.service import redact
from koddi.policy import decide
from koddi.policy.paths import PathAccessError, WorkspacePaths, external_path
from koddi.policy.instructions import scoped_instructions
from koddi.tools.patches import parse_patch, prepare_patch
from koddi.review import diff_review, safety_review, secret_scan
from koddi.skills import apply_skill, create_skill
from koddi.storage.db import db, runtime_path
from koddi.tools.schemas import validate_tool_arguments
from koddi.tools.output_store import read_tool_output
from koddi.tools.read_cache import FileReadCache


class ToolError(RuntimeError):
    pass


_DEV_SERVERS: dict[int, dict[str, Any]] = {}
_DEV_SERVERS_LOCK = threading.RLock()


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()[-limit:]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _running_servers(owner_id: str, workspace: Path) -> list[dict[str, Any]]:
    with _DEV_SERVERS_LOCK:
        items = list(_DEV_SERVERS.items())
    return [{"pid": pid, "command": item["command"], "running": item["proc"].poll() is None}
            for pid, item in items if item.get("owner_id") == owner_id and item.get("workspace") == str(workspace)]


def _stop_process_tree(proc: subprocess.Popen) -> None:
    """Останавливает команду вместе с дочерними процессами: на Windows taskkill /T, на macOS и Linux группа процессов."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=HIDDEN_SUBPROCESS, timeout=10, check=False,
        )
    else:
        _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            _signal_group(proc, signal.SIGKILL)
        proc.wait(timeout=3)


@atexit.register
def _stop_dev_servers() -> None:
    for item in list(_DEV_SERVERS.values()):
        try:
            _stop_process_tree(item["proc"])
        except Exception:  # noqa: BLE001 - выход не должен падать из-за чужого процесса
            pass


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


HIDDEN_SUBPROCESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_SEARCH_CANDIDATE_PATHS = 100000
_POSIX_SHELLS = {"zsh", "bash", "sh", "fish"}


def _rc_prefix(shell: str) -> str:
    """PATH из .zshrc (nvm, pyenv, свои каталоги) нужен агенту так же, как человеку в терминале: подгружаем его молча."""
    name = Path(shell).name
    rc = {"zsh": "~/.zshrc", "bash": "~/.bashrc"}.get(name)
    return f"[ -r {rc} ] && . {rc} >/dev/null 2>&1; " if rc else ""


def shell_argv(command: str) -> list[str]:
    """Команда оболочки: PowerShell на Windows, на macOS и Linux - оболочка пользователя в режиме входа.

    Режим входа (-l) подтягивает PATH из профиля (Homebrew, nvm и т.п.): у программы, запущенной из Finder,
    он почти пустой.
    """
    if os.name == "nt":
        # команда собирается уже после смены кодировки: иначе ошибки разбора PowerShell приходят в OEM-кодировке
        payload = base64.b64encode(command.encode("utf-8")).decode("ascii")
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            "$ErrorView = 'NormalView'; "
            f"& ([scriptblock]::Create([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}'))))",
        ]
    shell = os.environ.get("SHELL") or ""
    if Path(shell).name not in _POSIX_SHELLS or not Path(shell).is_file():
        shell = "/bin/zsh" if Path("/bin/zsh").is_file() else "/bin/sh"
    return [shell, "-l", "-c", _rc_prefix(shell) + command]


class _ToolOutputBuffer:
    def __init__(self, limit: int):
        if limit < 128:
            raise ValueError("Лимит результата должен быть не меньше 128 символов")
        self.limit = limit
        self.head = ""
        self.tail = ""
        self.chars = 0
        self.newlines = 0
        self.pending_cr = False

    def append(self, text: str) -> None:
        if self.pending_cr:
            text = "\r" + text
        self.pending_cr = text.endswith("\r")
        if self.pending_cr:
            text = text[:-1]
        self._append(text.replace("\r\n", "\n"))

    def _append(self, text: str) -> None:
        self.chars += len(text)
        self.newlines += text.count("\n")
        self.head += text[: max(0, self.limit - len(self.head))]
        self.tail = (self.tail + text)[-(self.limit + 1) :]

    def finish(self) -> str:
        if self.pending_cr:
            self._append("\r")
            self.pending_cr = False
        if self.chars <= self.limit:
            return self.head
        # резерв учитывает сам маркер: готовый excerpt повторно не сокращается
        reserve = 96
        while True:
            available = max(0, self.limit - reserve)
            head_size = available * 7 // 10
            tail_size = available - head_size
            head = self.head[:head_size]
            tail = self.tail[-tail_size:] if tail_size else ""
            omitted_chars = self.chars - len(head) - len(tail)
            omitted_lines = self.newlines - head.count("\n") - tail.count("\n")
            omitted_last = self.tail[-tail_size - 1] if tail_size else self.tail[-1]
            omitted_lines += int(omitted_last != "\n")
            marker = f"\n...[пропущено {omitted_chars} символов, {omitted_lines} строк]...\n"
            if len(marker) <= reserve:
                return head + marker + tail
            reserve = len(marker)


def tool_output_excerpt(text: str, limit: int = 20000) -> str:
    """детерминированный excerpt нового результата, не преобразование истории."""
    output = _ToolOutputBuffer(limit)
    output.append(text)
    return output.finish()


def _line_changes(before: str | None, after: str | None) -> tuple[int, int]:
    matcher = SequenceMatcher(
        a=(before or "").splitlines(keepends=True), b=(after or "").splitlines(keepends=True)
    )
    added = removed = 0
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation in {"insert", "replace"}:
            added += new_end - new_start
        if operation in {"delete", "replace"}:
            removed += old_end - old_start
    return added, removed


def _run_async(coroutine):
    """Запускает асинхронный провайдер из синхронного вызова инструмента.

    В AgentOrchestrator уже крутится event loop, и asyncio.run там упадёт. Короткоживущий
    поток оставляет ToolRunner синхронным, а корутины HTTP и браузера спокойно доделываются.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    result: dict[str, Any] = {}
    failure: list[BaseException] = []

    def execute() -> None:
        try:
            result["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # propagate provider failures to audit path
            failure.append(exc)

    thread = threading.Thread(target=execute, name="koddi-async-tool", daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result.get("value")


def _safe_path(workspace: Path, relative: str) -> Path:
    return WorkspacePaths(workspace).resolve(relative)


def _record_tool(
    turn_id: int | None, name: str, args: dict[str, Any], status: str, result: dict
) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO tool_calls(turn_id, name, arguments_json, result_json, status) VALUES (?, ?, ?, ?, ?)",
            (
                turn_id,
                name,
                json.dumps(redact(args), ensure_ascii=False),
                json.dumps(redact(result), ensure_ascii=False),
                status,
            ),
        )


# семантические инструменты кода: имя инструмента -> функция koddi.codeintel.tools
CODE_TOOLS = {
    "symbol.search": "symbol_search",
    "code.outline": "outline",
    "code.references": "references",
    "code.definition": "definition",
    "code.symbol_edit": "symbol_edit",
    "refactor.rename": "rename",
    "refactor.extract": "extract",
}


# инструменты, которые меняют файлы или индекс git рабочей папки: их ход держит замок «один писатель»
WORKSPACE_WRITERS = {
    "file.create", "file.write_patch", "file.edit", "file.search_replace", "file.delete", "file.move", "file.copy",
    "file.restore", "patch.apply", "format.run", "refactor.rename", "refactor.extract", "code.symbol_edit",
    "git.checkout", "git.create_branch", "git.add", "git.commit", "git.restore_file", "changes.rollback",
}


class ToolRunner:
    SAFE_EXPLICIT_WRITE_TOOLS = {
        "file.create",
        "file.write_patch",
        "file.edit",
        "file.search_replace",
        "patch.apply",
    }

    def __init__(
        self,
        workspace: Path | None,
        project_id: int | None = None,
        turn_id: int | None = None,
        explicit_write_consent: bool = False,
        cancel_event: threading.Event | None = None,
        *,
        review_actions: bool = False,
    ) -> None:
        self.workspace = workspace.resolve() if workspace else None
        # в цикле агента решение review уходит на проверку Докки; прямой вызов из интерфейса делает сам человек
        self.review_actions = review_actions
        self._external: set[str] = set()
        self.project_id = project_id
        self.turn_id = turn_id
        self.explicit_write_consent = explicit_write_consent
        self.cancel_event = cancel_event or threading.Event()
        self.read_cache = FileReadCache()
        self.computer_session = None
        self.pending_diagnostics = {}
        # инженерный цикл и глубина хода задаёт оркестратор; прямой вызов из интерфейса - без них
        self.ub_workflow = False
        self.quality_depth: str | None = None
        # файлы, которые агент сам положил в индекс: только их можно коммитить без явных путей
        self.agent_staged: set[str] = set()

    async def _network(self, coroutine):
        import asyncio

        pending = asyncio.create_task(coroutine)
        try:
            while not pending.done():
                if self.cancel_event.is_set():
                    raise ToolError('operation cancelled')
                await asyncio.wait({pending}, timeout=0.05)
            return pending.result()
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    def run(self, name: str, args: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        if self.cancel_event.is_set():
            return {"ok": False, "cancelled": True, "error": "operation cancelled"}
        validation_error = validate_tool_arguments(name, args)
        if validation_error:
            _record_tool(self.turn_id, name, args, "error", validation_error)
            log_event("ERROR", "tool.invalid_arguments", name, validation_error)
            return validation_error
        decision = decide(name, args, self.workspace, self.project_id)
        if approved and decision.action in {"ask", "review"}:
            decision = replace(decision, action="allow", reason="approved for this exact call")
        if decision.action == "review" and not self.review_actions:
            decision = replace(decision, action="allow", reason="automatic mode outside the agent loop")
        if (
            decision.action == "ask"
            and not decision.external
            and self.explicit_write_consent
            and name in self.SAFE_EXPLICIT_WRITE_TOOLS
            and not (
                name == "patch.apply"
                and "patch" in args
                and any(
                    patch.destination is None
                    or (patch.source and patch.source != patch.destination)
                    for patch in parse_patch(args["patch"])
                )
            )
        ):
            decision = replace(decision, action="allow", reason="explicit user request for a workspace edit")
        if decision.action == "block":
            result = {"ok": False, "blocked": True, "reason": decision.reason}
            _record_tool(self.turn_id, name, args, "blocked", result)
            log_event("WARNING", "tool.blocked", name, result)
            return result
        if decision.action in {"ask", "review"}:
            result = {
                "ok": False,
                "approval_required": True,
                "review": decision.action == "review",
                "external": list(decision.external),
                "dangerous": decision.dangerous,
                "reason": decision.reason,
                "tool": name,
                "args": args,
            }
            _record_tool(self.turn_id, name, args, "approval_required", result)
            log_event("INFO", "tool.approval_required", name, {"approval_required": True})
            return result

        if name in WORKSPACE_WRITERS and self.workspace is not None:
            from koddi.changes.writer import WriterBusy, claim

            try:
                claim(self.workspace, self.turn_id)
            except WriterBusy as exc:
                result = {"ok": False, "writer_busy": True, "holder_turn": exc.holder.turn_id,
                          "error": "Эту папку сейчас меняет другой ход агента. Подождите его завершения или работайте "
                                   "в отдельном worktree - два писателя в одной папке перетирают правки друг друга."}
                _record_tool(self.turn_id, name, args, "error", result)
                return result
        try:
            from koddi.policy.engine import READ_ONLY_TOOLS

            if name not in READ_ONLY_TOOLS:
                self.read_cache.clear()
            self._external = set(decision.external)
            try:
                result = self._dispatch(name, args)
            finally:
                self._external = set()
            if self.workspace and name not in READ_ONLY_TOOLS and not name.startswith(("project.map", "project.knowledge", "browser.")):
                from koddi.projectmap import mark_dirty

                # правка или команда могла изменить файлы: карта перечитает их при следующем запросе
                mark_dirty(self.project_id, self.workspace)
            if result.get("ok", True) and name in {
                "file.read",
                "file.read_many",
                "file.read_range",
                "file.read_chunk",
                "directory.list",
                "directory.tree",
                "workspace.inspect",
            }:
                targets = (
                    args.get("paths", []) if name in {"file.read_many", "workspace.inspect"} else [args.get("path", ".")]
                )
                rules = {}
                for target in targets:
                    candidate = Path(str(target))
                    # правила проекта к внешним файлам не относятся
                    if candidate.is_absolute() and not candidate.resolve().is_relative_to(self._require_workspace()):
                        continue
                    for record in scoped_instructions(
                        self._require_workspace(),
                        target,
                        directory=name.startswith("directory."),
                        include_root=False,
                    ):
                        rules[record["path"]] = record
                if rules:
                    result["project_instructions"] = list(rules.values())
            result = redact(result)
            _record_tool(
                self.turn_id, name, args, "ok" if result.get("ok", True) else "error", result
            )
            log_event("INFO", "tool.call", name, {"ok": result.get("ok", True)})
            # внутренние git-вызовы и аудит получают полный вывод, wire - готовый excerpt
            if name != "shell.run":
                result = {
                    key: (
                        tool_output_excerpt(value)
                        if key in {"stdout", "stderr"} and isinstance(value, str)
                        else value
                    )
                    for key, value in result.items()
                }
            return result
        except Exception as exc:
            result = redact({"ok": False, "error": str(exc)})
            _record_tool(self.turn_id, name, args, "error", result)
            log_event("ERROR", "tool.error", name, result)
            return result

    def _require_workspace(self) -> Path:
        if not self.workspace:
            raise ToolError("workspace is required")
        return self.workspace

    def _path(self, value: str) -> Path:
        """Путь проекта или внешний файл, который политика разрешила именно для этого вызова."""
        workspace = self._require_workspace()
        try:
            return WorkspacePaths(workspace).resolve(value)
        except PathAccessError as exc:
            if "escapes workspace" not in str(exc):
                raise
            path = external_path(value)
            if str(path) not in self._external:
                raise
            return path

    def _shown(self, path: Path) -> str:
        workspace = self._require_workspace()
        return path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else str(path)

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == 'attachment.read':
            from koddi.attachments.service import read_attachment

            with db() as conn:
                turn = conn.execute('SELECT chat_id FROM turns WHERE id=?', (self.turn_id,)).fetchone()
            if not turn:
                raise ToolError('attachment access requires an active chat')
            return read_attachment(args['attachment_id'], turn['chat_id'])
        if name == "tool.output_read":
            return read_tool_output(
                args["output_id"],
                self.turn_id,
                offset=args.get("offset", 0),
                max_chars=args.get("max_chars", 6000),
                cancel_event=self.cancel_event,
            )
        if name == "project.instructions":
            return {
                "ok": True,
                "instructions": scoped_instructions(
                    self._require_workspace(), args["path"], directory=args.get("directory", False)
                ),
            }
        if name in {"workspace.open", "workspace.create"}:
            return self.workspace_open_create(args)
        if name == "workspace.snapshot":
            return self.workspace_snapshot(args)
        if name in {"directory.tree", "directory.list"}:
            return self.directory_tree(args)
        if name == "file.read_many":
            return {
                "ok": True,
                "files": [self.file_read({"path": path}) for path in args.get("paths", [])],
            }
        if name == "file.metadata":
            path = self._path(args["path"])
            stat = path.stat()
            return {
                "ok": True,
                "path": args["path"],
                "size_bytes": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
                "is_file": path.is_file(),
            }
        if name == "file.read":
            return self.file_read(args)
        if name == "file.read_range":
            return self.file_read_range(args)
        if name == "file.read_chunk":
            return self.file_read_chunk(args)
        if name == "file.create":
            if self._path(args["path"]).exists():
                raise ToolError("file.create cannot overwrite an existing file")
            return self.file_write_patch(args)
        if name == "patch.apply":
            return self.patch_apply(args)
        if name in {"file.edit", "file.search_replace"}:
            return self.file_edit(args)
        if name == "file.write_patch":
            return self.file_write_patch(args)
        if name == "file.delete":
            return self.file_delete(args)
        if name == "file.move":
            return self.file_move(args)
        if name == "file.copy":
            return self.file_copy(args)
        if name == "file.restore":
            return self.rollback_changes(args.get("change_set_id"))
        if name == "ignore.rules_read":
            return self.ignore_rules_read()
        if name == "search.text":
            return self.search_text(args)
        if name == "search.files":
            return self.search_files(args)
        if name == "context.retrieve":
            return self.context_retrieve(args)
        if name == "docs.lookup":
            from koddi.libdocs import DocsError, lookup

            try:
                return lookup(self._require_workspace(), args)
            except DocsError as exc:
                return {"ok": False, "error": str(exc), "retryable": True}
        if name == "skill.load":
            from koddi.policy.account import local_owner
            from koddi.projectmap.service import project_for_workspace
            from koddi.skills.selector import load

            project_id = self.project_id or (project_for_workspace(self.workspace) if self.workspace else None)
            return load(str(args["name"]), project_id=project_id, owner=local_owner(), root=self.workspace,
                        file=str(args["file"]) if args.get("file") else None)
        if name == "project.knowledge":
            from koddi.knowledge import knowledge_tool
            from koddi.policy.account import local_owner

            return knowledge_tool(self.project_id, self._require_workspace(), args, owner=local_owner())
        if name == "qa.run":
            from koddi.quality.gate import qa_tool

            return qa_tool(self, args)
        if name == "quality.run":
            from koddi.quality.engine import quality_tool

            return quality_tool(self, args)
        if name == "ui.verify":
            from koddi.quality.ui import UiVerifyError, verify

            try:
                return verify(self, args)
            except UiVerifyError as exc:
                return {"ok": False, "error": str(exc)}
        if name == "code.impact":
            from koddi.codeintel.impact import analyze

            return analyze(self, args)
        if name in CODE_TOOLS:
            from koddi.codeintel import tools as code_tools

            return getattr(code_tools, CODE_TOOLS[name])(self, args)
        if name == "patch.preview":
            return self.patch_preview(args)
        if name == "devserver.start":
            return self.devserver_start(args)
        if name in {"format.run", "test.run", "build.run", "lint.run"}:
            return self.shell_run(
                {"command": args["command"], "timeout_seconds": args.get("timeout_seconds", 600)}
            )
        if name == "shell.run":
            return self.shell_run(args)
        if name == "process.list":
            return self.process_list()
        if name == "process.kill":
            return self.process_kill(args)
        if name == "git.status":
            return self.git_command(["git", "status", "--short", "--branch"])
        if name == "git.diff":
            names = self.git_command(["git", "diff", "--name-only", "-z", "--"])
            if not names.get('ok'):
                return names
            paths = WorkspacePaths(self._require_workspace())
            allowed = [name for name in names.get('stdout','').split('\x00') if name and paths.accessible(paths.root / name)]
            return self.git_command(["git", "diff", "--", *allowed]) if allowed else {'ok':True,'stdout':'','stderr':''}
        if name == "git.log":
            return self.git_command(["git", "log", "--oneline", f"-{int(args.get('limit', 20))}"])
        if name == "git.branch":
            return self.git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if name in {"git.checkout", "git.create_branch"}:
            from koddi.projects.gitsafe import branch_error

            branch = str(args.get("branch") or "")
            if error := branch_error(branch):
                return {"ok": False, "error": error, "stdout": "", "stderr": error}
            # «--» в конце: аргумент - только ветка или коммит, не путь; без -f git не перетирает незакоммиченные правки
            command = ["git", "checkout", branch, "--"] if name == "git.checkout" else ["git", "checkout", "-b", branch, "--"]
            return self.git_command(command)
        if name == "git.worktree_create":
            from koddi.projects.gitsafe import ensure_worktree

            workspace = self._require_workspace()
            return ensure_worktree(workspace, (workspace / str(args["path"])).resolve(), args.get("branch"))
        if name == "git.worktree_remove":
            from koddi.projects.gitsafe import remove_worktree

            return remove_worktree(self._require_workspace(), Path(str(args["path"])))
        if name == "git.worktree_list":
            return self.git_command(["git", "worktree", "list", "--porcelain"])
        if name == "git.add":
            from koddi.projects.gitsafe import add

            workspace = self._require_workspace()
            access = WorkspacePaths(workspace)
            result = add(workspace, args.get("paths"), accessible=lambda item: access.accessible(workspace / item))
            self.agent_staged.update(result.get("staged") or [])
            return result
        if name == "git.commit":
            from koddi.projects.gitsafe import commit
            from koddi.quality.gate import turn_changes

            workspace = self._require_workspace()
            access = WorkspacePaths(workspace)
            own = set(self.agent_staged) | set(turn_changes(self.turn_id))
            result = commit(workspace, args["message"], paths=args.get("paths"), body=args.get("body"), own=own,
                            accessible=lambda item: access.accessible(workspace / item))
            if result.get("ok"):
                self.agent_staged.difference_update(result.get("committed") or [])
            return result
        if name == "git.restore_file":
            return self.git_restore_file(args)
        if name in {"test.detect", "build.detect"}:
            return self.detect_commands(name)
        if name == "verification.run":
            return self.verify_changed_files(args)
        if name == "diagnostics.collect":
            return self.diagnostics_collect()
        if name == "document.parse":
            from koddi.documents.service import extract_text

            text, outline = extract_text(_safe_path(self._require_workspace(), args['path']))
            return {
                "ok": True,
                "document": {'text':text,'outline':outline},
            }
        if name == "ocr.run":
            return self.ocr_run(args)
        if name == "vision.inspect":
            return self.vision_inspect(args)
        if name == "secret.scan":
            return secret_scan(args.get("text", ""))
        if name == "diff.review":
            return diff_review(args.get("diff", ""))
        if name == "safety.review":
            return safety_review(args.get("diff", ""), args.get("command", ""))
        if name == "test_gap.analysis":
            return self.unsupported(name)
        if name == "web.search":
            return _run_async(self._network(web_search(args["query"], int(args.get("limit", 10)))))
        if name == "web.fetch":
            from koddi.policy.grants import private_network_allowed

            return _run_async(self._network(web_fetch(args["url"], allow_private=private_network_allowed(self.project_id))))
        if name.startswith("browser."):
            return self.browser_tool(name, args)
        if name == "computer.status":
            from koddi.computer_use.service import status

            return status()
        if name == "computer.use":
            from koddi.computer_use.service import ComputerSession

            if self.computer_session is None:
                self.computer_session = ComputerSession(self.turn_id, self.project_id, self.cancel_event)
            return self.computer_session.run(args)
        if name == "task.history":
            from koddi.agents.task_context import read_completed_context

            return read_completed_context(self.turn_id, args.get("source_turn_id"), action_id=args.get("action_id"), message_id=args.get("message_id"), before_action_id=args.get("before_action_id"), offset=args.get("offset", 0), max_chars=args.get("max_chars", 6000))
        if name == "workspace.inspect":
            return self.workspace_inspect(args)
        if name == "project.map":
            return self.project_map(args)
        if name == "project.map_update":
            return self.project_map_update(args)
        if name == "memory.get":
            return self.memory_get(args)
        if name == "memory.set":
            return self.memory_set(args)
        if name == "context.compact":
            return {"ok": True, "summary": str(args.get("content", ""))[:4000]}
        if name.startswith("task.") or name.startswith("orchestrator."):
            return self.task_tool(name, args)
        if name == "automation.create":
            return {
                "ok": True,
                "automation": create_automation(
                    args["name"], args.get("action", {}), args.get("schedule", "manual")
                ),
            }
        if name == "automation.run":
            return run_automation(int(args["automation_id"]))
        if name == "skill.create":
            return {
                "ok": True,
                "skill": create_skill(
                    args["name"], args.get("description", ""), args.get("workflow", {})
                ),
            }
        if name == "skill.apply":
            return apply_skill(int(args["skill_id"]), args.get("task", ""))
        if name == "app.update_check":
            return update_check()
        if name == "app.i18n_check":
            return check_locales()
        if name == "app.theme_check":
            return self.unsupported(name)
        if name == "app.diagnostics":
            return self.diagnostics_collect()
        if name == "mcp.tools_list":
            return mcp_tools_list()
        if name == "mcp.tool_call":
            return mcp_tool_call_sync(args["server"], args["name"], args.get("arguments", {}))
        if name == "ide.open_file":
            return open_file(
                str(_safe_path(self._require_workspace(), args["path"])), args.get("line")
            )
        if name == "ide.open_diff":
            return open_diff(args.get("path"))
        if name == "terminal.open":
            return terminal_open(str(self.workspace) if self.workspace else None)
        if name == "devserver.detect":
            return self.detect_devserver()
        if name == "devserver.stop":
            return self.devserver_stop(args)
        if name == "issue.import":
            return _run_async(issue_import(args["url"]))
        if name == "issue.update":
            return issue_update(args["issue_id"], args["body"])
        if name == "logs.list":
            return {"ok": True, "logs": list_logs(int(args.get("limit", 100)))}
        if name == "changes.latest":
            return {"ok": True, "change_set": latest_change_set(self.project_id, scoped=True)}
        if name == "changes.rollback":
            return self.rollback_changes()
        raise ToolError(f"unknown tool {name}")

    @staticmethod
    def unsupported(name: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": "not_implemented",
            "error": f"{name} has no executable adapter; no action was performed",
        }

    def workspace_open_create(self, args: dict[str, Any]) -> dict[str, Any]:
        from koddi.projects import add_project

        path = Path(args["path"]).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "project": add_project(str(path))}

    def workspace_snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        from koddi.storage.db import runtime_path

        backup_root = Path(tempfile.mkdtemp(prefix="workspace-", dir=runtime_path("backups")))
        for source in WorkspacePaths(workspace).files():
            destination = backup_root / source.relative_to(workspace)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        with db() as conn:
            conn.execute(
                "INSERT INTO backups(project_id, path, backup_path, reason) VALUES (?, ?, ?, ?)",
                (
                    self.project_id,
                    str(workspace),
                    str(backup_root),
                    args.get("reason", "manual snapshot"),
                ),
            )
        return {"ok": True, "backup_path": str(backup_root)}

    def directory_tree(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        root = _safe_path(workspace, args.get("path", "."))
        max_depth = int(args.get("max_depth", 3))
        limit = int(args.get("limit", 200))
        items: list[dict[str, Any]] = []
        paths = WorkspacePaths(workspace)
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(
                name for name in dirs if not paths.is_noise(name) and paths.accessible(Path(current) / name)
            )
            files = sorted(name for name in files if paths.accessible(Path(current) / name))
            rel_dir = Path(current).relative_to(root)
            depth = len(rel_dir.parts)
            children = sorted(dirs + files)
            if depth >= max_depth:
                dirs[:] = []
                continue
            for name in children:
                path = Path(current) / name
                if len(items) >= limit:
                    items.sort(key=lambda item: item["path"])
                    return {"ok": True, "root": str(root), "truncated": True, "items": items}
                items.append(
                    {
                        "path": str(path.relative_to(workspace)),
                        "type": "dir" if path.is_dir() else "file",
                        "size": path.stat().st_size if path.is_file() else None,
                    }
                )
        items.sort(key=lambda item: item["path"])
        return {"ok": True, "root": str(root), "truncated": False, "items": items}

    def workspace_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        tree = self.directory_tree({"max_depth": 2, "limit": 160})
        outlines = []
        for path in args.get("paths", []):
            if self.cancel_event.is_set():
                raise ToolError("operation cancelled")
            outlines.append({"path": path, **self.code_outline({"path": path})})
        return {"ok": True, "tree": tree, "test_commands": self.detect_commands("test.detect"),
                "outlines": outlines, "note": "Commands are suggestions; no checks were executed."}

    def project_map(self, args: dict[str, Any]) -> dict[str, Any]:
        from koddi.policy.account import local_owner
        from koddi.projectmap import MapError, map_tool

        try:
            return map_tool(self.project_id, self._require_workspace(), args, owner=local_owner(), cancel=self.cancel_event)
        except MapError as exc:
            return {"ok": False, "error": str(exc)}

    def project_map_update(self, args: dict[str, Any]) -> dict[str, Any]:
        from koddi.policy.account import local_owner
        from koddi.projectmap import MapError, map_update_tool

        chat_id = None
        if self.turn_id is not None:
            with db() as conn:
                row = conn.execute("SELECT chat_id FROM turns WHERE id=?", (self.turn_id,)).fetchone()
            chat_id = row["chat_id"] if row else None
        try:
            return map_update_tool(self.project_id, self._require_workspace(), args, owner=local_owner(), chat_id=chat_id)
        except MapError as exc:
            return {"ok": False, "error": str(exc), "retryable": True}

    def file_read(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._path(args["path"])
        max_bytes = max(
            1,
            min(
                int(args.get("max_bytes") or settings.max_file_read_bytes),
                settings.max_file_read_bytes,
            ),
        )
        data, digest = self.read_cache.read(path, 0, max_bytes + 1)
        truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": self._shown(path),
            "truncated": truncated,
            "content": text,
            "sha256": digest,
        }

    def file_read_range(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._path(args["path"])
        start = max(int(args["start"]), 1)
        end = max(int(args["end"]), start)
        if path.stat().st_size > settings.max_file_read_bytes:
            raise ToolError(
                "Файл слишком большой для чтения диапазоном; используйте file.read_chunk"
            )
        data, digest = self.read_cache.read(path, 0, settings.max_file_read_bytes + 1)
        lines = data.decode("utf-8", errors="replace").splitlines()
        selected = lines[start - 1 : end]
        return {
            "ok": True,
            "path": self._shown(path),
            "start": start,
            "end": end,
            "content": "\n".join(selected),
            "sha256": digest,
        }

    def file_read_chunk(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._path(args["path"])
        offset = max(0, int(args.get("offset", 0)))
        size = max(1, min(int(args.get("size", 65536)), settings.max_file_read_bytes))
        data, digest = self.read_cache.read(path, offset, size)
        return {
            "ok": True,
            "path": self._shown(path),
            "offset": offset,
            "content": data.decode("utf-8", errors="replace"),
            "chunk_sha256": digest,
        }

    def file_write_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._path(args["path"])
        before = self._read_editable(path)
        after = str(args["content"])
        relative = self._shown(path)
        result = self._commit_files(
            {relative: (before, after)}, args.get("description", f"Update {path.name}")
        )
        return {**result, "path": relative}

    @staticmethod
    def _read_editable(path: Path) -> str | None:
        if not path.exists():
            return None
        data = path.read_bytes()
        if b"\x00" in data:
            raise ToolError("binary files cannot be changed by text tools")
        return data.decode("utf-8")

    @staticmethod
    def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".koddi-edit-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _commit_files(
        self, changes: dict[str, tuple[str | None, str | None]], description: str
    ) -> dict[str, Any]:
        from koddi.changes.service import sha256_text, unified_diff

        workspace = self._require_workspace()
        canonical = {}
        for relative, (before, after) in changes.items():
            path = self._path(relative)
            if path in canonical:
                raise ToolError("duplicate normalized patch path")
            # конфиг и хуки git исполняют команды при следующем git status или commit - мимо подтверждения
            if any(part.lower() in {".git", "git~1"} for part in path.parts):
                raise ToolError("files inside .git are not changed by agent tools")
            if self._read_editable(path) != before:
                raise ToolError("file changed since edit preparation")
            if after is not None and "\x00" in after:
                raise ToolError("text tools cannot write binary content")
            if before != after:
                canonical[path] = (before, after, path.stat().st_mode if path.exists() else None)
        if not canonical:
            return {"ok": False, "error_code": "no_changes", "error": "no file content changed"}
        outside = [path for path in canonical if not path.is_relative_to(workspace)]
        if outside and len(canonical) > 1:
            raise ToolError("files outside the project are changed one at a time")
        # файл вне проекта: корень отката - его папка, запись без project_id, путь абсолютный,
        # так существующий откат находит его по снимку и проверяет так же строго
        root = outside[0].parent if outside else workspace
        project_id = None if outside else self.project_id
        applied = []
        items = []
        from koddi.changes import recovery
        from koddi.policy.account import local_owner

        if not outside and self.turn_id is not None:
            from koddi.changes.checkpoints import ensure_captured

            ensure_captured(self.turn_id, workspace)
        operation = recovery.prepare(
            owner_id=local_owner(), project_id=project_id, turn_id=None if outside else self.turn_id, root=root,
            changes={path:(before.encode('utf-8') if before is not None else None,after.encode('utf-8') if after is not None else None) for path,(before,after,_mode) in canonical.items()},
        )
        try:
            with db() as conn:
                diffs = [
                    unified_diff(self._shown(path), before, after)
                    for path, (before, after, mode) in canonical.items()
                ]
                cursor = conn.execute(
                    "INSERT INTO change_sets(project_id, turn_id, description, diff, owner_id) VALUES (?, ?, ?, ?, ?)",
                    (project_id, self.turn_id, description, "".join(diffs), local_owner()),
                )
                change_set_id = int(cursor.lastrowid)
                for path, (before, after, mode) in canonical.items():
                    if self.cancel_event.is_set():
                        raise ToolError("operation cancelled before write")
                    self._path(str(path))
                    if self._read_editable(path) != before:
                        raise ToolError("file changed since edit preparation")
                    if after is None:
                        path.unlink()
                    else:
                        self._atomic_write(path, after.encode("utf-8"), mode)
                    applied.append(path)
                    relative = self._shown(path)

                    # откат читает файл с универсальными переводами строк
                    def normalize(text):
                        return (
                            text.replace("\r\n", "\n").replace("\r", "\n")
                            if text is not None
                            else None
                        )

                    conn.execute(
                        "INSERT INTO file_changes(change_set_id, path, before_hash, after_hash, before_text, after_text) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            change_set_id,
                            relative,
                            sha256_text(normalize(before)),
                            sha256_text(normalize(after)),
                            before,
                            after,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO snapshots(change_set_id, path, content, existed) VALUES (?, ?, ?, ?)",
                        (
                            change_set_id,
                            str(path),
                            before.encode("utf-8") if before is not None else None,
                            int(before is not None),
                        ),
                    )
                    added, removed = _line_changes(before, after)
                    items.append({"path": relative, "lines_added": added, "lines_removed": removed})
        except Exception:
            for path in reversed(applied):
                before, after, mode = canonical[path]
                if self._read_editable(path) != after:
                    continue
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    self._atomic_write(path, before.encode("utf-8"), mode)
            try:
                recovery.restore(operation, expected_hashes={path.relative_to(root).as_posix(): recovery._hash(path.read_bytes() if path.exists() else None) for path in canonical})
            except (ValueError, OSError):
                pass
            raise
        recovery.finish(operation)
        check_paths = [path.relative_to(workspace).as_posix() for path, (_, after, _) in canonical.items()
                       if after is not None and not outside and path.suffix.lower() in {".py", ".json"}
                       and path.stat().st_size <= settings.max_file_read_bytes]
        verification = self.verify_changed_files({"paths": check_paths}) if check_paths else {"checks": []}
        for check in verification["checks"]:
            if check["ok"]:
                self.pending_diagnostics.pop(check["path"], None)
            else:
                self.pending_diagnostics[check["path"]] = check
        for path, (_, after, _) in canonical.items():
            if after is None:
                self.pending_diagnostics.pop(self._shown(path), None)
        return {
            "ok": True,
            **({"backup": "Исходный файл сохранён, изменение можно откатить", "outside_project": True} if outside else {}),
            "checks": verification["checks"],
            "verification_scope": "python_syntax_and_json_parse_only",

            "change_set_id": change_set_id,
            "files": items,
            "lines_added": sum(item["lines_added"] for item in items),
            "lines_removed": sum(item["lines_removed"] for item in items),
        }

    def file_edit(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._path(args["path"])
        before = self._read_editable(path)
        if before is None:
            raise ToolError("edit target does not exist")
        if (
            args.get("expected_sha256")
            and hashlib.sha256(before.encode("utf-8")).hexdigest()
            != args["expected_sha256"].lower()
        ):
            raise ToolError("file hash differs from expected_sha256")
        after = before
        replacements = 0
        newline = "\r\n" if "\r\n" in before else "\n"
        for edit in args.get("edits", [args]):
            old = edit["old_text"].replace("\r\n", "\n").replace("\n", newline)
            new = edit["new_text"].replace("\r\n", "\n").replace("\n", newline)
            count = after.count(old)
            if not old or count == 0:
                raise ToolError("old_text was not found; no files changed")
            if count > 1 and not edit.get("replace_all", False):
                raise ToolError(
                    f"old_text is ambiguous ({count} matches); use replace_all explicitly"
                )
            after = after.replace(old, new, -1 if edit.get("replace_all", False) else 1)
            replacements += count if edit.get("replace_all", False) else 1
        relative = self._shown(path)
        result = self._commit_files(
            {relative: (before, after)}, args.get("description", "Exact text edit")
        )
        return {
            **result,
            "path": relative,
            "replacements": replacements,
            "sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
        }

    def _prepare_patch(self, args: dict[str, Any]):
        workspace = self._require_workspace()
        patches = parse_patch(args["patch"])

        def read(relative):
            return self._read_editable(_safe_path(workspace, relative))

        return prepare_patch(patches, read)

    def patch_apply(self, args: dict[str, Any]) -> dict[str, Any]:
        if "patch" not in args:
            return self.file_write_patch(args)
        return self._commit_files(self._prepare_patch(args), args.get("description", "Apply patch"))

    def rollback_changes(self, change_set_id: int | None = None) -> dict[str, Any]:
        from koddi.changes.service import rollback_change_set

        workspace = self._require_workspace()
        if change_set_id is None:
            latest = latest_change_set(self.project_id, scoped=True)
            if not latest:
                return {"ok": False, "rolled_back": False, "reason": "no change sets"}
            change_set_id = int(latest["id"])
        from koddi.policy.account import local_owner, owns

        with db() as conn:
            change = conn.execute("SELECT owner_id,project_id FROM change_sets WHERE id=?", (change_set_id,)).fetchone()
            if not change or not owns(change["owner_id"], local_owner()) or change["project_id"] != self.project_id:
                return {"ok": False, "rolled_back": False, "reason": "change set not found"}
            snapshots = conn.execute(
                "SELECT path FROM snapshots WHERE change_set_id=?", (change_set_id,)
            ).fetchall()
            for snapshot in snapshots:
                _safe_path(workspace, snapshot["path"])
            changes = conn.execute(
                "SELECT path, after_text FROM file_changes WHERE change_set_id=?", (change_set_id,)
            ).fetchall()
            for change in changes:
                current = self._read_editable(_safe_path(workspace, change["path"]))
                if current != change["after_text"]:
                    return {
                        "ok": False,
                        "rolled_back": False,
                        "reason": "file conflicts",
                        "conflicts": [change["path"]],
                    }
        result = rollback_change_set(change_set_id)
        return {"ok": result.get("rolled_back", False), **result}

    def file_delete(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        path = _safe_path(workspace, args["path"])
        before = self._read_editable(path)
        if before is None:
            raise ToolError("delete target does not exist")
        relative = path.relative_to(workspace).as_posix()
        return {
            **self._commit_files({relative: (before, None)}, f"Delete {path.name}"),
            "path": relative,
        }

    def file_move(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        src = _safe_path(workspace, args["source"])
        dst = _safe_path(workspace, args["destination"])
        content = self._read_editable(src)
        if content is None or dst.exists() or src == dst:
            raise ToolError("move requires an existing source and a new destination")
        return self._commit_files(
            {
                src.relative_to(workspace).as_posix(): (content, None),
                dst.relative_to(workspace).as_posix(): (None, content),
            },
            f"Move {src.name}",
        )

    def file_copy(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        src = _safe_path(workspace, args["source"])
        dst = _safe_path(workspace, args["destination"])
        content = self._read_editable(src)
        if content is None or dst.exists():
            raise ToolError("copy requires an existing source and a new destination")
        return self._commit_files(
            {dst.relative_to(workspace).as_posix(): (None, content)}, f"Copy {src.name}"
        )

    def ignore_rules_read(self) -> dict[str, Any]:
        workspace = self._require_workspace()
        rules = {}
        for name in [".gitignore", ".ignore", ".koddiignore"]:
            path = WorkspacePaths(workspace).resolve(name)
            if path.exists():
                rules[name] = path.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "rules": rules}

    @staticmethod
    def _search_candidates(workspace: Path, pattern: str):
        access = WorkspacePaths(workspace)
        parts = pattern.split("/")
        prefix = []
        for part in parts:
            if not part or part in {".", ".."} or "\\" in part or ":" in part:
                prefix = []
                break
            if any(char in part for char in "*?[{"):
                break
            prefix.append(part)
        # шаблон имени ищет во всех папках; сузить можно только путь до первого glob-компонента
        if not prefix or len(parts) == 1:
            yield from access.files()
            return
        exact = len(prefix) == len(parts)
        if any(access.is_noise(part) for part in (prefix[:-1] if exact else prefix)):
            return
        try:
            scope = access.resolve("/".join(prefix), check_ignore=True)
            # fnmatchcase сохраняет регистр даже на нечувствительной к нему файловой системе
            current = workspace
            for part in prefix:
                with os.scandir(current) as entries:
                    if not any(entry.name == part for entry in entries):
                        return
                current /= part
            if exact:
                if scope.is_file():
                    yield scope
            elif scope.is_dir():
                yield from access.files(scope)
        except (ValueError, OSError):
            return

    def _search_paths(self, deadline: float, pattern: str = "*") -> list[Path]:
        workspace = self._require_workspace()
        paths = []
        for path in self._search_candidates(workspace, pattern):
            if self.cancel_event.is_set():
                raise ToolError("search cancelled")
            if time.monotonic() > deadline:
                raise ToolError("Поиск остановлен: список файлов требует слишком много времени")
            if len(paths) >= MAX_SEARCH_CANDIDATE_PATHS:
                raise ToolError(
                    f"Поиск остановлен: превышен лимит {MAX_SEARCH_CANDIDATE_PATHS} файлов-кандидатов"
                )
            paths.append(path)
        paths.sort(key=lambda path: path.relative_to(workspace).as_posix())
        if self.cancel_event.is_set():
            raise ToolError("search cancelled")
        if time.monotonic() > deadline:
            raise ToolError("Поиск остановлен: сортировка файлов требует слишком много времени")
        return paths

    def search_text(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        query = args["query"]
        if len(query) > 1000:
            raise ToolError("Слишком длинное выражение поиска")
        try:
            pattern = regex.compile(query)
        except regex.error as exc:
            raise ToolError("Некорректное выражение поиска") from exc
        deadline = time.monotonic() + 10
        matches = []
        skipped = []
        for path in self._search_paths(deadline, args.get("glob", "*")):
            if self.cancel_event.is_set():
                raise ToolError("search cancelled")
            if time.monotonic() > deadline:
                return self._search_result(matches, skipped, True)
            relative = path.relative_to(workspace).as_posix()
            if not self._matches_glob(relative, args.get("glob", "*")):
                continue
            if path.stat().st_size > settings.max_file_read_bytes:
                skipped.append(relative)
                continue
            data = path.read_bytes()
            if b"\x00" in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(relative)
                continue
            for index, line in enumerate(text.splitlines(), 1):
                if self.cancel_event.is_set():
                    raise ToolError("search cancelled")
                if time.monotonic() > deadline:
                    return self._search_result(matches, skipped, True)
                try:
                    matched = pattern.search(line, timeout=0.025, concurrent=True)
                except TimeoutError as exc:
                    raise ToolError("Поиск остановлен: выражение требует слишком много времени") from exc
                if matched:
                    matches.append(
                        {"path": relative, "line": index, "text": tool_output_excerpt(line, 2000)}
                    )
                    if len(matches) >= 500:
                        return self._search_result(matches, skipped, True)
            if self.cancel_event.is_set():
                raise ToolError("search cancelled")
        return self._search_result(matches, skipped, False)

    @staticmethod
    def _search_result(matches, skipped, truncated):
        matches = sorted(matches, key=lambda match: (match["path"], match["line"]))
        return {
            "ok": True,
            "matches": matches,
            "skipped": sorted(skipped),
            "truncated": truncated,
            "stdout": "\n".join(f"{m['path']}:{m['line']}:{m['text']}" for m in matches),
            "stderr": "",
        }

    @staticmethod
    def _matches_glob(relative: str, pattern: str) -> bool:
        brace = re.search(r"\{([^{}]+)\}", pattern)
        patterns = [pattern]
        if brace:
            patterns = [
                pattern[: brace.start()] + option + pattern[brace.end() :]
                for option in brace[1].split(",")
            ]
        return any(
            fnmatch.fnmatchcase(relative, glob)
            or fnmatch.fnmatchcase(Path(relative).name, glob)
            or (glob.startswith("**/") and fnmatch.fnmatchcase(relative, glob[3:]))
            for glob in patterns
        )

    def search_files(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        query = str(args.get("query", "*"))
        matches = []
        deadline = time.monotonic() + 10
        for path in self._search_paths(deadline, query):
            if self.cancel_event.is_set():
                raise ToolError("search cancelled")
            if time.monotonic() > deadline:
                return {"ok": True, "files": matches, "truncated": True}
            relative = path.relative_to(workspace).as_posix()
            if self._matches_glob(relative, query):
                matches.append(relative)
                if len(matches) >= 500:
                    return {"ok": True, "files": sorted(matches), "truncated": True}
        return {"ok": True, "files": sorted(matches), "truncated": False}

    def code_outline(self, args: dict[str, Any]) -> dict[str, Any]:
        file = self.file_read({"path": args["path"], "max_bytes": 200000})
        content = file["content"]
        if Path(args["path"]).suffix == ".py" and not file["truncated"]:
            import ast

            try:
                tree = ast.parse(content)
                outline = []
                lines = content.splitlines()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        outline.append({"line": node.lineno, "end_line": node.end_lineno,
                                        "kind": type(node).__name__, "name": node.name,
                                        "text": lines[node.lineno - 1].strip()[:200]})
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        outline.append({"line": node.lineno, "kind": "import", "text": ast.get_source_segment(content, node)[:200]})
                return {"ok": True, "parser": "python_ast", "outline": sorted(outline, key=lambda item: item["line"])[:200], "truncated": len(outline) > 200}
            except SyntaxError as exc:
                return {"ok": False, "parser": "python_ast", "error": str(exc)}
        outline = []
        for idx, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(
                ("def ", "class ", "function ", "public ", "private ", "internal ", "async ", "export ", "interface ", "type ", "import ", "from ")
            ):
                outline.append({"line": idx, "text": stripped[:200]})
        return {"ok": True, "parser": "line_heuristic", "outline": outline[:200], "truncated": file["truncated"] or len(outline) > 200}

    def patch_preview(self, args: dict[str, Any]) -> dict[str, Any]:
        from koddi.changes.service import unified_diff

        workspace = self._require_workspace()
        if "patch" in args:
            changes = self._prepare_patch(args)
            return {
                "ok": True,
                "diff": "".join(
                    unified_diff(path, before, after) for path, (before, after) in changes.items()
                ),
                "paths": list(changes),
            }
        path = _safe_path(workspace, args["path"])
        before = self._read_editable(path)
        return {
            "ok": True,
            "diff": unified_diff(str(path.relative_to(workspace)), before, args.get("content", "")),
        }

    def context_retrieve(self, args: dict[str, Any]) -> dict[str, Any]:
        from koddi.codeintel.impact import changed_files
        from koddi.context.retrieve import RetrieveError, retrieve
        from koddi.policy.account import local_owner
        from koddi.projectmap.service import MapError, project_for_workspace

        workspace = self._require_workspace()
        project_id = self.project_id or project_for_workspace(workspace)
        if project_id is None:
            return {"ok": False, "error": "context.retrieve needs a project opened in Dokky; use search.text"}
        paths = WorkspacePaths(workspace)
        focus = [paths.resolve(str(item), check_ignore=True).relative_to(workspace).as_posix() for item in args.get("paths") or []]
        try:
            return retrieve(
                project_id, workspace, local_owner(), str(args["query"]), focus=focus or changed_files(workspace, limit=20),
                max_chars=int(args.get("max_chars") or 12000), limit=int(args.get("limit") or 12),
                include_tests=args.get("include_tests"), cancel=self.cancel_event,
            )
        except (RetrieveError, MapError) as exc:
            return {"ok": False, "error": str(exc), "retryable": True}

    def shell_run(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        timeout = int(args.get("timeout_seconds") or settings.command_timeout_seconds)
        return self.run_process(shell_argv(args["command"]), workspace, timeout)

    def run_process(self, argv: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
        """Процесс без shell с остановкой всего дерева по отмене хода или тайм-ауту; вывод ограничен."""
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL if os.name != "nt" else None,
            creationflags=HIDDEN_SUBPROCESS,
            # своя группа процессов, чтобы остановка снимала и дочерние процессы команды
            start_new_session=os.name != "nt",
            encoding="utf-8",
            errors="replace",
        )
        buffers = {"stdout": _ToolOutputBuffer(20000), "stderr": _ToolOutputBuffer(12000)}
        output_locks = {key: threading.Lock() for key in buffers}

        def drain(stream, key):
            while chunk := stream.read(4096):
                with output_locks[key]:
                    buffers[key].append(chunk)
            stream.close()

        readers = [
            threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()

        def stop_tree():
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=HIDDEN_SUBPROCESS,
                    timeout=10,
                    check=False,
                )
            else:
                _signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    proc.kill()
                else:
                    _signal_group(proc, signal.SIGKILL)
                proc.wait(timeout=2)

        cancelled = timed_out = False
        started = time.monotonic()
        while proc.poll() is None:
            if self.cancel_event.is_set():
                cancelled = True
                stop_tree()
                break
            if time.monotonic() - started >= timeout:
                timed_out = True
                stop_tree()
                break
            time.sleep(0.05)
        for reader in readers:
            reader.join(timeout=2)
        output = {}
        for key, buffer in buffers.items():
            with output_locks[key]:
                output[key] = buffer.finish()
        return {
            "ok": proc.returncode == 0 and not cancelled and not timed_out,
            "returncode": proc.returncode,
            **output,
            **({"output_incomplete": True} if any(reader.is_alive() for reader in readers) else {}),
            **({"cancelled": True, "error": "operation cancelled"} if cancelled else {}),
            **(
                {"timed_out": True, "error": f"command timed out after {timeout} seconds"}
                if timed_out
                else {}
            ),
        }

    def process_list(self) -> dict[str, Any]:
        if os.name != "nt":
            proc = subprocess.run(
                ["ps", "-axo", "pid=,comm="],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            processes = []
            for line in proc.stdout.splitlines():
                pid, _, name = line.strip().partition(" ")
                if pid.isdigit() and name.strip():
                    processes.append({"Id": int(pid), "ProcessName": Path(name.strip()).name})
            return {
                "ok": proc.returncode == 0,
                "processes_json": json.dumps(processes),
                "stderr": proc.stderr,
            }
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process | Select-Object Id,ProcessName | ConvertTo-Json",
            ],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=HIDDEN_SUBPROCESS,
        )
        return {"ok": proc.returncode == 0, "processes_json": proc.stdout, "stderr": proc.stderr}

    def process_kill(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            pid = int(args["pid"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "stderr": "pid must be an integer"}
        # 0 и отрицательные pid в kill() - группы процессов (-1 - все процессы пользователя), 1 и 4 - системные;
        # саму Докки и её родителя агент не завершает
        own = {os.getpid(), os.getppid()} | ({os.getpgrp()} if os.name != "nt" else set())
        if pid <= 1 or pid > 2**31 - 1 or (os.name == "nt" and pid == 4) or pid in own:
            return {"ok": False, "stderr": f"process {pid} cannot be stopped by the agent"}
        if os.name != "nt":
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                return {"ok": False, "stderr": str(exc)}
            return {"ok": True, "stderr": ""}
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid}"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=HIDDEN_SUBPROCESS,
        )
        return {"ok": proc.returncode == 0, "stderr": proc.stderr}

    def git_command(self, command: list[str]) -> dict[str, Any]:
        from koddi.projects.gitsafe import SAFE_CONFIG, git_env

        workspace = self._require_workspace()
        proc = subprocess.run(
            [command[0], *SAFE_CONFIG, *command[1:]],
            cwd=workspace,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
            env=git_env(),
            creationflags=HIDDEN_SUBPROCESS,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }

    def git_restore_file(self, args: dict[str, Any]) -> dict[str, Any]:
        """Версия файла из индекса пишется как обычная правка агента: она в журнале и откатывается.
        `git restore` стирал бы незакоммиченные правки человека безвозвратно, а «.» - во всём дереве."""
        from koddi.projects.gitsafe import index_text

        workspace = self._require_workspace()
        try:
            relative, after = index_text(workspace, str(args.get("path") or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        path = self._path(relative)
        before = self._read_editable(path)
        if before == after:
            return {"ok": True, "path": relative, "unchanged": True}
        return {**self._commit_files({relative: (before, after)}, f"Restore {path.name} from git"), "path": relative}

    def detect_commands(self, name: str) -> dict[str, Any]:
        workspace = self._require_workspace()
        commands = []
        if (workspace / "pyproject.toml").exists():
            # на macOS и Linux команды `python` часто нет, есть только python3
            python = "python" if os.name == "nt" or shutil.which("python") else "python3"
            commands.extend([f"{python} -m pytest", f"{python} -m ruff check ."])
        if (workspace / "package.json").exists():
            commands.extend(["npm test", "npm run build"])
        if (workspace / "Cargo.toml").exists():
            commands.extend(["cargo test", "cargo build"])
        return {"ok": True, "kind": name, "commands": commands}

    def verify_changed_files(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        checks: list[dict[str, Any]] = []
        for relative in args.get("paths", []):
            path = _safe_path(workspace, str(relative))
            check: dict[str, Any] = {"path": str(relative), "ok": True}
            try:
                if not path.is_file():
                    raise ToolError("changed file does not exist")
                if path.stat().st_size > settings.max_file_read_bytes:
                    raise ToolError("Файл превышает лимит локальной проверки")
                text = path.read_text(encoding="utf-8-sig")
                if path.suffix.lower() == ".py":
                    compile(text, str(path), "exec")
                    check["check"] = "python_compile"
                elif path.suffix.lower() == ".json":
                    json.loads(text)
                    check["check"] = "json_parse"
                else:
                    check["check"] = "readable_file"
                    check["size_bytes"] = path.stat().st_size
            except Exception as exc:
                check.update({"ok": False, "error": str(exc)})
            checks.append(check)
        ok = bool(checks) and all(check["ok"] for check in checks)
        return {
            "ok": ok,
            "checks": checks,
            "error": None if ok else "verification failed",
        }

    def completion_diagnostics(self) -> list[dict]:
        for relative in list(self.pending_diagnostics):
            path = _safe_path(self._require_workspace(), relative)
            if not path.exists():
                self.pending_diagnostics.pop(relative, None)
                continue
            check = self.verify_changed_files({"paths": [relative]})["checks"][0]
            if check["ok"]:
                self.pending_diagnostics.pop(relative, None)
            else:
                self.pending_diagnostics[relative] = check
        return list(self.pending_diagnostics.values())

    def detect_devserver(self) -> dict[str, Any]:
        workspace = self._require_workspace()
        hints = []
        if (workspace / "package.json").exists():
            hints.extend(["npm run dev", "npm start"])
        if (workspace / "vite.config.ts").exists() or (workspace / "vite.config.js").exists():
            hints.append("http://127.0.0.1:5173")
        from koddi.policy.account import local_owner

        return {"ok": True, "hints": hints, "running": _running_servers(local_owner(), workspace)}

    def browser_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Встроенный браузер в правой панели: страница проекта, свой localhost или публичный сайт."""
        from koddi.policy.grants import private_network_allowed

        action = name.split(".", 1)[1]
        if action == "open":
            workspace = self.workspace
            target = str(args["url"]).strip()
            if "://" not in target and not target.lower().startswith("file:"):
                workspace = self._require_workspace()
                try:
                    target = file_url_for(target, workspace)
                except (ValueError, OSError) as exc:
                    raise ToolError(f"файл вне проекта или недоступен: {exc}") from exc
            loopback = is_loopback_url(target)
            if loopback and not local_preview_allowed(self.project_id, workspace):
                raise ToolError("localhost закрыт: сначала запусти сервер через devserver.start в этом проекте")
            return browser_open(
                target, self.project_id, allow_private=private_network_allowed(self.project_id),
                local_preview=loopback, workspace=workspace, replace=True,
            )
        payload = {key: value for key, value in args.items() if key != "session_id"}
        result = browser_event(int(args.get("session_id") or 0) or None, action, payload)
        if action == "screenshot" and result.get("ok"):
            result.update(
                self.attach_image(Path(result.pop("path")), "browser-page.png"),
                vision_transport="not_confirmed",
                note="проверяй страницу через browser.snapshot, картинка нужна пользователю",
            )
        return result

    def attach_image(self, path: Path, name: str) -> dict[str, Any]:
        """Картинка во вложения чата хода: её видит пользователь, модели она не передаётся."""
        from koddi.attachments.service import store_attachment

        with db() as conn:
            turn = conn.execute("SELECT chat_id FROM turns WHERE id=?", (self.turn_id,)).fetchone()
        with path.open("rb") as handle:
            attachment = store_attachment(turn["chat_id"] if turn else None, name, handle)
        return {"attachment_id": attachment["id"], "image_url": f"/api/attachments/{attachment['id']}/content"}

    def devserver_start(self, args: dict[str, Any]) -> dict[str, Any]:
        """Долгая команда в фоне: сервер разработки, наблюдатель. Возвращает первые строки вывода и pid для остановки."""
        workspace = self._require_workspace()
        from koddi.policy.account import local_owner

        owner_id = local_owner()
        wait = min(60.0, max(0.0, float(args.get("wait_seconds", 3))))
        directory = runtime_path("devservers")
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / f"{int(time.time() * 1000)}.log"
        log = open(log_path, "wb")
        try:
            proc = subprocess.Popen(
                shell_argv(args["command"]),
                cwd=workspace,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # без этого питоновский сервер держит вывод в буфере и агент не видит строку запуска
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                creationflags=(HIDDEN_SUBPROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        finally:
            log.close()
        with _DEV_SERVERS_LOCK:
            _DEV_SERVERS[proc.pid] = {"proc": proc, "command": str(args["command"]), "log": log_path, "started": time.time(),
                                      "owner_id": owner_id, "workspace": str(workspace)}
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and proc.poll() is None and not self.cancel_event.is_set():
            time.sleep(0.1)
        output = _tail(log_path)
        return {
            "ok": proc.poll() in (None, 0),
            "pid": proc.pid,
            "running": proc.poll() is None,
            "returncode": proc.poll(),
            "output": output,
            "hint": "Команда работает в фоне; остановить: devserver.stop с этим pid.",
        }

    def devserver_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        pid = args.get("pid")
        from koddi.policy.account import local_owner

        owner_id = local_owner()
        workspace = str(self._require_workspace())
        with _DEV_SERVERS_LOCK:
            targets = [int(pid)] if pid is not None else list(_DEV_SERVERS)
        stopped = []
        for target in targets:
            with _DEV_SERVERS_LOCK:
                item = _DEV_SERVERS.get(target)
                if item is None or item.get("owner_id") != owner_id or item.get("workspace") != workspace:
                    continue
                item = _DEV_SERVERS.pop(target)
            _stop_process_tree(item["proc"])
            stopped.append({"pid": target, "command": item["command"], "output": _tail(item["log"], 2000)})
        if pid is not None and not stopped:
            return {"ok": False, "error": f"фоновой команды с pid {pid} нет"}
        return {"ok": True, "stopped": stopped}

    def diagnostics_collect(self) -> dict[str, Any]:
        return {
            "ok": True,
            "workspace": str(self.workspace) if self.workspace else None,
            "latest_change": latest_change_set(self.project_id, scoped=True),
            "logs": list_logs(20),
        }

    def vision_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        path = _safe_path(workspace, args["path"])
        metadata: dict[str, Any] = {"path": str(path), "size_bytes": path.stat().st_size}
        try:
            from PIL import Image

            with Image.open(path) as image:
                metadata.update(
                    {
                        "width": image.width,
                        "height": image.height,
                        "format": image.format,
                        "mode": image.mode,
                    }
                )
        except Exception as exc:
            metadata["image_error"] = str(exc)
            return {"ok": False, **metadata}
        return {"ok": True, "inspection": "image_metadata", **metadata}

    def ocr_run(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace = self._require_workspace()
        path = _safe_path(workspace, args["path"])
        try:
            import pytesseract
            from PIL import Image

            text = pytesseract.image_to_string(Image.open(path), lang=args.get("lang", "eng+rus"))
            return {"ok": True, "engine": "tesseract", "text": text}
        except Exception as exc:
            return {"ok": False, "engine": "tesseract", "error": str(exc), "text": ""}

    def memory_get(self, args: dict | None = None) -> dict[str, Any]:
        from koddi.agents.memory import retrieve_memory

        args = args or {}
        return {
            "ok": True,
            "memory": retrieve_memory(
                self.project_id, args.get("query", ""), int(args.get("limit", 12))
            ),
        }

    def memory_set(self, args: dict[str, Any]) -> dict[str, Any]:
        from koddi.policy.account import local_owner

        with db() as conn:
            conn.execute(
                "INSERT INTO memory_summaries(project_id, kind, content, owner_id) VALUES (?, ?, ?, ?)",
                (self.project_id, args.get("kind", "note"), args.get("content", ""), local_owner()),
            )
        return {"ok": True}

    def task_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in {"task.create", "orchestrator.start_turn"}:
            return {
                "ok": True,
                "task": create_task(title=args["title"], project_id=self.project_id),
            }
        if name in {"task.plan", "task.checklist"}:
            return {
                "ok": True,
                "task": set_checklist(int(args["task_id"]), args.get("checklist", [])),
            }
        if name == "task.update_status":
            return {"ok": True, "task": update_task_status(int(args["task_id"]), args["status"])}
        if name in {"task.spawn_parallel", "orchestrator.spawn_child"}:
            return {
                "ok": True,
                "task": spawn_child(
                    int(args["task_id"]), args["title"], args.get("role", "researcher")
                ),
            }
        if name in {"task.cancel", "task.resume"}:
            return {
                "ok": True,
                "task": update_task_status(
                    int(args["task_id"]), "cancelled" if name.endswith("cancel") else "queued"
                ),
            }
        if name in {"task.report", "orchestrator.join_child"}:
            return {"ok": True, "graph": task_graph(self.project_id)}
        if name == "task.ask_user":
            return {"ok": True, "approval_required": True, "question": args.get("question", "")}
        return {"ok": False, "error": f"unknown task tool {name}"}
