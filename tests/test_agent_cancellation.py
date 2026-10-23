from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from koddi.agents.runs import AgentRunRegistry
from koddi.tools.runner import ToolRunner


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    import koddi.storage.db as storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "cancellation.sqlite3")
    storage.init_db()


@pytest.mark.asyncio
async def test_active_run_can_be_cancelled_immediately() -> None:
    cancel_event = threading.Event()

    async def endless_stream():
        yield "event: turn.started\ndata: {}\n\n"
        await asyncio.Event().wait()

    registry = AgentRunRegistry()
    run = await registry.start(999_001, endless_stream(), cancel_event=cancel_event)
    await asyncio.sleep(0)

    assert await registry.cancel(999_001) is True
    assert cancel_event.is_set()
    assert run.completed is True
    assert any("event: turn.cancelled" in event for event in run.events)
    assert registry.active(999_001) is False


@pytest.mark.asyncio
async def test_shell_tool_observes_cancel_token(tmp_path: Path) -> None:
    cancel_event = threading.Event()
    runner = ToolRunner(workspace=tmp_path, cancel_event=cancel_event)
    task = asyncio.create_task(
        asyncio.to_thread(
            runner.shell_run,
            {"command": "Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30", "timeout_seconds": 60},
        )
    )
    await asyncio.sleep(0.2)
    cancel_event.set()
    result = await asyncio.wait_for(task, timeout=3)

    assert result["ok"] is False
    assert result["cancelled"] is True
