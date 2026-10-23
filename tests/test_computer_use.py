import base64
import io
import sys
import threading
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.computer_use import service
from koddi.tools import ToolRunner


@pytest.fixture
def computer(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "computer.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "user-a"})
    with storage.db() as conn:
        project = conn.execute(
            "INSERT INTO projects(name,path) VALUES ('p',?)", (str(tmp_path),)
        ).lastrowid
        chat = conn.execute(
            "INSERT INTO chats(title,project_id,owner_id) VALUES ('a',?,'user-a')", (project,)
        ).lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
    seen = []
    png = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(png, format="PNG")

    def native(request, cancel_event, **kwargs):
        seen.append(deepcopy(request))
        if request["action"] == "observe":
            return {
                "ok": True,
                "identity": "test-instance",
                "elements": [
                    {
                        "path": "0",
                        "title": "Test",
                        "role": "button",
                        "protected": False,
                        "enabled": True,
                        "frame": [0, 0, 10, 10],
                    }
                ],
            }
        if request["action"] == "screenshot":
            return {"ok": True, "png_base64": base64.b64encode(png.getvalue()).decode()}
        return {"ok": True}

    monkeypatch.setattr(service, "invoke_native", native)
    return {
        "path": tmp_path,
        "project": project,
        "chat": chat,
        "turn": turn,
        "seen": seen,
        "runner": ToolRunner(tmp_path, project, turn),
    }


def enable(computer):
    service.set_consent(True)
    return computer["runner"]


def observe(runner):
    return runner.run("computer.use", {"action": "observe", "pid": 100}, approved=True)


def test_default_enabled_and_each_exact_call_needs_approval_even_when_trusted(computer):
    runner = computer["runner"]
    storage.set_setting("permission_profile", "trusted")
    assert service.consent_enabled()
    service.set_consent(False)
    assert runner.run("computer.use", {"action": "observe", "pid": 100})["blocked"]
    enable(computer)
    assert runner.run("computer.use", {"action": "observe", "pid": 100})["approval_required"]
    assert not computer["seen"]
    assert observe(runner)["ok"]
    assert len(computer["seen"]) == 1
    storage.set_setting("permission_profile", "read-only")
    assert runner.run("computer.use", {"action": "observe", "pid": 100}, approved=True)["blocked"]


def test_structured_element_has_session_bound_snapshot_and_live_identity(computer):
    runner = enable(computer)
    snapshot = observe(runner)
    args = {
        "action": "press",
        "pid": 100,
        "snapshot_id": snapshot["snapshot_id"],
        "element_id": snapshot["elements"][0]["id"],
    }
    other = ToolRunner(computer["path"], computer["project"], computer["turn"])
    assert other.run("computer.use", args, approved=True)["ok"] is False
    assert runner.run("computer.use", args, approved=True)["ok"]
    request = computer["seen"][-1]
    assert request["identity"] == "test-instance" and request["element_path"] == "0"
    assert request["expected"]["title"] == "Test" and request["expected_frame"] == [0, 0, 10, 10]
    assert runner.run("computer.use", args, approved=True)["ok"] is False


@pytest.mark.parametrize("patch", [{"pid": 101}, {"snapshot_id": "0" * 32}, {"extra": "value"}])
def test_wrong_target_snapshot_and_extra_fields_cannot_execute(computer, patch):
    runner = enable(computer)
    snapshot = observe(runner)
    args = {"action": "focus", "pid": 100, "snapshot_id": snapshot["snapshot_id"], **patch}
    assert runner.run("computer.use", args, approved=True)["ok"] is False
    assert len(computer["seen"]) == 1


def test_expired_snapshot_and_owner_switch_deny(computer):
    runner = enable(computer)
    snapshot = observe(runner)
    runner.computer_session.snapshots[snapshot["snapshot_id"]]["at"] -= 31
    assert (
        runner.run(
            "computer.use",
            {"action": "focus", "pid": 100, "snapshot_id": snapshot["snapshot_id"]},
            approved=True,
        )["ok"]
        is False
    )
    storage.set_setting("dokky_user", {"id": "user-b"})
    assert service.consent_enabled()
    assert observe(runner)["ok"] is False
    assert len(computer["seen"]) == 1


def test_missing_active_task_and_cancel_deny(computer):
    enable(computer)
    standalone = ToolRunner(computer["path"], computer["project"])
    assert observe(standalone)["ok"] is False
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE id=?", (computer["turn"],))
    assert observe(computer["runner"])["ok"] is False
    computer["runner"].cancel_event.set()
    assert observe(computer["runner"])["cancelled"]
    assert not computer["seen"]


def test_screen_is_private_attachment_not_base64_in_journal_or_other_account(computer):
    runner = enable(computer)
    snapshot = observe(runner)
    result = runner.run(
        "computer.use",
        {
            "action": "screenshot",
            "pid": 100,
            "snapshot_id": snapshot["snapshot_id"],
            "window_id": 1,
        },
        approved=True,
    )
    assert result["ok"] and "png_base64" not in result
    client = TestClient(create_app())
    response = client.get(result["image_url"])
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    storage.set_setting("dokky_user", {"id": "user-b"})
    assert client.get(result["image_url"]).status_code == 404
    with storage.db() as conn:
        raw = "\n".join(row[0] for row in conn.execute("SELECT result_json FROM tool_calls"))
    assert "png_base64" not in raw


def test_consent_api_does_not_accept_truthy_strings(computer):
    client = TestClient(create_app())
    assert client.put("/api/computer/consent", json={"enabled": "true"}).status_code == 422
    assert service.consent_enabled()
    assert client.put("/api/computer/consent", json={"enabled": True}).status_code == 200
    assert service.consent_enabled()
    assert client.put("/api/computer/consent", json={"enabled": False}).status_code == 200
    assert service._revoked.is_set()


def test_native_worker_timeout_and_cancellation_do_not_leave_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service,
        "native_command",
        lambda: [sys.executable, "-c", "import sys,time;sys.stdin.buffer.read();time.sleep(30)"],
    )
    stop = threading.Event()
    timer = threading.Timer(0.1, stop.set)
    timer.start()
    try:
        with pytest.raises(ValueError, match="остановлено"):
            service.invoke_native({"action": "status"}, stop)
    finally:
        timer.join()


def test_native_output_is_bounded(monkeypatch):
    monkeypatch.setattr(
        service,
        "native_command",
        lambda: [
            sys.executable,
            "-c",
            "import sys;sys.stdin.buffer.read();sys.stdout.write('x'*2200000)",
        ],
    )
    with pytest.raises(ValueError):
        service.invoke_native({"action": "status"}, threading.Event())


def test_password_elements_never_reach_native_action(computer):
    runner = enable(computer)
    snapshot = observe(runner)
    element_id = snapshot["elements"][0]["id"]
    runner.computer_session.snapshots[snapshot["snapshot_id"]]["elements"][element_id][
        "protected"
    ] = True
    result = runner.run(
        "computer.use",
        {
            "action": "type_element",
            "pid": 100,
            "snapshot_id": snapshot["snapshot_id"],
            "element_id": element_id,
            "text": "private",
        },
        approved=True,
    )
    assert not result["ok"] and len(computer["seen"]) == 1


def test_rejected_private_text_is_not_echoed_into_diagnostic_logs(computer):
    private = "confidential-sentinel-" * 150
    result = computer["runner"].run(
        "computer.use",
        {"action": "type", "pid": 100, "snapshot_id": "0" * 32, "text": private},
        approved=True,
    )
    assert not result["ok"] and private not in result["error"]
    with storage.db() as conn:
        entries = "\n".join(row[0] for row in conn.execute("SELECT data_json FROM logs"))
    assert "confidential-sentinel" not in entries
    assert not computer["seen"]


@pytest.mark.skipif(sys.platform != "win32", reason="помощник Windows")
def test_windows_helper_status_and_ascii_request():
    assert service.invoke_native({"action": "status"}, threading.Event())["screen_capture"] is True
    # кириллица уходит в помощник экранированной, кодировка stdin PowerShell не важна
    result = service.invoke_native(
        {"action": "launch", "application": r"C:\Windows\System32\не-существует.exe"}, threading.Event()
    )
    assert result["ok"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="оболочка Windows")
def test_windows_shell_reports_parse_errors_in_utf8():
    import subprocess

    from koddi.tools.runner import shell_argv

    done = subprocess.run(
        shell_argv("echo привет"), capture_output=True, timeout=30
    )
    assert done.stdout.decode("utf-8").strip() == "привет"
    broken = subprocess.run(shell_argv("echo a & echo b"), capture_output=True, timeout=30)
    assert broken.returncode != 0 and "&" in broken.stderr.decode("utf-8")


def test_click_element_sends_centre_of_snapshot_element(computer):
    runner = enable(computer)
    snapshot = observe(runner)
    element = next(iter(runner.computer_session.snapshots[snapshot["snapshot_id"]]["elements"]))
    args = {"action": "click_element", "pid": 100, "snapshot_id": snapshot["snapshot_id"], "element_id": element, "clicks": 2}
    assert runner.run("computer.use", args, approved=True)["ok"]
    sent = computer["seen"][-1]
    assert sent["action"] == "click" and (sent["x"], sent["y"]) == (5, 5) and sent["clicks"] == 2
    assert sent["element_path"] == "0"


def test_list_windows_needs_no_pid_and_launch_options_are_validated(computer, tmp_path):
    runner = enable(computer)
    assert runner.run("computer.use", {"action": "list_windows", "query": "блокнот"}, approved=True)["ok"]
    assert computer["seen"][-1] == {"action": "list_windows", "query": "блокнот"}
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"MZ")
    good = {"action": "launch", "application": str(exe), "arguments": ["--debug", "a b"], "working_directory": str(tmp_path)}
    assert runner.run("computer.use", good, approved=True)["ok"]
    for patch in (
        {"arguments": "--debug"},
        {"arguments": ["x"] * 21},
        {"arguments": ["a\x00b"]},
        {"working_directory": "relative"},
        {"working_directory": str(tmp_path / "нет")},
    ):
        before = len(computer["seen"])
        assert runner.run("computer.use", {**good, **patch}, approved=True)["ok"] is False
        assert len(computer["seen"]) == before
    for patch in ({"button": "middle"}, {"clicks": 3}, {"clicks": True}):
        snapshot = observe(runner)
        args = {"action": "click", "pid": 100, "snapshot_id": snapshot["snapshot_id"], "x": 1, "y": 1, **patch}
        assert runner.run("computer.use", args, approved=True)["ok"] is False


def test_native_error_code_gets_actionable_hint(computer, monkeypatch):
    runner = enable(computer)
    monkeypatch.setattr(
        service, "invoke_native", lambda request, cancel, **kw: {"ok": False, "error_code": "needs_elevation"}
    )
    result = runner.run("computer.use", {"action": "observe", "pid": 100}, approved=True)
    assert "администратор" in str(result)
