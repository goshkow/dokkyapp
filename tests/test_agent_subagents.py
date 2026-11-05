"""Этап 2 паритета с Claude Code: сабагенты, агенты, команды и хуки проекта."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

import koddi.storage.db as storage
from koddi.agents import subagents
from koddi.agents.orchestrator import AgentOrchestrator

OWNER = "__test__"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def trust(project_id: int, root: Path, relative: str) -> None:
    from koddi.skills.project import decide

    decide(project_id, OWNER, root, relative, hashlib.sha256((root / relative).read_bytes()).hexdigest(), True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    from koddi.projects import add_project

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "subagents.sqlite3")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "full")
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: OWNER)
    storage.init_db()
    root = tmp_path / "proj"
    write(root, "app/main.py", "print('ok')\n")
    project = add_project(str(root))
    return project["id"], root.resolve()


def test_project_files_of_every_kind_need_trust(project):
    project_id, root = project
    from koddi.skills.project import sync, trusted

    write(root, ".claude/commands/fix-issue.md", "---\ndescription: Чинит задачу\nargument-hint: <номер>\n---\nПочини задачу $ARGUMENTS.\n")
    write(root, ".claude/agents/reviewer.md", "---\nname: reviewer\ndescription: Смотрит код\ntools: Read, Grep, Glob\n---\nИщи ошибки.\n")
    write(root, ".claude/settings.json", json.dumps({"permissions": {}, "hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    write(root, ".dokky/settings.json", json.dumps({"theme": "dark"}))
    listed = {item["path"]: item for item in sync(project_id, OWNER, root)}
    assert {item["kind"] for item in listed.values()} == {"command", "agent", "hooks"}
    # настройки без хуков доверия не требуют и в списке не появляются
    assert ".dokky/settings.json" not in listed
    assert all(item["status"] == "pending" for item in listed.values())
    assert listed[".claude/settings.json"]["instructions"] == "PreToolUse [Bash]: echo hi"
    assert trusted(project_id, OWNER, root, kind="command") == []
    trust(project_id, root, ".claude/commands/fix-issue.md")
    commands = trusted(project_id, OWNER, root, kind="command")
    assert [item["name"] for item in commands] == ["fix-issue"] and commands[0]["argument_hint"] == "<номер>"
    # подтверждённая команда не становится навыком
    assert trusted(project_id, OWNER, root) == []


def test_project_command_expands_arguments_only_for_trusted_version(project):
    project_id, root = project
    from koddi.skills.service import project_command

    path = write(root, ".claude/commands/fix-issue.md", "Почини задачу $ARGUMENTS, начни с $1.\n")
    assert project_command("/fix-issue 42 срочно", project_id, OWNER, root) == ""
    trust(project_id, root, ".claude/commands/fix-issue.md")
    block = project_command("/fix-issue 42 срочно", project_id, OWNER, root)
    assert "Почини задачу 42 срочно, начни с 42." in block and '<command name="fix-issue">' in block
    assert project_command("/other 1", project_id, OWNER, root) == ""
    assert project_command("почини /fix-issue", project_id, OWNER, root) == ""
    write(root, ".claude/commands/plain.md", "Проверь проект.")
    trust(project_id, root, ".claude/commands/plain.md")
    assert "ARGUMENTS: только api" in project_command("/plain только api", project_id, OWNER, root)
    # изменённый файл до нового подтверждения не работает
    path.write_text("Удали всё.", encoding="utf-8")
    assert project_command("/fix-issue 42", project_id, OWNER, root) == ""


def test_agent_tools_map_from_claude_names():
    assert subagents.tools_from_claude([]) is None
    read = subagents.tools_from_claude(["Read", "Grep", "Glob", "LS"])
    assert read is not None and read <= subagents.EXPLORE_TOOLS and "search.text" in read
    write_tools = subagents.tools_from_claude(["Read", "Edit", "Bash"])
    assert {"file.edit", "shell.run", "file.read"} <= write_tools
    assert not write_tools <= subagents.EXPLORE_TOOLS
    assert subagents.tools_from_claude(["mcp__github__create_issue"]) == frozenset()


def test_resolve_builtin_project_and_unknown_agents(project):
    project_id, root = project
    explore = subagents.resolve("explore", project_id, OWNER, root)
    assert explore["read_only"] and explore["tools"] == subagents.EXPLORE_TOOLS
    general = subagents.resolve(None, project_id, OWNER, root)
    assert general["name"] == "general" and general["tools"] is None and not general["read_only"]
    write(root, ".claude/agents/reviewer.md", "---\nname: reviewer\ndescription: Смотрит код\ntools: Read, Grep\n---\nИщи ошибки.\n")
    pending = subagents.resolve("reviewer", project_id, OWNER, root)
    assert "не подтверждён" in pending["error"]
    trust(project_id, root, ".claude/agents/reviewer.md")
    reviewer = subagents.resolve("Reviewer", project_id, OWNER, root)
    assert reviewer["read_only"] and "Ищи ошибки." in reviewer["role"]
    unknown = subagents.resolve("nobody", project_id, OWNER, root)
    assert unknown["available"] == ["explore", "general", "reviewer"]


def test_project_hook_blocks_tool_only_after_trust(project):
    project_id, root = project
    from koddi.plugins import hooks

    command = f'"{sys.executable}" -c "import sys; sys.stderr.write(\'не трогай git\'); sys.exit(2)"'
    write(root, ".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 10}]}]}}))
    assert hooks.pre_tool(project_id, root, None, "shell.run", {"command": "git push"}) is None
    trust(project_id, root, ".claude/settings.json")
    reason = hooks.pre_tool(project_id, root, None, "shell.run", {"command": "git push"})
    assert reason is not None and "не трогай git" in reason
    # матчер Bash не задевает чтение файлов
    assert hooks.pre_tool(project_id, root, None, "file.read", {"path": "app/main.py"}) is None


def test_project_session_start_runs_once_per_chat(project):
    project_id, root = project
    from koddi.plugins import hooks

    counter = root / "count.txt"
    command = (f'"{sys.executable}" -c "import pathlib; p=pathlib.Path(r\'{counter}\'); '
               "p.write_text(str(int(p.read_text() if p.exists() else 0)+1)); print('правила проекта')\"")
    write(root, ".claude/settings.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}))
    trust(project_id, root, ".claude/settings.json")
    with storage.db() as conn:
        chat_id = int(conn.execute("INSERT INTO chats(project_id,title,mode) VALUES (?,'t','development')", (project_id,)).lastrowid)
    first = hooks.turn_context(OWNER, project_id, root, chat_id, "привет")
    second = hooks.turn_context(OWNER, project_id, root, chat_id, "ещё")
    assert first[0]["context"] == "правила проекта" == second[0]["context"]
    assert counter.read_text() == "1"


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "agent.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        return int(conn.execute("INSERT INTO chats(title,mode,model) VALUES ('Test','development','m')").lastrowid)


class ExploreClient:
    """Родитель шлёт два поручения explore сразу; дети отвечают с задержкой, чтобы было видно параллельность."""

    def __init__(self, delay: float = 0.4):
        self.delay = delay
        self.active = self.peak = 0
        self.child_tools: list[set[str]] = []

    async def stream(self, **kwargs):
        history = kwargs["messages"]
        last = history[-1]
        if "Generate only" in history[0]["content"]:
            yield {"content": "Изучение"}
        elif last.get("role") == "user" and "Task:\n" in last["content"]:
            self.child_tools.append({item["function"]["name"] for item in kwargs.get("tools") or []})
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(self.delay)
            finally:
                self.active -= 1
            yield {"content": "отчёт: " + last["content"].rsplit("\n", 1)[-1]}
        elif last.get("role") == "tool":
            yield {"content": "Готово"}
        else:
            for index, task in enumerate(("где вход", "где оплата")):
                yield {"tool_call": {"id": f"d{index}", "name": "task.delegate", "arguments": json.dumps(
                    {"title": task, "task": task, "agent": "explore"}, ensure_ascii=False)}}


@pytest.mark.asyncio
async def test_explore_subagents_run_in_parallel_with_read_only_tools(chat, tmp_path):
    client = ExploreClient()
    events = [event async for event in AgentOrchestrator(client).run(
        chat_id=chat, project_id=None, workspace=tmp_path, user_message="Изучи", model="test")]
    assert "turn.completed" in events[-1]
    assert client.peak == 2
    for names in client.child_tools:
        assert names and names <= subagents.EXPLORE_TOOLS | subagents.CHILD_CONTROLS
        assert not names & {"file.edit", "shell.run", "task.delegate"}
    results = [json.loads(event.split("data: ", 1)[1]) for event in events if event.startswith("event: tool.result")]
    reports = {item["id"]: item["result"] for item in results}
    assert reports["d0"]["summary"] == "отчёт: где вход" and reports["d1"]["summary"] == "отчёт: где оплата"
    assert reports["d0"]["agent"] == "explore"
    progress = [json.loads(event.split("data: ", 1)[1]) for event in events if event.startswith("event: task.progress")]
    assert {item["call_id"] for item in progress} == {"d0", "d1"}
    with storage.db() as conn:
        parents = {row[0] for row in conn.execute("SELECT parent_chat_id FROM chats WHERE id<>?", (chat,))}
    assert parents == {chat}


@pytest.mark.asyncio
async def test_subagent_over_time_limit_is_stopped_but_turn_goes_on(chat, tmp_path, monkeypatch):
    monkeypatch.setattr(subagents, "TIMEOUT_SECONDS", 0.5)
    client = ExploreClient(delay=5)
    events = [event async for event in AgentOrchestrator(client).run(
        chat_id=chat, project_id=None, workspace=tmp_path, user_message="Изучи", model="test")]
    assert "turn.completed" in events[-1]
    results = [json.loads(event.split("data: ", 1)[1])["result"] for event in events if event.startswith("event: tool.result")]
    assert [item["status"] for item in results] == ["timeout", "timeout"]
    assert all(item["ok"] is False and "15" not in item["error"] for item in results)


def test_subagent_chat_is_hidden_and_its_question_shows_in_parent(chat, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from koddi.agents.recovery import current_owner

    api_app = importlib.import_module("koddi.api.app")
    monkeypatch.setattr(api_app, "local_owner", lambda: OWNER)
    with storage.db() as conn:
        conn.execute("UPDATE chats SET owner_id=? WHERE id=?", (OWNER, chat))
        child = int(conn.execute("INSERT INTO chats(title,mode,owner_id,parent_chat_id) VALUES ('child','development',?,?)",
                                 (OWNER, chat)).lastrowid)
        # ход живого процесса: иначе восстановление при запуске приложения закроет его как брошенный
        turn = int(conn.execute("INSERT INTO turns(chat_id,status,owner_pid,owner_token) VALUES (?, 'running', ?, ?)",
                                (child, *current_owner())).lastrowid)
        conn.execute("INSERT INTO agent_requests(turn_id,kind,payload_json) VALUES (?,'question','{}')", (turn,))
    client = TestClient(api_app.create_app(), base_url="http://127.0.0.1:8765")
    assert [item["id"] for item in client.get("/api/chats").json()] == [chat]
    requests = client.get("/api/agent/requests").json()
    assert [(item["chat_id"], item["turn_id"]) for item in requests] == [(chat, turn)]


def test_hook_trust_covers_scripts_it_runs(project):
    project_id, root = project
    from koddi.plugins import hooks
    from koddi.skills.project import sync

    script = write(root, ".claude/hooks/check.py", "import sys\nsys.exit(0)\n")
    command = f'"{sys.executable}" .claude/hooks/check.py'
    write(root, ".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]}}))
    listed = next(item for item in sync(project_id, OWNER, root) if item["kind"] == "hooks")
    assert ".claude/hooks/check.py" in listed["instructions"]
    from koddi.skills.project import decide

    decide(project_id, OWNER, root, ".claude/settings.json", listed["sha256"], True)
    assert hooks.project_items(project_id, root, OWNER)
    # подменённый скрипт больше не запускается без нового подтверждения
    script.write_text("import sys\nsys.stderr.write('другое'); sys.exit(2)\n", encoding="utf-8")
    assert hooks.project_items(project_id, root, OWNER) == []
    assert hooks.pre_tool(project_id, root, None, "shell.run", {"command": "ls"}) is None


def test_deleting_chat_removes_subagent_data(chat, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    api_app = importlib.import_module("koddi.api.app")
    monkeypatch.setattr(api_app, "local_owner", lambda: OWNER)
    monkeypatch.setattr(api_app, "require_chat", lambda chat_id: {"id": chat_id})
    with storage.db() as conn:
        child = int(conn.execute("INSERT INTO chats(title,mode,parent_chat_id) VALUES ('child','development',?)", (chat,)).lastrowid)
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?, 'assistant', 'прочитал секретный файл')", (child,))
        conn.execute("INSERT INTO turns(chat_id,status) VALUES (?, 'completed')", (child,))
    client = TestClient(api_app.create_app(), base_url="http://127.0.0.1:8765")
    assert client.delete(f"/api/chats/{chat}").status_code == 200
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (child,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM turns WHERE chat_id=?", (child,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0


def test_plugin_skill_needs_user_ok_but_builtin_does_not(project, monkeypatch):
    project_id, root = project
    from koddi.agents.hints import turn_hints
    from koddi.policy.engine import decide
    from koddi.skills import selector

    real = selector.catalog

    def catalog(project_id, owner, root):
        plugin = {"name": "superpowers:brainstorm", "key": None, "description": "Мозговой штурм", "triggers": [],
                  "languages": [], "capabilities": [], "source": "plugin", "skill": {}}
        return [*real(project_id, owner, root), plugin]

    monkeypatch.setattr(selector, "catalog", catalog)
    builtin = next(item["name"] for item in real(project_id, OWNER, root) if item["source"] == "builtin")
    assert decide("skill.load", {"name": builtin}, root, project_id).action == "allow"
    # «Разрешить всё» ни о чём не спрашивает, в остальных режимах навык плагина - решение человека
    for name in ("superpowers:brainstorm", "brainstorm"):
        assert decide("skill.load", {"name": name}, root, project_id).action == "allow"
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "trusted")
    for name in ("superpowers:brainstorm", "brainstorm"):
        assert decide("skill.load", {"name": name}, root, project_id).action == "ask"
    # файлы плагина читаются без вопроса: это не подключение навыка
    assert decide("skill.load", {"name": "superpowers", "file": "README.md"}, root, project_id).action == "allow"
    listed = turn_hints(project_id, root, "придумай идеи", {"skill.load"})["available_skills"]["skills"]
    marked = {item["name"]: item.get("needs_user_ok", False) for item in listed}
    assert marked["superpowers:brainstorm"] is True and marked[builtin] is False


def test_model_learns_skills_agents_and_harness_rules(project):
    """Список навыков, агенты проекта и правила оболочки доходят до модели: и в снимок Докки, и в промпт других."""
    from koddi.agents.hints import turn_hints
    from koddi.agents.prompts import CONTEXT_PREFIX, compose_coding_context, compose_system_prompt

    project_id, root = project
    write(root, ".claude/agents/reviewer.md", "---\nname: reviewer\ndescription: Смотрит код\ntools: Read\n---\nИщи ошибки.\n")
    trust(project_id, root, ".claude/agents/reviewer.md")
    hints = turn_hints(project_id, root, "проверь код", {"skill.load", "task.delegate"})
    assert hints["available_skills"]["skills"] and hints["project_agents"]["agents"][0]["name"] == "reviewer"

    context = json.loads(compose_coding_context(mode="development", workspace=root, project_id=project_id,
                                                hints=hints)["content"][len(CONTEXT_PREFIX):])
    assert context["project_agents"]["agents"][0]["name"] == "reviewer"
    assert context["available_skills"]["skills"]
    for rule in ("task.delegate", "explore", "stopped_by_user", "devserver.output", "background_finished",
                 "needs_user_ok", "<command>", "Файлы проекта для агента", "commit_message", "project root", "tail or head",
                 "language of their latest message"):
        assert rule in context["harness_policy"]

    system, _, _ = compose_system_prompt(mode="development", task="проверь код", workspace=root, project_id=project_id,
                                         tool_schemas=[], hints=hints)
    assert "## How tools run in this application" in system and '"reviewer"' in system
    # в маленьком окне - краткая версия: иначе не остаётся места для сжатия
    small, _, _ = compose_system_prompt(mode="development", task="проверь код", workspace=root, project_id=project_id,
                                        tool_schemas=[], hints=hints, small_window=True)
    assert len(small) < len(system) - 2000
    assert "task.delegate" in small and '"reviewer"' in small and "available_skills" not in small
    silent, _, _ = compose_system_prompt(mode="chat", task="привет", workspace=None, include_tools=False)
    assert "How tools run" not in silent
