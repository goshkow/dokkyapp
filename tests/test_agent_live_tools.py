"""Этап 1 паритета с Claude Code: живой вывод и остановка одной команды, фоновые команды, параллельные чтения."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from koddi.agents import orchestrator
from koddi.tools import runner as runner_module
from koddi.tools.runner import ToolRunner


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    import koddi.storage.db as storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "live-tools.sqlite3")
    storage.init_db()


def python_command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_command_output_reaches_listener_before_the_command_ends(tmp_path: Path):
    runner = ToolRunner(workspace=tmp_path)
    seen: list[tuple[float, str]] = []
    runner.output_listener = lambda text: seen.append((time.monotonic(), text))
    started = time.monotonic()
    result = runner.shell_run({"command": python_command(
        "import time; print('первая', flush=True); time.sleep(1); print('вторая', flush=True)"
    )})
    finished = time.monotonic()
    assert result["ok"] is True
    assert "первая" in result["stdout"] and "вторая" in result["stdout"]
    first = next(at for at, text in seen if "первая" in text)
    # первая строка пришла до конца команды, а не одной пачкой в конце
    assert first - started < finished - started - 0.5


def test_user_can_stop_one_command_without_cancelling_the_turn(tmp_path: Path):
    runner = ToolRunner(workspace=tmp_path)
    runner.reset_command()
    timer = threading.Timer(0.3, runner.stop_command)
    timer.start()
    result = runner.shell_run({"command": python_command("import time; time.sleep(30)"), "timeout_seconds": 60})
    timer.join()
    assert result["ok"] is False
    assert result["stopped_by_user"] is True
    assert "cancelled" not in result
    assert not runner.cancel_event.is_set()
    # следующая команда не наследует остановку
    runner.reset_command()
    assert runner.shell_run({"command": python_command("print(1)")})["ok"] is True


def test_background_command_output_and_finish_notice(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runner_module, "runtime_path", lambda *parts: tmp_path.joinpath(*parts))
    runner = ToolRunner(workspace=tmp_path)
    started = runner.run("devserver.start", {"command": python_command(
        "import time; print('сборка', flush=True); time.sleep(1.5); print('готово', flush=True)"
    ), "wait_seconds": 1}, approved=True)
    pid = started["pid"]
    assert started["running"] is True
    first = runner.run("devserver.output", {"pid": pid})
    assert "сборка" in first["output"]
    time.sleep(2)
    second = runner.run("devserver.output", {"pid": pid})
    # второе чтение отдаёт только новое
    assert "готово" in second["output"] and "сборка" not in second["output"]
    assert second["running"] is False and second["returncode"] == 0
    # прочитанное до конца завершение уже не приходит уведомлением
    assert "background_finished" not in runner.run("directory.list", {"path": "."})
    assert runner.run("devserver.output", {"pid": 999_999})["ok"] is False


def test_finished_background_command_is_reported_once(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runner_module, "runtime_path", lambda *parts: tmp_path.joinpath(*parts))
    runner = ToolRunner(workspace=tmp_path)
    started = runner.run("devserver.start", {"command": python_command("print('ок')"), "wait_seconds": 0}, approved=True)
    deadline = time.monotonic() + 5
    result = {}
    while time.monotonic() < deadline:
        result = runner.run("directory.list", {"path": "."})
        if "background_finished" in result:
            break
        time.sleep(0.1)
    assert result["background_finished"][0]["pid"] == started["pid"]
    assert result["background_finished"][0]["returncode"] == 0
    assert "background_finished" not in runner.run("directory.list", {"path": "."})


class SlowReads:
    """Чтения по 0,4 с: последовательно шесть заняли бы 2,4 с."""

    def __init__(self):
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def run(self, name, args, approved=False):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.4)
        with self.lock:
            self.active -= 1
        return {"ok": True, "path": args.get("path")}


def call(index: int, name: str, path: str = "a.py") -> dict:
    return {"id": f"call-{index}", "name": name, "arguments": json.dumps({"path": path})}


async def no_hook(name, args):
    return None


@pytest.mark.asyncio
async def test_leading_reads_run_together_and_stop_at_first_write():
    runner = SlowReads()
    calls = [call(1, "file.read", "a.py"), call(2, "search.text", "b"), call(3, "file.read", "c.py"),
             call(4, "file.edit", "a.py"), call(5, "file.read", "d.py")]
    started = time.monotonic()
    tasks = await orchestrator.prefetch_reads(calls, runner, threading.Event(), usable=lambda name, args: True, hook=no_hook)
    assert set(tasks) == {"call-1", "call-2", "call-3"}
    results = [await tasks[key] for key in ("call-1", "call-2", "call-3")]
    assert time.monotonic() - started < 1.0
    assert runner.peak == 3
    assert [outcome["result"]["path"] for outcome, _ in results] == ["a.py", "b", "c.py"]


@pytest.mark.asyncio
async def test_nothing_is_prefetched_without_a_parallel_gain():
    runner = SlowReads()
    single = await orchestrator.prefetch_reads([call(1, "file.read"), call(2, "shell.run")], runner, threading.Event(),
                                               usable=lambda name, args: True, hook=no_hook)
    assert single == {}
    # повтор того же чтения и недоступные вызовы идут обычным порядком
    calls = [call(1, "file.read", "a.py"), call(2, "file.read", "a.py"), call(3, "file.read", "b.py")]
    tasks = await orchestrator.prefetch_reads(calls, runner, threading.Event(),
                                              usable=lambda name, args: args["path"] != "b.py", hook=no_hook)
    assert tasks == {}


@pytest.mark.asyncio
async def test_plugin_hook_still_blocks_a_prefetched_read():
    runner = SlowReads()

    async def hook(name, args):
        return "секреты не читать" if args["path"] == ".env" else None

    tasks = await orchestrator.prefetch_reads([call(1, "file.read", ".env"), call(2, "file.read", "a.py")], runner,
                                              threading.Event(), usable=lambda name, args: True, hook=hook)
    blocked, _ = await tasks["call-1"]
    allowed, _ = await tasks["call-2"]
    assert blocked == {"hook_block": "секреты не читать"}
    assert allowed["result"]["ok"] is True


@pytest.mark.asyncio
async def test_tool_events_carry_live_output(tmp_path: Path):
    runner = ToolRunner(workspace=tmp_path)
    output = orchestrator.queue.SimpleQueue()
    runner.output_listener = output.put
    updates = [update async for update in orchestrator.execute_tool_events(
        runner, "shell.run", {"command": python_command(
            "import time; print('раз', flush=True); time.sleep(1); print('два', flush=True)"
        )}, threading.Event(), approved=True, output=output,
    )]
    texts = [update["output"] for update in updates if "output" in update]
    assert len(texts) >= 2 and "раз" in texts[0]
    assert "два" in "".join(texts)
    assert updates[-1]["result"]["ok"] is True


def test_stop_reaches_only_the_running_call():
    class Runner:
        stopped = 0

        def stop_command(self):
            self.stopped += 1

    runner = Runner()
    with orchestrator._running_lock:
        orchestrator._running_tools[77] = ("call-9", runner)
    try:
        assert orchestrator.stop_tool_call(77, "call-8") is False
        assert orchestrator.stop_tool_call(78, "call-9") is False
        assert orchestrator.stop_tool_call(77, "call-9") is True
        assert runner.stopped == 1
        orchestrator._forget_running(77, object())
        assert 77 in orchestrator._running_tools
        orchestrator._forget_running(77, runner)
        assert 77 not in orchestrator._running_tools
    finally:
        orchestrator._running_tools.pop(77, None)


def test_external_paths_are_per_thread(tmp_path: Path):
    runner = ToolRunner(workspace=tmp_path)
    runner._external = {"/внешний"}
    seen = []
    worker = threading.Thread(target=lambda: seen.append(runner._external))
    worker.start()
    worker.join()
    assert seen == [set()] and runner._external == {"/внешний"}


def test_read_cache_survives_parallel_reads(tmp_path: Path):
    for index in range(20):
        (tmp_path / f"{index}.txt").write_text("x" * 1000 * (index + 1), encoding="utf-8")
    runner = ToolRunner(workspace=tmp_path)
    errors = []

    def read(index):
        for _ in range(30):
            result = runner.run("file.read", {"path": f"{index % 20}.txt"})
            if result.get("ok") is False:
                errors.append(result)

    threads = [threading.Thread(target=read, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_stop_endpoint_answers_404_for_a_finished_command(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    api_app = importlib.import_module("koddi.api.app")
    monkeypatch.setattr(api_app, "require_chat", lambda chat_id: {"id": chat_id})
    client = TestClient(api_app.create_app(), base_url="http://127.0.0.1:8765")
    assert asyncio.iscoroutinefunction(orchestrator.prefetch_reads)
    assert client.post("/api/chats/5/tools/call-1/stop").status_code == 404
