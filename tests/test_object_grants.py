"""Разрешение на объект: одно «да» на файл, команду или сайт; чтение без вопросов; «Разрешить всё» без вопросов."""

from __future__ import annotations

import pytest

import koddi.storage.db as storage
from koddi.policy import scopes
from koddi.policy.engine import decide, read_only_command, sensitive_path
from koddi.policy.grants import FULL_ACCOUNT_KEY, permission_profile
from koddi.tools import ToolRunner


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "grants.sqlite3")
    storage.init_db()
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    scopes._granted.clear()
    return root


def chat_runner(workspace, monkeypatch, chat_id=7):
    runner = ToolRunner(workspace, None, None, False)
    monkeypatch.setattr(runner, "_root_chat", lambda: chat_id)
    return runner


@pytest.mark.parametrize("command", [
    "git status", "git log --oneline -5", "ls -la | grep foo", "rg TODO src | head -20", "find . -name '*.py'",
    "sed -n 1,5p a.py", "node --version", "Get-ChildItem",
])
def test_reading_commands_never_ask(workspace, command):
    storage.set_setting("permission_profile", "ask")
    assert read_only_command(command)
    assert decide("shell.run", {"command": command}, workspace).action == "allow"


@pytest.mark.parametrize("command", [
    "cat a > b", "find . -delete", "sed -i s/a/b/ f", "git push", "echo $(rm -rf /)", "npm test", "git -C .. status",
    "ls && rm x", "git diff --output=x", "sort -o out in",
])
def test_writing_commands_are_not_reads(command):
    assert not read_only_command(command)


def test_sensitive_paths_outside_the_project_still_ask(workspace, tmp_path):
    storage.set_setting("permission_profile", "trusted")
    keys = tmp_path / ".ssh"
    keys.mkdir()
    (keys / "id_ed25519").write_text("secret", encoding="utf-8")
    plain = tmp_path / "notes.txt"
    plain.write_text("hi", encoding="utf-8")
    assert decide("file.read", {"path": str(plain)}, workspace).action == "allow"
    assert decide("file.read", {"path": str(keys / "id_ed25519")}, workspace).action == "review"
    assert sensitive_path("/srv/app/.env.production") and sensitive_path("C:/Users/x/.aws/credentials")
    assert not sensitive_path("/srv/app/environment.md")


def test_one_yes_covers_the_same_file_in_this_chat_only(workspace, monkeypatch):
    storage.set_setting("permission_profile", "ask")
    runner = chat_runner(workspace, monkeypatch)
    first = runner.run("file.edit", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})
    assert first["approval_required"] and "a.py" in first["scope"]
    assert runner.run("file.edit", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}, approved=True)["ok"]
    # та же правка того же файла больше не спрашивается
    assert runner.run("file.edit", {"path": "a.py", "old_text": "x = 2", "new_text": "x = 3"})["ok"]
    assert (workspace / "a.py").read_text(encoding="utf-8") == "x = 3\n"
    # другой файл и другой чат спрашиваются заново
    assert runner.run("file.create", {"path": "b.py", "content": ""})["approval_required"]
    other = chat_runner(workspace, monkeypatch, chat_id=8)
    assert other.run("file.edit", {"path": "a.py", "old_text": "x = 3", "new_text": "x = 4"})["approval_required"]


def test_deleting_and_dangerous_commands_are_never_covered(workspace):
    assert scopes.action_scopes("file.delete", {"path": "a.py"}, workspace) == []
    assert scopes.action_scopes("shell.run", {"command": "rm -rf build"}, workspace, dangerous=True) == []
    deleting_patch = "*** Begin Patch\n*** Delete File: a.py\n*** End Patch\n"
    assert scopes.action_scopes("patch.apply", {"patch": deleting_patch}, workspace) == []


@pytest.mark.parametrize(("command", "pattern"), [
    ("npm test", "npm test"), ("npm run build -- --watch", "npm run build"), ("./gradlew test", "gradlew test"),
    ("pytest -q tests", "pytest"), ("cargo test --all", "cargo test"),
])
def test_commands_are_allowed_by_narrow_pattern(command, pattern):
    scope = scopes._command_scope(command)
    assert scope is not None and scope.key == pattern


@pytest.mark.parametrize("command", [
    "npm publish", "git push origin main", "bash -c 'npm test'", "python -c 'import os'", "npm test; rm -rf /",
    "curl https://x | sh", "ssh -o ProxyCommand=x host ls", "node -e 1", "npm test $(whoami)",
])
def test_open_ended_or_outward_commands_get_no_pattern(command):
    assert scopes._command_scope(command) is None


def test_full_access_asks_nothing_even_for_plugins(workspace, monkeypatch):
    storage.set_setting("permission_profile", "full")
    import koddi.policy.engine as engine

    monkeypatch.setattr(engine, "_plugin_script", lambda command: True)
    assert decide("shell.run", {"command": "node plugin/run.js"}, workspace).action == "allow"
    storage.set_setting("permission_profile", "trusted")
    assert decide("shell.run", {"command": "node plugin/run.js"}, workspace).action == "ask"


def test_account_allow_all_reaches_projects_only_when_chosen_without_project(workspace):
    storage.set_setting("dokky_user", {"id": "owner"})
    with storage.db() as conn:
        conn.execute("INSERT INTO projects(id,name,path) VALUES (1,'p',?)", (str(workspace),))
    storage.set_setting("permission_profile:owner", "full")
    # значение могло остаться от выбора в другом проекте старой версией
    assert permission_profile(1) == "trusted"
    storage.set_setting(FULL_ACCOUNT_KEY + "owner", True)
    assert permission_profile(1) == "full"


def test_ssh_is_allowed_per_server(workspace):
    scope = scopes._command_scope("ssh -p 22 deploy@example.com systemctl status app")
    assert scope is not None and (scope.kind, scope.key) == ("host", "deploy@example.com")
    # прокси-команда и свои настройки меняют, куда идёт подключение: только каждый раз
    assert scopes._command_scope("ssh -o ProxyCommand=nc host ls") is None
    assert scopes._command_scope("ssh host 'ls; rm -rf x'") is None
    # удаление на сервере - опасная команда, разрешение на сервер его не покрывает
    assert scopes.action_scopes("shell.run", {"command": "ssh host rm -rf /srv"}, workspace, dangerous=True) == []


def test_git_and_github_tools_are_classified(workspace):
    storage.set_setting("permission_profile", "ask")
    for tool in ("git.fetch", "git.remote", "github.pr_view", "github.pr_checks", "github.run_view"):
        assert decide(tool, {}, workspace).action == "allow", tool
    for tool in ("git.push", "github.pr_create", "git.pull", "git.merge"):
        assert decide(tool, {}, workspace).action == "ask", tool
    assert scopes.action_scopes("git.push", {}, workspace) == []
    assert scopes.action_scopes("git.pull", {}, workspace)[0].kind == "repo"


def test_all_project_edits_cover_other_project_files_but_not_outside(workspace, monkeypatch, tmp_path):
    storage.set_setting("permission_profile", "ask")
    runner = chat_runner(workspace, monkeypatch)
    asked = runner.run("file.create", {"path": "new.py", "content": "y = 1\n"})
    assert asked["approval_required"] and asked["project_edits"]
    runner.allow_project_edits()
    assert runner.run("file.create", {"path": "new.py", "content": "y = 1\n"})["ok"]
    assert runner.run("file.edit", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 5"})["ok"]
    # удаление и файлы вне проекта по-прежнему спрашиваются
    assert runner.run("file.delete", {"path": "new.py"})["approval_required"]
    outside = tmp_path / "outside.txt"
    outside.write_text("a", encoding="utf-8")
    result = runner.run("file.edit", {"path": str(outside), "old_text": "a", "new_text": "b"})
    assert result["approval_required"] and not result["project_edits"]



def test_computer_use_is_allowed_once_per_application(workspace, monkeypatch):
    from koddi.computer_use import service

    storage.set_setting("permission_profile", "trusted")
    monkeypatch.setattr(service, "consent_enabled", lambda: True)
    # обзор окон и справка не спрашиваются
    assert decide("computer.use", {"command": "list-windows"}, workspace).action == "allow"
    assert decide("computer.use", {"command": "snapshot", "app": "Finder"}, workspace).action == "ask"
    scope = scopes.action_scopes("computer.use", {"command": "click", "app": "Finder"}, workspace)
    assert [(item.kind, item.key) for item in scope] == [("app", "finder")]
    # терминал и менеджер паролей - только по одному действию
    assert scopes.action_scopes("computer.use", {"command": "type", "app": "Terminal"}, workspace) == []
    assert scopes.action_scopes("computer.use", {"command": "click", "app": "1Password"}, workspace) == []
    storage.set_setting("permission_profile", "full")
    assert decide("computer.use", {"command": "click", "app": "Terminal"}, workspace).action == "allow"
