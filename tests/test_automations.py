from koddi.automations import cancel_automation, create_automation, run_automation
from koddi.storage.db import set_setting


def test_automation_runs_registered_tool_and_cancels() -> None:
    set_setting("permission_profile", "read-only")
    automation = create_automation("Read logs", {"tool": "logs.list", "arguments": {"limit": 1}})
    result = run_automation(automation["id"])
    assert result["ok"] is True
    assert result["automation"]["status"] == "completed"
    cancelled = cancel_automation(automation["id"])
    assert cancelled["ok"] is True
    assert cancelled["automation"]["status"] == "cancelled"


def test_automation_left_running_by_a_crash_returns_to_schedule() -> None:
    from koddi.automations.service import _LOCK, _RUNNING, due_automations, recover_interrupted
    from koddi.storage.db import db

    set_setting("permission_profile", "read-only")
    stuck = create_automation("Stuck", {"tool": "logs.list", "arguments": {"limit": 1}}, "hourly")
    live = create_automation("Live", {"tool": "logs.list", "arguments": {"limit": 1}}, "hourly")
    with db() as conn:
        conn.execute("UPDATE automations SET status='running', last_run_at='2000-01-01 00:00:00' WHERE id IN (?, ?)",
                      (stuck["id"], live["id"]))
    with _LOCK:
        _RUNNING[live["id"]] = object()
    try:
        recover_interrupted()
        with db() as conn:
            rows = dict(conn.execute("SELECT id, status FROM automations WHERE id IN (?, ?)", (stuck["id"], live["id"])).fetchall())
    finally:
        with _LOCK:
            _RUNNING.pop(live["id"], None)
    assert rows == {stuck["id"]: "failed", live["id"]: "running"}
    assert stuck["id"] in {item["id"] for item in due_automations()}
