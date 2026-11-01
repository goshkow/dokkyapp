"""Платная проверка оболочки агента на настоящей модели: знает ли модель свои возможности и пользуется ли ими сама.

Сценарии: знание оболочки, @-файл, параллельные сабагенты, фоновая команда, остановка команды человеком, правка с
предложением коммита и настоящими коммитом и отправкой, а также команда, агент, навык и хук проекта.

Запускается только явно: --confirm-live. Вход читается без изменения исходного профиля, refresh запрещён, профиль и
проекты временные, инструменты вне списка обрывают сценарий до выполнения. Расход считается по токенам сверху.
--prepare-only ничего не тратит: собирает проекты и проверяет всё, что не требует модели.
Печатаются только поля отчёта: ни настроек, ни токенов в выводе нет.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from live_plugin_check import READ_ONLY_SHELL, read_only_login  # noqa: E402
from live_ub_check import cost  # noqa: E402

READS = {
    "project.instructions", "project.map", "workspace.inspect", "directory.list", "directory.tree", "file.read",
    "file.read_many", "file.read_range", "file.read_chunk", "file.metadata", "search.text", "search.files",
    "symbol.search", "code.outline", "code.symbols", "code.references", "context.retrieve", "tool.output_read",
    "git.status", "git.diff", "plan.update", "task.complete", "skill.load",
}
WRITES = {"file.create", "file.write", "file.write_patch", "file.edit", "file.search_replace", "file.apply_patch",
          "patch.apply", "patch.preview", "qa.run", "quality.run", "test.run", "verification.run"}
# проект временный, но запускаем только то, что в нём лежит: python со своими файлами и pytest
PROJECT_SHELL = re.compile(r"^\s*((\.venv/bin/)?python3?\s+(-m\s+pytest|slow\.py|server\.py)|pytest)(\s[^;&|<>`$()\n]*)?$")

READ_ONLY_ECHO = re.compile(r"^\s*echo(\s[^;&|<>`$()\n]*)?$")

SERVER = """import http.server
import os
import threading
import time

time.sleep(4)
server = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
print("listening on", server.server_address[1], flush=True)
threading.Timer(180, os._exit, [0]).start()
server.serve_forever()
"""
SLOW = """import time

for second in range(120):
    print("шаг", second, flush=True)
    time.sleep(1)
"""
GUARD = """import json
import sys

data = json.load(sys.stdin)
if "locked/" in json.dumps(data.get("tool_input") or {}):
    sys.stderr.write("Папку locked/ менять запрещено правилами проекта")
    sys.exit(2)
"""
COMMAND = """---
description: Найти TODO в папке
---
Найди все TODO в папке $ARGUMENTS. Поручи поиск агенту проекта todo-scout и выведи его список как есть, по строке на TODO.
"""
AGENT = """---
name: todo-scout
description: Ищет TODO в коде
tools: Grep, Read
---
Ты ищешь TODO в коде. Для каждого пиши путь и номер строки. Последней строкой отчёта всегда пиши SCOUT-OK.
"""
SKILL = """---
name: release-notes
description: Как в этом проекте писать заметки о выпуске (release notes, RELEASE.md)
---
Первая строка заметки о выпуске - ровно `## Что нового [проверено]`. Дальше список, каждый пункт начинается с глагола.
"""


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def repository(root: Path, files: dict[str, str], branch: str = "main") -> Path:
    for relative, text in files.items():
        write(root, relative, text)
    git(root, "init", "-q", "-b", branch)
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root.resolve()


def seed_explore(root: Path) -> Path:
    return repository(root, {
        "app/config.py": "PORT = 8123\n",
        "net/client.py": "RETRIES = 7\n\n\ndef fetch(url):\n    return url\n",
        "db/schema.py": 'ORDERS_TABLE = "orders_v2"\n',
    })


def seed_commit(root: Path) -> Path:
    root = repository(root, {
        "pricing.py": "def apply_discount(price, percent):\n    return round(price - price * percent, 2)\n",
        "tests/test_pricing.py": "from pricing import apply_discount\n\n\ndef test_discount():\n"
                                 "    assert apply_discount(200, 10) == 180\n",
        "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["."]\n',
        "notes.txt": "черновик\n",
    }, branch="feature/pricing")
    write(root, "notes.txt", "незаконченная правка человека\n")
    write(root, ".env", "TOKEN=не-настоящий\n")
    git(root, "branch", "main")
    bare = root.parent / (root.name + "-remote.git")
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    git(root, "remote", "add", "origin", str(bare))
    git(root, "push", "-q", "origin", "main")
    return root


def seed_project_files(root: Path) -> Path:
    return repository(root, {
        "src/a.py": "x = 1  # TODO поправить имя\n",
        "src/deep/b.py": "\n\ny = 2  # TODO убрать константу\n",
        "other/c.py": "z = 3  # TODO это вне src\n",
        "locked/config.txt": "mode=slow\n",
        ".claude/commands/find-todos.md": COMMAND,
        ".claude/agents/todo-scout.md": AGENT,
        ".dokky/skills/release-notes/SKILL.md": SKILL,
        ".claude/hooks/guard.py": GUARD,
        ".claude/settings.json": json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Write|Edit|Bash", "hooks": [
                {"type": "command", "command": f'"{sys.executable}" .claude/hooks/guard.py', "timeout": 10}]}]}}),
    })


def trust_all(project_id: int, owner: str, root: Path) -> list[str]:
    from koddi.skills.project import decide, sync

    names = []
    for item in sync(project_id, owner, root):
        decide(project_id, owner, root, item["path"], item["sha256"], True)
        names.append(f"{item['kind']}:{item['name']}")
    return sorted(names)


def has(text: str, *needles: str) -> bool:
    # в запросах к модели имена инструментов идут через __, ей можно называть их и так
    lowered = text.casefold().replace("__", ".")
    return all(needle.casefold() in lowered for needle in needles)


class Budget:
    """Оценка расхода: законченные ходы (и сабагенты) из базы плюс идущий ход по его событиям."""

    def __init__(self, limit: float):
        self.limit = limit
        self.live = 0.0
        self.turn_id = None

    def finished(self) -> float:
        import koddi.storage.db as storage

        with storage.db() as conn:
            rows = conn.execute("SELECT turn_id,metrics_json FROM turn_metrics").fetchall()
        total = 0.0
        for row in rows:
            if row["turn_id"] == self.turn_id:
                continue
            metrics = json.loads(row["metrics_json"] or "{}")
            total += cost({"prompt_tokens": metrics.get("input_tokens"),
                           "prompt_cache_hit_tokens": metrics.get("cached_input_tokens"),
                           "completion_tokens": metrics.get("output_tokens")})
        return total

    def spent(self) -> float:
        return self.finished() + self.live

    def check(self) -> None:
        if self.spent() >= self.limit:
            raise RuntimeError("остановлено по бюджету расхода")


async def run_turn(name: str, workspace: Path, project_id: int, chat_id: int, content: str, budget: Budget, *,
                   allowed, context: str = "", seconds: int = 240, max_steps: int = 18, on_output=None) -> dict:
    """Один ход агента: события те же, что получает окно, в отчёт идут только имена инструментов и итог."""
    import koddi.storage.db as storage
    from koddi.agents.orchestrator import AgentOrchestrator

    report: dict = {"tools": [], "delegates": [], "child_tools": [], "steps": 0, "output_events": 0}
    cancellation = threading.Event()
    started = time.monotonic()
    budget.live, budget.turn_id = 0.0, None
    message = (context + "\n\nЗапрос пользователя:\n" if context else "") + content
    stream = AgentOrchestrator().run(chat_id=chat_id, project_id=project_id, workspace=workspace, user_message=message,
                                     display_message=content, mode="development", cancel_event=cancellation)
    step_delegates: dict[int, int] = {}
    try:
        async with asyncio.timeout(seconds):
            async for event in stream:
                kind = event.splitlines()[0][7:]
                payload = json.loads(event.split("data: ", 1)[1])
                if kind == "turn.started":
                    budget.turn_id = payload.get("turn_id") or budget.turn_id
                elif kind == "agent.usage" and isinstance(payload.get("usage"), dict):
                    budget.live += cost(payload["usage"])
                    budget.check()
                elif kind == "agent.step":
                    report["steps"] += 1
                    if report["steps"] > max_steps:
                        raise RuntimeError("остановлено по числу шагов")
                elif kind == "tool.call":
                    tool, arguments = payload.get("name", ""), payload.get("arguments") or {}
                    if not allowed(tool, arguments):
                        shown = json.dumps(arguments, ensure_ascii=False)[:300]
                        raise RuntimeError(f"остановлено до непредусмотренного инструмента: {tool} {shown}")
                    report["tools"].append(tool)
                    if tool == "task.delegate":
                        report["delegates"].append(arguments.get("agent") or "general")
                        step_delegates[report["steps"]] = step_delegates.get(report["steps"], 0) + 1
                    if tool == "shell.run":
                        report.setdefault("shell", []).append(str(arguments.get("command"))[:120])
                    if tool == "task.complete" and arguments.get("commit_message"):
                        report["commit_message_arg"] = str(arguments["commit_message"])[:120]
                    print(f"{name:14} {time.monotonic() - started:6.1f} с  {tool}  ~${budget.spent():.4f}", flush=True)
                elif kind == "task.progress":
                    if payload.get("event") == "tool":
                        report["child_tools"].append(payload.get("name"))
                    if payload.get("event") == "finished":
                        budget.check()
                elif kind == "tool.output":
                    report["output_events"] += 1
                    if on_output:
                        on_output(payload, report)
                elif kind == "tool.result":
                    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                    if result.get("stopped_by_user"):
                        report["stopped_by_user"] = True
                    if str(payload.get("name") or "").startswith("devserver."):
                        report.setdefault("devserver_text", []).append(json.dumps(result, ensure_ascii=False)[:2000])
                elif kind == "agent.input_required":
                    approved = payload.get("kind") == "approval" and allowed(
                        str(payload.get("name") or ""),
                        payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {})
                    answer = {"approved": approved} if payload.get("kind") == "approval" else \
                        {"answer": "Реши сам, без уточнений."}
                    report.setdefault("asked", []).append(str(payload.get("name") or payload.get("kind")))
                    with storage.db() as conn:
                        conn.execute("UPDATE agent_requests SET response_json=? WHERE id=?",
                                     (json.dumps(answer), payload["id"]))
                elif kind in {"turn.failed", "turn.cancelled", "turn.completed"}:
                    report["status"] = kind[5:]
                    budget.turn_id = None
                    if kind == "turn.failed":
                        report["error"] = str(payload.get("error"))[:300]
    except (RuntimeError, TimeoutError) as exc:
        report["stopped"] = str(exc)[:400] or type(exc).__name__
    finally:
        cancellation.set()
        await stream.aclose()
    budget.live, budget.turn_id = 0.0, None
    with storage.db() as conn:
        row = conn.execute("SELECT content FROM messages WHERE chat_id=? AND role='assistant' AND content<>'' "
                           "ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
    report["answer"] = row[0] if row else ""
    with storage.db() as conn:
        lines = conn.execute("SELECT content FROM messages WHERE chat_id=? AND role='assistant' AND content<>''", (chat_id,)).fetchall()
    # реплики не на языке запроса: человек видит их в ленте
    report["not_russian"] = [line[0][:80] for line in lines if re.match(r"[`A-Za-z]", line[0]) and not re.search(r"[А-Яа-я]", line[0])]
    report["parallel_delegates"] = max(step_delegates.values(), default=0)
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="без модели и без расхода")
    parser.add_argument("--max-usd", type=float, default=0.40)
    parser.add_argument("--only", default="", help="сценарии через запятую")
    args = parser.parse_args()
    if not args.confirm_live and not args.prepare_only:
        parser.error("Нужен --confirm-live: сценарий расходует лимит Докки")
    activate = None
    if not args.prepare_only:
        activate = read_only_login()
        if activate is None:
            print(json.dumps({"ok": False, "error": "нужен действующий вход; refresh не выполнялся"}, ensure_ascii=False))
            return 2

    output = Path(tempfile.mkdtemp(prefix="dokky-live-agent-")).resolve()
    profile = output / "profile"
    profile.mkdir()
    os.environ["KODDI_DATA_DIR"] = str(profile)
    os.environ.pop("KODDI_INTERNAL_TEST", None)
    # python3 и pytest в проектах - из окружения разработки
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    logging.disable(logging.CRITICAL)
    import koddi.storage.db as storage

    storage.DB_PATH = profile / "koddi.sqlite3"
    storage.init_db()
    from koddi.agents.mentions import mentioned_files
    from koddi.plugins import hooks
    from koddi.projects import add_project, gitpanel
    from koddi.skills.service import project_command

    budget = Budget(args.max_usd)
    report: dict = {"ok": False, "output": str(output), "scenarios": {}}
    picked = {item.strip() for item in args.only.split(",") if item.strip()}
    owner = "__prepare__"
    coding = None

    def chat(project: dict, title: str) -> int:
        with storage.db() as conn:
            return conn.execute("INSERT INTO chats(project_id,title,mode,model,owner_id) VALUES (?,?,'development','dokky-code',?)",
                                (project["id"], title, coding.owner() if coding else owner)).lastrowid

    async def scenario(name: str, root: Path, project: dict, content: str, allowed, verdict, **options) -> None:
        if picked and name not in picked:
            return
        if budget.spent() >= budget.limit:
            report["scenarios"][name] = {"skipped": "бюджет исчерпан"}
            return
        chat_id = chat(project, name)
        session = await coding.ensure(chat_id)
        if session["session"]["reasoning_mode"] != "light":
            raise RuntimeError("сервер не подтвердил Light; генерация не запускалась")
        extra = options.pop("build", None)
        if extra:
            extra = extra(chat_id)
            options.update(extra)
        result = await run_turn(name, root, project["id"], chat_id, content, budget, allowed=allowed, **options)
        try:
            result["checks"] = verdict(result, chat_id)
        except Exception as exc:  # отчёт важнее: падение одной проверки не должно обрывать остальные платные сценарии
            result["checks"] = {"check_crashed": type(exc).__name__}
        result["passed"] = result.get("status") == "completed" and all(
            value is True for value in result["checks"].values() if isinstance(value, bool))
        result["answer"] = result["answer"][:3000]
        result.pop("devserver_text", None)
        report["scenarios"][name] = result
        print(f"{name:14} {'OK' if result['passed'] else 'НЕ ПРОШЁЛ'}  ~${budget.spent():.4f}", flush=True)

    started = time.monotonic()
    code = 1
    try:
        explore_root = seed_explore(output / "explore")
        background_root = repository(output / "background", {"server.py": SERVER, "slow.py": SLOW,
                                                             "docs/note.md": "Кодовое слово: ТЮЛЬПАН-42.\n"})
        commit_root = seed_commit(output / "commit")
        files_root = seed_project_files(output / "files")
        gate_root = repository(output / "gate", {"greet.py": "def greet(name):\n    return 'Hi, ' + name\n"})

        if not args.prepare_only:
            activate()
            from koddi.dokky.client import dokky

            await dokky.bootstrap()
            from koddi.dokky import coding as coding_module
            from koddi.policy.preferences import save_reasoning_mode

            coding = coding_module
            save_reasoning_mode("light")
            owner = str(storage.get_setting("dokky_user", {})["id"])
            storage.set_setting("permission_profile:" + owner, "trusted")
            storage.set_setting("coding_keepalive:" + owner, False)
            storage.set_setting("computer_use_enabled:" + owner, False)
        else:
            os.environ["KODDI_INTERNAL_TEST"] = "1"
            storage.set_setting("dokky_user", {"id": owner})

        explore = add_project(str(explore_root))
        background = add_project(str(background_root))
        commit = add_project(str(commit_root))
        files = add_project(str(files_root))
        gate = add_project(str(gate_root))
        report["trusted"] = trust_all(files["id"], owner, files_root)

        # всё, что можно проверить без модели, чтобы не платить за ошибки самого сценария
        expanded = project_command("/find-todos src", files["id"], owner, files_root)
        mention = mentioned_files("Что в @docs/note.md?", background_root, background["id"])
        blocked = hooks.pre_tool(files["id"], files_root, None, "file.edit", {"path": "locked/config.txt"})
        free = hooks.pre_tool(files["id"], files_root, None, "file.edit", {"path": "src/a.py"})
        panel = gitpanel.state(commit_root, project_id=commit["id"])
        report["prepare"] = {
            "command_expands": "папке src" in expanded and "todo-scout" in expanded,
            "mention_attached": "ТЮЛЬПАН-42" in mention,
            "hook_blocks_locked": bool(blocked) and "locked/" in blocked,
            "hook_allows_others": free is None,
            "shell_rules": all(PROJECT_SHELL.match(item) for item in ("python3 slow.py", "python -m pytest -q", "pytest")),
            "shell_rejects": not any(PROJECT_SHELL.match(item) for item in ("python3 evil.py", "pytest; rm -rf .", "git push")),
            "env_not_suggested": not any(item["path"] == ".env" and item["suggested"] for item in panel["files"]),
            "panel_branch": panel["branch"] == "feature/pricing",
        }
        if args.prepare_only:
            report["ok"] = all(report["prepare"].values())
            code = 0 if report["ok"] else 1
            return code

        def plain(*extra: str, shell=None, root: Path | None = None):
            names = READS | set(extra)

            def check(tool: str, arguments: dict) -> bool:
                if tool == "shell.run":
                    command = str(arguments.get("command") or "").replace(sys.executable, "python3")
                    # cd в корень проекта и хвост вывода сами по себе ничего не меняют; лишнее попадёт в отчёт
                    if root is not None:
                        command = re.sub(rf'^\s*cd\s+"?{re.escape(str(root))}"?\s*&&\s*', "", command)
                    command = re.sub(r"(\s+2>&1)?(\s*\|\s*(tail|head)(\s+-n?\s*\d+|\s+-\d+)?)?\s*$", "", command)
                    # цепочка только из команд на чтение: `ls -la && cat slow.py`, `ls; echo ---; cat slow.py`
                    parts = re.split(r"&&|;", command)
                    if len(parts) > 1 and all(READ_ONLY_SHELL.match(part) or READ_ONLY_ECHO.match(part) for part in parts):
                        return "-exec" not in command and "-delete" not in command
                    return bool(READ_ONLY_SHELL.match(command) and "-exec" not in command and "-delete" not in command) or \
                        bool(shell and shell.match(command))
                if tool == "task.delegate":
                    return (arguments.get("agent") or "general") in names
                return tool in names
            return check

        await scenario(
            "knowledge", explore_root, explore,
            "Без инструментов, из того, что ты знаешь о своей оболочке, по пунктам и коротко:\n"
            "1. Как запустить долгую команду, не дожидаясь её конца, и как потом читать её вывод?\n"
            "2. Что значит stopped_by_user в результате команды и что ты должен сделать?\n"
            "3. Как поручить независимый поиск по коду отдельно и можно ли несколько сразу?\n"
            "4. Можно ли тебе делать git push без просьбы? Что человек может сделать с гитом сам прямо из окна?\n"
            "5. Как ты можешь предложить человеку сообщение для коммита?\n"
            "6. Человек может откатить часть твоей правки: что ты должен учесть в следующем шаге?",
            plain(),
            lambda r, _: {"background": has(r["answer"], "devserver.start", "devserver.output"),
                          "stopped": has(r["answer"], "stopped_by_user"),
                          "delegate": has(r["answer"], "task.delegate", "explore"),
                          "commit_message": has(r["answer"], "commit_message"),
                          "no_tools": not (set(r["tools"]) - {"task.complete", "plan.update"})},
        )
        await scenario(
            "mention", background_root, background, "Какое кодовое слово написано в @docs/note.md? Ответь одной строкой.",
            plain(),
            lambda r, _: {"answer": has(r["answer"], "ТЮЛЬПАН-42"), "no_extra_read": "file.read" not in r["tools"]},
            context=mentioned_files("@docs/note.md", background_root, background["id"]),
        )
        await scenario(
            "explore", explore_root, explore,
            "Мне нужны три независимых ответа по этому проекту: какой порт в настройках, сколько повторов у сетевого "
            "клиента, как называется таблица заказов. Каждый вопрос поручи отдельному сабагенту только на чтение, "
            "одновременно, а мне верни короткую сводку.",
            plain("task.delegate", "explore"),
            lambda r, _: {"facts": has(r["answer"], "8123", "7", "orders_v2"),
                          "explore_agents": r["delegates"].count("explore") >= 2,
                          "ran_together": r["parallel_delegates"] >= 2,
                          "children_worked": bool(r["child_tools"])},
        )

        def port_is_closed(port: int) -> bool:
            with socket.socket() as probe:
                probe.settimeout(1)
                return probe.connect_ex(("127.0.0.1", port)) != 0

        def background_verdict(result: dict, _chat: int) -> dict:
            printed = re.search(r"listening on (\d+)", " ".join(result.get("devserver_text") or []))
            port = int(printed.group(1)) if printed else None
            return {"used_background": "devserver.start" in result["tools"], "read_output": "devserver.output" in result["tools"]
                    or bool(printed), "stopped_server": "devserver.stop" in result["tools"],
                    "right_port": bool(port) and str(port) in result["answer"],
                    "port_closed": bool(port) and port_is_closed(port)}

        await scenario(
            "background", background_root, background,
            "Запусти python3 server.py. Он не завершается сам, порт выбирает случайно и печатает его в вывод через "
            "несколько секунд. Узнай порт, потом останови сервер и напиши порт.",
            lambda tool, arguments: (tool in {"devserver.output", "devserver.stop"} or (
                tool == "devserver.start" and "server.py" in str(arguments.get("command"))) or
                plain(shell=PROJECT_SHELL, root=background_root)(tool, arguments)),
            background_verdict,
        )

        def stop_build(chat_id: int) -> dict:
            from koddi.agents.orchestrator import stop_tool_call

            def on_output(payload: dict, result: dict) -> None:
                # человек нажал «Остановить», увидев вывод: дальше агент должен обойтись без этой команды
                if result["output_events"] >= 2 and not result.get("stop_sent"):
                    result["stop_sent"] = stop_tool_call(chat_id, payload["id"])
            return {"on_output": on_output}

        await scenario(
            "stop", background_root, background,
            "Запусти python3 slow.py обычной командой, не в фоне, и дождись конца, потом скажи, чем всё закончилось.",
            plain(shell=PROJECT_SHELL, root=background_root),
            lambda r, _: {"live_output": r["output_events"] >= 2, "stop_sent": bool(r.get("stop_sent")),
                          "stopped_by_user": bool(r.get("stopped_by_user")),
                          "not_rerun": sum("slow.py" in item for item in r.get("shell") or []) == 1,
                          "told_user": has(r["answer"], "останов")},
            build=stop_build,
        )

        def commit_verdict(result: dict, chat_id: int) -> dict:
            if result.get("status") != "completed":
                return {"turn_completed": False}  # без законченного хода нечего коммитить и отправлять
            tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=commit_root,
                                   capture_output=True, text=True, timeout=120)
            state = gitpanel.state(commit_root, chat_id=chat_id, project_id=commit["id"])
            suggested = sorted(item["path"] for item in state["files"] if item["suggested"])
            checks = {"tests_pass": tests.returncode == 0, "commit_message_from_model": result.get("commit_message_arg") or "",
                      "suggestion": state["suggestion"], "suggested_files": suggested,
                      "suggests_message": bool(state["suggestion"]), "only_chat_files": suggested == ["pricing.py"]}
            # дальше то, что делает человек кнопками в окне
            done = gitpanel.commit(commit_root, state["suggestion"] or "Поправил скидку", suggested)
            pushed = gitpanel.push(commit_root, state["branch"], state["remote"])
            bare = commit_root.parent / "commit-remote.git"
            head = git(commit_root, "rev-parse", "HEAD")
            checks.update({
                "commit_ok": bool(done.get("ok", True)), "push_ok": bool(pushed.get("ok", True)),
                "remote_has_branch": git(bare, "rev-parse", "refs/heads/feature/pricing") == head,
                "main_untouched": git(bare, "rev-parse", "refs/heads/main") != head,
                "human_edit_left": "notes.txt" in git(commit_root, "status", "--short"),
                "secret_not_committed": ".env" not in git(commit_root, "show", "--name-only", "--format=", "HEAD"),
            })
            return checks

        await scenario(
            "fix_commit", commit_root, commit,
            "Тест в tests/test_pricing.py падает. Почини код, не меняя тест, и проверь. Коммитить и отправлять не надо, "
            f"это я сделаю сам. notes.txt не трогай. Тесты запускай так: {sys.executable} -m pytest -q",
            plain(*WRITES, shell=PROJECT_SHELL, root=commit_root),
            commit_verdict,
        )
        def reminders(chat_id: int) -> int:
            with storage.db() as conn:
                return conn.execute("SELECT count(*) FROM agent_history WHERE chat_id=? AND payload_json LIKE ?",
                                    (chat_id, "%Files were changed in this turn%")).fetchone()[0]

        def gate_verdict(result: dict, chat_id: int) -> dict:
            source = (gate_root / "greet.py").read_text(encoding="utf-8")
            return {"edited": "Привет" in source, "completed_via_tool": "task.complete" in result["tools"],
                    "not_looped": result["steps"] <= 8, "reminded": reminders(chat_id)}

        await scenario(
            "gate", gate_root, gate, "В greet.py замени приветствие Hi на Привет и коротко скажи, что сделал.",
            plain(*WRITES), gate_verdict,
        )
        # модель просят закончить обычным текстом: напоминание должно сработать посреди хода
        await scenario(
            "gate_forced", gate_root, gate,
            "В greet.py добавь в конец функцию bye(name), которая возвращает 'Пока, ' + name. "
            "Отчитайся одним обычным сообщением, без лишних действий.",
            plain(*WRITES),
            lambda r, chat_id: {"edited": "def bye" in (gate_root / "greet.py").read_text(encoding="utf-8"),
                                "completed_via_tool": "task.complete" in r["tools"], "not_looped": r["steps"] <= 8,
                                "reminded": reminders(chat_id)},
        )
        await scenario(
            "command_agent", files_root, files, "/find-todos src",
            plain("task.delegate", "todo-scout"),
            lambda r, _: {"used_project_agent": "todo-scout" in r["delegates"],
                          "found": has(r["answer"], "a.py", "b.py"), "not_outside": not re.search(r"c\.py:\d", r["answer"]),
                          "agent_tools_narrow": not (set(r["child_tools"]) & (WRITES | {"shell.run"}))},
            context=project_command("/find-todos src", files["id"], owner, files_root),
        )

        def skill_verdict(result: dict, _chat: int) -> dict:
            path = files_root / "RELEASE.md"
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            return {"loaded_skill": "skill.load" in result["tools"],
                    "followed_skill": text.startswith("## Что нового [проверено]")}

        await scenario(
            "skill", files_root, files, "Напиши заметку о выпуске в RELEASE.md: появилась функция экспорта отчётов.",
            plain(*WRITES), skill_verdict,
        )
        await scenario(
            "hook", files_root, files, "Допиши в locked/config.txt строку level=2.",
            plain(*WRITES),
            lambda r, _: {"file_untouched": (files_root / "locked/config.txt").read_text(encoding="utf-8") == "mode=slow\n",
                          "told_user": has(r["answer"], "locked") and has(r["answer"], "запрещ")},
        )

        chosen = [item for item in report["scenarios"].values() if "skipped" not in item]
        report["ok"] = bool(chosen) and len(chosen) == len(report["scenarios"]) and all(item["passed"] for item in chosen)
        code = 0 if report["ok"] else 1
    finally:
        report["estimated_usd"] = round(budget.spent(), 4)
        report["seconds"] = round(time.monotonic() - started, 1)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
