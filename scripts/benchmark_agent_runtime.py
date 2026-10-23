"""Офлайн replay одинаковых задач и tool traces. Не измеряет качество или цену реальной модели."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from time import monotonic


SCENARIOS = [
    (
        "new_project",
        {},
        [
            ("file.create", {"path": "main.py", "content": "def greet():\n    return 'Привет'\n"}),
            ("verification.run", {"paths": ["main.py"]}),
        ],
        {"main.py": "def greet():\n    return 'Привет'\n"},
    ),
    (
        "react_edit",
        {"App.tsx": "export const App = () => <h1>Hello</h1>;\n"},
        [("file.edit", {"path": "App.tsx", "old_text": "Hello", "new_text": "Привет"})],
        {"App.tsx": "export const App = () => <h1>Привет</h1>;\n"},
    ),
    (
        "backend_api",
        {},
        [
            (
                "file.create",
                {
                    "path": "api.py",
                    "content": "from http.server import BaseHTTPRequestHandler\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        self.send_response(200)\n        self.end_headers()\n        self.wfile.write(b'healthy')\n",
                },
            ),
            ("verification.run", {"paths": ["api.py"]}),
        ],
        {},
    ),
    (
        "bug_fix",
        {"maths.py": "def ratio(a, b):\n    return a / b\n"},
        [
            ("file.read", {"path": "maths.py"}),
            (
                "file.edit",
                {
                    "path": "maths.py",
                    "old_text": "return a / b",
                    "new_text": "return a / b if b else None",
                },
            ),
            ("verification.run", {"paths": ["maths.py"]}),
        ],
        {"maths.py": "def ratio(a, b):\n    return a / b if b else None\n"},
    ),
    (
        "multi_file_refactor",
        {
            "source.py": "def old():\n    return 42\n",
            "consumer.py": "from source import old\nvalue = old()\n",
        },
        [
            ("file.edit", {"path": "source.py", "old_text": "old", "new_text": "renamed"}),
            (
                "file.edit",
                {
                    "path": "consumer.py",
                    "old_text": "old",
                    "new_text": "renamed",
                    "replace_all": True,
                },
            ),
            ("verification.run", {"paths": ["source.py", "consumer.py"]}),
        ],
        {"consumer.py": "from source import renamed\nvalue = renamed()\n"},
    ),
    (
        "large_repository",
        {"target.py": "value = 7\n"},
        [
            ("search.files", {"query": "target.py"}),
            ("file.read", {"path": "target.py"}),
            ("search.files", {"query": "target.py"}),
            ("file.read", {"path": "target.py"}),
        ],
        {"target.py": "value = 7\n"},
    ),
    (
        "add_tests",
        {"maths.py": "def add(a, b):\n    return a + b\n"},
        [
            (
                "file.create",
                {
                    "path": "test_maths.py",
                    "content": "import unittest\nfrom maths import add\nclass Tests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
                },
            ),
            ("verification.run", {"paths": ["test_maths.py"]}),
        ],
        {},
    ),
    (
        "compile_fix",
        {"broken.py": "def fixed(:\n    return True\n"},
        [
            (
                "file.edit",
                {"path": "broken.py", "old_text": "def fixed(:", "new_text": "def fixed():"},
            ),
            ("verification.run", {"paths": ["broken.py"]}),
        ],
        {"broken.py": "def fixed():\n    return True\n"},
    ),
    (
        "git_workflow",
        {"main.py": "value = 1\n"},
        [("git.status", {}), ("git.diff", {}), ("file.read", {"path": "main.py"})],
        {"main.py": "value = 1\n"},
    ),
    (
        "resume_session",
        {"main.py": "value = 1\n"},
        [("file.read", {"path": "main.py"})],
        {"main.py": "value = 1\n"},
    ),
    (
        "completed_context_restore",
        {"main.py": "value = 1\n"},
        [("file.read", {"path": "main.py"})],
        {"main.py": "value = 1\n"},
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        parser.error("repetitions: 1..10")
    root = args.repository.resolve(strict=True)
    sys.path.insert(0, str(root / "backend"))
    with tempfile.TemporaryDirectory(prefix="dokky-agent-replay-") as temporary:
        os.environ.update(KODDI_INTERNAL_TEST="1", KODDI_DATA_DIR=str(Path(temporary) / "profile"))
        import koddi.agents.orchestrator as agent
        import koddi.storage.db as storage
        from koddi.dokky import coding
        from koddi.tools.runner import ToolRunner

        storage.init_db()
        storage.set_setting("dokky_user", {"id": "replay-user"})
        storage.set_setting("permission_profile", "trusted")
        agent.remote_config = lambda: {
            "provider": "dokky",
            "model": "dokky-code",
            "context_tokens": 200000,
        }

        async def discover():
            return [], {}, []

        agent.discover_agent_tools = discover
        states = {}

        async def ensure(chat_id):
            return states[chat_id]

        coding.ensure = ensure
        coding.load = lambda chat_id: states[chat_id]
        coding.pending = lambda _: None
        results = []

        class ScriptedModel:
            config = {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000}

            def __init__(self, chat_id, operations):
                self.chat_id, self.operations = chat_id, iter(operations)
                self.requests = 0
                self.context_bytes = 0

            async def stream(self, **request):
                self.requests += 1
                self.context_bytes += len(json.dumps(request, ensure_ascii=False).encode())
                if not request["tools"]:
                    yield {"content": "Тестовый заголовок", "finish_reason": "stop"}
                    return
                operation = next(self.operations, None)
                if operation:
                    name, arguments = operation
                    call = {
                        "id": "call_" + str(self.requests),
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    }
                    assistant = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": name.replace(".", "__"),
                                    "arguments": call["arguments"],
                                },
                            }
                        ],
                    }
                    reply = {"tool_call": call, "finish_reason": "tool_calls"}
                else:
                    assistant = {"role": "assistant", "content": "Операции проверены"}
                    reply = {"content": assistant["content"], "finish_reason": "stop"}
                value = states[self.chat_id]
                value["messages"] = deepcopy([*request["messages"], assistant])
                value["tools"] = deepcopy(request["tools"])
                for item in value["tools"]:
                    item["function"]["name"] = item["function"]["name"].replace(".", "__")
                yield reply

        async def run_case(name, initial, operations, expected, repetition):
            workspace = Path(temporary).resolve() / f"{name}-{repetition}"
            workspace.mkdir()
            for filename, content in initial.items():
                (workspace / filename).write_text(content, encoding="utf-8")
            if name == "large_repository":
                for number in range(2500):
                    path = workspace / f"module{number // 100}" / f"file{number}.py"
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(f"value = {number}\n")
            if name == "git_workflow":
                subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
            with storage.db() as conn:
                project = conn.execute(
                    "INSERT INTO projects(name,path) VALUES (?,?)", (name, str(workspace))
                ).lastrowid
                chat = conn.execute(
                    "INSERT INTO chats(project_id,title,mode,owner_id) VALUES (?,'Новый чат','development','replay-user')",
                    (project,),
                ).lastrowid
            states[chat] = {
                "session": {"id": "cs_" + f"{chat:024x}", "history": {"length": 0}},
                "messages": [],
                "tools": [],
                "history": None,
            }
            model = ScriptedModel(chat, operations)
            started = monotonic()
            statuses = []
            for turn in range(2 if name in {"resume_session", "completed_context_restore"} else 1):
                events = [
                    event
                    async for event in agent.AgentOrchestrator(model).run(
                        chat_id=chat,
                        project_id=project,
                        workspace=workspace,
                        user_message=f"{name}: этап {turn + 1}",
                    )
                ]
                statuses.append("event: turn.completed" in events[-1])
                assert statuses[-1], events[-1]
            for filename, content in expected.items():
                assert (workspace / filename).read_text(encoding="utf-8") == content, (
                    name,
                    filename,
                )
            if name != "large_repository":
                for path in workspace.glob("*.py"):
                    compile(path.read_bytes(), path.name, "exec")
            if name == "add_tests":
                subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-q"],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
            if name == "backend_api":
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from api import Handler; from http.server import HTTPServer; import threading,urllib.request; s=HTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=s.serve_forever);t.start();r=urllib.request.urlopen('http://127.0.0.1:%s/'%s.server_port);assert r.status==200 and r.read()==b'healthy';s.shutdown();t.join();s.server_close()",
                    ],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
            with storage.db() as conn:
                calls = conn.execute(
                    "SELECT status FROM tool_calls WHERE turn_id IN (SELECT id FROM turns WHERE chat_id=?)",
                    (chat,),
                ).fetchall()
            assert all(statuses) and all(row["status"] == "ok" for row in calls), (
                name,
                statuses,
                [row["status"] for row in calls],
            )
            # отдельная проверка инвалидирования кэша на реальных файлах и том же trace
            runner = ToolRunner(workspace, project)
            (workspace / "cache_probe.py").write_text("value = 1\n")
            reads = 0
            original_open = Path.open

            def count_open(path, *params, **kwargs):
                nonlocal reads
                if path == workspace / "cache_probe.py" and params and params[0] == "rb":
                    reads += 1
                return original_open(path, *params, **kwargs)

            Path.open = count_open
            try:
                runner.run("file.read", {"path": "cache_probe.py"})
                runner.run("search.files", {"query": "cache_probe.py"})
                assert (
                    runner.run("file.read", {"path": "cache_probe.py"})["content"] == "value = 1\n"
                )
                (workspace / "cache_probe.py").write_text("value = 2\n")
                assert (
                    runner.run("file.read", {"path": "cache_probe.py"})["content"] == "value = 2\n"
                )
            finally:
                Path.open = original_open
            return {
                "scenario": name,
                "repetition": repetition,
                "oracle_passed": True,
                "logical_model_requests": model.requests,
                "tool_calls": len(calls),
                "internal_context_json_bytes": model.context_bytes,
                "cache_probe_disk_reads": reads,
                "duration_seconds": round(monotonic() - started, 4),
                "input_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "api_cost": None,
            }

        async def run_all():
            for repetition in range(args.repetitions):
                for case in SCENARIOS:
                    results.append(await run_case(*case, repetition))

        asyncio.run(run_all())
        report = {
            "kind": "offline_scripted_replay",
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
            "model": "deterministic_script_not_an_LLM",
            "reasoning": "not_applicable",
            "results": results,
            "limitations": [
                "Нет запросов к LLM; качество модели и реальная стоимость не измерены.",
                "JSON bytes описывают внутренний контекст теста, а не wire traffic или оплаченные токены.",
                "React проверяется точным oracle текста; нативный Computer Use проверяется отдельным smoke-сценарием.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "runs": len(results),
                    "oracle_passed": sum(row["oracle_passed"] for row in results),
                    "logical_model_requests": sum(row["logical_model_requests"] for row in results),
                    "cache_probe_disk_reads": sum(row["cache_probe_disk_reads"] for row in results),
                }
            )
        )


if __name__ == "__main__":
    main()
