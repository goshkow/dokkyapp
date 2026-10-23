"""Рабочий сценарий агента в режиме Code на macOS и Linux: файлы, поиск, оболочка, git, фоновые серверы, процессы."""

import json
import os
import socket
import time
from pathlib import Path

import pytest

from koddi.storage import init_db
from koddi.storage.db import set_setting
from koddi.tools import ToolRunner
from koddi.tools import runner as runner_module

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX shell")


@pytest.fixture()
def tool(tmp_path):
    init_db()
    set_setting("permission_profile", "ask")
    project = tmp_path / "proj"
    project.mkdir()
    runner = ToolRunner(project, explicit_write_consent=True)

    def run(name, args=None):
        return runner.run(name, args or {}, approved=True)

    run.root = project
    run.runner = runner
    return run


def test_agent_can_build_edit_and_organize_a_project(tool):
    created = tool("file.create", {"path": "src/deep/new_dir/hello.py", "content": "print('привет')\n"})
    assert created["ok"], created
    assert (tool.root / "src/deep/new_dir/hello.py").read_text(encoding="utf-8") == "print('привет')\n"
    assert tool("file.edit", {"path": "src/deep/new_dir/hello.py", "old_text": "привет", "new_text": "мир"})["ok"]
    assert tool("file.create", {"path": "README.md", "content": "# demo\n"})["ok"]
    assert tool("file.search_replace", {"path": "README.md", "old_text": "demo", "new_text": "demo2"})["ok"]
    assert tool("file.move", {"source": "README.md", "destination": "docs/README.md"})["ok"]
    assert tool("file.copy", {"source": "docs/README.md", "destination": "docs/copy.md"})["ok"]
    assert (tool.root / "docs/README.md").read_text(encoding="utf-8") == "# demo2\n" and not (tool.root / "README.md").exists()
    found = tool("search.text", {"query": "мир"})
    assert found["ok"] and found["matches"][0]["path"] == "src/deep/new_dir/hello.py"
    assert tool("directory.tree", {"path": "."})["ok"]
    assert tool("file.delete", {"path": "docs/copy.md"})["ok"] and not (tool.root / "docs/copy.md").exists()


@posix_only
def test_shell_uses_login_shell_and_reports_exit_code_and_unicode(tool):
    result = tool("shell.run", {"command": "echo привет && pwd && echo err 1>&2; exit 3"})
    assert result["returncode"] == 3 and not result["ok"]
    assert "привет" in result["stdout"] and Path(result["stdout"].splitlines()[1]).resolve() == tool.root.resolve()
    assert result["stderr"].strip() == "err"
    # стандартные утилиты и интерпретатор находятся по PATH оболочки
    ok = tool("shell.run", {"command": "command -v git && python3 -c 'print(6*7)'"})
    assert ok["ok"] and "42" in ok["stdout"]


@posix_only
def test_shell_timeout_stops_the_whole_process_group(tool):
    started = time.monotonic()
    result = tool("shell.run", {"command": "sleep 30 & sleep 30", "timeout_seconds": 1})
    assert result.get("timed_out") and time.monotonic() - started < 10


@posix_only
def test_shell_argv_loads_zshrc_path(tmp_path, monkeypatch):
    zdot = tmp_path / "home"
    zdot.mkdir()
    (zdot / ".zshrc").write_text('export KODDI_FROM_RC=yes\n')
    monkeypatch.setenv("HOME", str(zdot))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    if not Path("/bin/zsh").exists():
        pytest.skip("нет zsh")
    import subprocess

    out = subprocess.run(runner_module.shell_argv('echo "$KODDI_FROM_RC"'), capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == "yes"


@posix_only
def test_git_workflow(tool):
    tool("file.create", {"path": "a.txt", "content": "one\n"})
    assert tool("shell.run", {"command": "git init -q && git config user.email a@b.c && git config user.name t"})["ok"]
    assert tool("git.add", {"paths": ["."]})["ok"]
    assert tool("git.commit", {"message": "init"})["ok"]
    tool("file.edit", {"path": "a.txt", "old_text": "one", "new_text": "two"})
    assert "two" in tool("git.diff")["stdout"]
    assert tool("git.create_branch", {"branch": "feature/x"})["ok"]
    assert tool("git.status")["ok"] and "feature/x" in tool("git.branch")["stdout"]


@posix_only
def test_process_list_and_kill(tool):
    import subprocess

    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        listed = json.loads(tool("process.list")["processes_json"])
        assert any(item["Id"] == proc.pid for item in listed)
        assert tool("process.kill", {"pid": proc.pid})["ok"]
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()


@posix_only
def test_devserver_runs_in_background_and_stops_with_children(tool):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = tool("devserver.start", {"command": f"python3 -m http.server {port} --bind 127.0.0.1", "wait_seconds": 2})
    assert started["ok"] and started["running"] and started["pid"]
    try:
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("сервер не открыл порт")
        assert tool("devserver.detect")["running"][0]["pid"] == started["pid"]
    finally:
        stopped = tool("devserver.stop", {"pid": started["pid"]})
    assert stopped["ok"] and stopped["stopped"][0]["pid"] == started["pid"]
    time.sleep(0.5)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
    assert tool("devserver.stop", {"pid": started["pid"]})["ok"] is False


@posix_only
def test_devserver_reports_a_command_that_dies_at_start(tool):
    result = tool("devserver.start", {"command": "echo broken >&2; exit 2", "wait_seconds": 3})
    assert result["ok"] is False and result["running"] is False and "broken" in result["output"]


def test_detect_uses_python3_when_python_is_missing(tool, monkeypatch):
    (tool.root / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.setattr(runner_module.shutil, "which", lambda name: None if name == "python" else "/usr/bin/" + name)
    if os.name != "nt":
        assert tool("test.detect")["commands"][0].startswith("python3 -m pytest")


def test_open_file_and_terminal_use_macos_tools(monkeypatch, tmp_path):
    from koddi.integrations import service

    calls = []

    class FakeProc:
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **_kwargs):
        calls.append(command)
        if command[0] in {"code", "cursor"}:
            raise FileNotFoundError(command[0])
        return FakeProc()

    monkeypatch.setattr(service.sys, "platform", "darwin")
    monkeypatch.setattr(service, "_VSCODE_BINS", ())
    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service.subprocess, "run", lambda command, **kw: calls.append(command) or type("R", (), {"returncode": 0, "stderr": ""})())
    target = tmp_path / "x.py"
    target.write_text("print(1)\n")
    assert service.open_file(str(target), 3)["ok"]
    assert any(command[0] == "open" for command in calls) and not any(command[0] == "notepad" for command in calls)
    calls.clear()
    assert service.terminal_open(str(tmp_path))["ok"]
    assert calls == [["open", "-a", "Terminal", str(tmp_path)]]
