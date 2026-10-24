from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

READ_ONLY_TOOLS = {
    "computer.status",
    "workspace.inspect",
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
    "patch.preview",
    "test.detect",
    "build.detect",
    "git.worktree_list",
    "process.list",
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
    "git.checkout",
    "git.create_branch",
    "git.worktree_create",
    "git.worktree_remove",
    "git.add",
    "git.commit",
    "git.restore_file",
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
DANGEROUS_SHELL = (
    "Remove-Item",
    "rm ",
    "del ",
    "format ",
    "shutdown",
    "reg delete",
    "Set-ExecutionPolicy",
    "sudo ",
    "diskutil ",
    "mkfs",
)


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


def _confirm(profile: str, reason: str, **extra) -> Decision:
    """Действие, которое меняет что-то или выходит за проект: решение зависит от режима подтверждений."""
    if profile == "full":
        return Decision("allow", "full access granted by the user", **extra)
    if profile == "trusted":
        return Decision("review", reason, **extra)
    return Decision("ask", reason, **extra)


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
        "document.parse",
        "vision.inspect",
        "ocr.run",
        "verification.run",
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
        if profile == "full":
            return Decision("allow", "full access granted by the user")
        return Decision("ask", "each computer action requires explicit approval")
    if tool_name.startswith("browser."):
        return _decide_browser(tool_name, args, workspace, profile)
    if external:
        reads = tool_name in READ_ONLY_TOOLS
        if profile == "read-only" and not reads:
            return Decision("block", "read-only permission profile")
        if profile == "read-only":
            return Decision("ask", "file outside the project", external=tuple(external))
        return _confirm(profile, "file outside the project", external=tuple(external))
    if tool_name in READ_ONLY_TOOLS:
        return Decision("allow", "read-only tool")
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
    if tool_name in WRITE_TOOLS or tool_name.startswith(("test.", "build.", "lint.", "process.")):
        return _confirm(profile, f"profile={profile}")
    if tool_name == "shell.run":
        command = str(args.get("command", ""))
        if any(marker.lower() in command.lower() for marker in DANGEROUS_SHELL):
            return _confirm(profile, "dangerous shell command", dangerous=True)
        return _confirm(profile, f"profile={profile}")
    return Decision("block", f"unknown tool {tool_name}")
