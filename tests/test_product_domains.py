from __future__ import annotations

import io
import os

os.environ.setdefault("KODDI_DATA_DIR", os.path.abspath(".test-data-domains"))
os.environ.setdefault("koddi_PORTABLE", "1")

from fastapi.testclient import TestClient

from koddi.api import create_app


def test_mission_control_automations_and_skills() -> None:
    client = TestClient(create_app())
    task = client.post("/api/tasks", json={"title": "Implement feature", "mode": "build"}).json()
    assert task["title"] == "Implement feature"

    graph = client.get("/api/mission-control").json()
    assert any(node["id"] == task["id"] for node in graph["nodes"])

    automation = client.post(
        "/api/automations",
        json={"name": "CI monitor", "schedule": "manual", "action": {"type": "check_ci"}},
    ).json()
    assert automation["name"] == "CI monitor"
    assert client.post(f"/api/automations/{automation['id']}/run").json()["ok"] is True

    skill = client.post(
        "/api/skills",
        json={
            "name": "review-flow",
            "description": "Review changed files",
            "workflow": {"steps": ["diff", "tests"]},
        },
    ).json()
    assert skill["workflow"]["steps"] == ["diff", "tests"]


def test_attachment_upload_and_document_index(tmp_path) -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/attachments",
        files={"file": ("note.txt", io.BytesIO(b"hello document"), "text/plain")},
    )
    assert response.status_code == 200
    attachment = response.json()
    assert attachment["original_name"] == "note.txt"

    document = client.post(
        "/api/documents/index",
        json={"path": attachment["stored_path"], "attachment_id": attachment["id"]},
    ).json()
    assert document["kind"] == "txt"


def test_full_tool_schema_contains_phase_two_three_tools() -> None:
    client = TestClient(create_app())
    names = {item["function"]["name"] for item in client.get("/api/tools/schema").json()}
    required = {
        "workspace.snapshot",
        "git.worktree_create",
        "browser.open",
        "document.parse",
        "automation.create",
        "skill.create",
        "safety.review",
        "mcp.tool_call",
    }
    assert required.issubset(names)
    assert "task.spawn_parallel" not in names
    # извлечение функции теперь настоящее (codeintel), а не запись заявки
    assert {"refactor.extract", "code.definition", "code.symbol_edit"}.issubset(names)
