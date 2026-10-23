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
