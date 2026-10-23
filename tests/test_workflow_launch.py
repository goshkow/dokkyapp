import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.agents.queue import request_queue
from koddi.automations.service import create_automation, run_automation, cancel_automation


def test_skill_execution_uses_persistent_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "skills.sqlite3")
    storage.init_db()
    monkeypatch.setattr(request_queue, "launch", None)
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"path": str(tmp_path)}).json()
    skill = client.post(
        "/api/skills",
        json={
            "name": "workflow",
            "description": "Inspect files",
            "workflow": {"steps": ["Read", "Verify"]},
        },
    ).json()
    import uuid

    payload = {"project_id": project["id"], "request_id": str(uuid.uuid4()), "task": "Read project"}
    # create_app связывает launcher; тест намеренно не запускает генерацию
    monkeypatch.setattr(request_queue, "launch", None)
    first = client.post(f"/api/skills/{skill['id']}/execute", json=payload)
    assert first.status_code == 200
    second = client.post(f"/api/skills/{skill['id']}/execute", json=payload)
    assert second.json()["chat"]["id"] == first.json()["chat"]["id"]
    assert len(first.json()["queue"]["items"]) == 1
    assert "Read project" in first.json()["queue"]["items"][0]["content"]


def test_workflow_cancel_reaches_executing_tool(monkeypatch):
    from koddi.tools import ToolRunner

    storage.init_db()
    started = threading.Event()

    def execute(self, *args, **kwargs):
        started.set()
        assert self.cancel_event.wait(3)
        return {"ok": False, "cancelled": True}

    monkeypatch.setattr(ToolRunner, "run", execute)
    automation = create_automation("cancel", {"tool": "logs.list"})
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_automation, automation["id"])
        assert started.wait(3)
        cancel_automation(automation["id"])
        assert future.result(timeout=3)["automation"]["status"] == "cancelled"


def test_unsupported_schedule_is_rejected():
    with pytest.raises(ValueError):
        create_automation("not scheduled", {}, schedule="every minute")
