from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from koddi.storage.db import runtime_path
from koddi.integrations.mcp import (
    mcp_config_path as mcp_config_path,
    mcp_tool_call as mcp_tool_call,
    mcp_tool_call_sync as mcp_tool_call_sync,
    mcp_tools_list as mcp_tools_list,
)


_VSCODE_BINS = (
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
)


def _file_commands(target: Path, line: int | None) -> list[list[str]]:
    spot = f"{target}:{line or 1}"
    commands = [["code", "-g", spot], ["cursor", "-g", spot]]
    if sys.platform == "darwin":
        commands += [[binary, "-g", spot] for binary in _VSCODE_BINS if Path(binary).exists()]
        # приложение без командной строки: открываем через систему, затем программой по умолчанию
        commands += [["open", "-a", "Visual Studio Code", str(target)], ["open", "-a", "Cursor", str(target)], ["open", str(target)]]
    elif os.name == "nt":
        commands.append(["notepad", str(target)])
    else:
        commands.append(["xdg-open", str(target)])
    return commands


def open_file(path: str, line: int | None = None) -> dict:
    target = Path(path).resolve()
    errors = []
    for command in _file_commands(target, line):
        try:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if command[0] == "open":
                # open сообщает об ошибке кодом возврата сразу
                try:
                    if proc.wait(timeout=5) != 0:
                        errors.append(f"{' '.join(command[:3])}: код {proc.returncode}")
                        continue
                except subprocess.TimeoutExpired:
                    pass
            return {"ok": True, "command": command}
        except Exception as exc:
            errors.append(str(exc))
    return {"ok": False, "errors": errors}


def open_diff(diff_path: str | None = None) -> dict:
    path = diff_path or str(runtime_path("diffs", "latest.diff"))
    return open_file(path)


def terminal_open(workspace: str | None = None) -> dict:
    cwd = workspace or os.getcwd()
    if sys.platform == "darwin":
        # Terminal.app открывает окно в этой папке
        proc = subprocess.run(["open", "-a", "Terminal", cwd], capture_output=True, text=True, timeout=10, check=False)
        return {"ok": proc.returncode == 0, "cwd": cwd, **({} if proc.returncode == 0 else {"error": proc.stderr.strip()})}
    if os.name == "nt":
        from koddi.hidden_process import CREATE_NEW_CONSOLE

        # терминал - единственное окно, которое человек просил открыть явно
        for terminal in (["wt", "-d", cwd], ["cmd", "/K", "cd", "/d", cwd]):
            try:
                subprocess.Popen(terminal, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NEW_CONSOLE)
                return {"ok": True, "cwd": cwd}
            except OSError:
                continue
        return {"ok": False, "cwd": cwd, "error": "терминал не найден"}
    for terminal in (["x-terminal-emulator"], ["gnome-terminal", f"--working-directory={cwd}"], ["konsole", "--workdir", cwd]):
        try:
            subprocess.Popen(terminal, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "cwd": cwd}
        except OSError:
            continue
    return {"ok": False, "cwd": cwd, "error": "терминал не найден"}


async def issue_import(url: str) -> dict:
    github = re.search(r"github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    if github:
        owner, repo, number = github.groups()
        api = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(api, headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
            data = response.json()
        return {
            "ok": True,
            "provider": "github",
            "title": data.get("title"),
            "body": data.get("body"),
            "url": data.get("html_url"),
        }
    gitlab = re.search(r"gitlab\.com/(.+)/-/issues/(\d+)", url)
    if gitlab:
        project, iid = gitlab.groups()
        api = f"https://gitlab.com/api/v4/projects/{httpx.URL(project).raw_path.decode().replace('/', '%2F')}/issues/{iid}"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(api)
            response.raise_for_status()
            data = response.json()
        return {
            "ok": True,
            "provider": "gitlab",
            "title": data.get("title"),
            "body": data.get("description"),
            "url": data.get("web_url"),
        }
    return {"ok": False, "error": "Unsupported issue URL or provider requires configured token"}


def issue_update(issue_id: str, body: str) -> dict:
    return {
        "ok": False,
        "issue_id": issue_id,
        "prepared_body": body,
        "status": "prepared_for_user_confirmation",
        "error_code": "not_implemented",
        "error": "Issue update was prepared but not sent; no write adapter is configured",
    }
