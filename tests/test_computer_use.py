import io
import json
import stat
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.computer_use import service
from koddi.tools import ToolRunner

# подставной agent-desktop: пишет вызовы в журнал и отвечает JSON-конвертами
FAKE = r'''#!{python}
import json, os, sys, time
args = sys.argv[1:]
config = json.load(open(os.environ["FAKE_CONFIG"])) if os.path.exists(os.environ["FAKE_CONFIG"]) else {{}}
stdin = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": args, "stdin": stdin, "home": os.environ.get("AGENT_DESKTOP_HOME"),
                           "leak": os.environ.get("AGENT_DESKTOP_SESSION")}}) + "\n")
rest = list(args)
while rest and rest[0] in ("--session", "--headed"):
    rest = rest[2:] if rest[0] == "--session" else rest[1:]
command = rest[0] if rest else ""
def flag(name):
    for item in rest:
        if item.startswith("--" + name + "="):
            return item.split("=", 1)[1]
def out(data=None, ok=True, error=None):
    envelope = {{"version": "2.6", "ok": ok, "command": command}}
    envelope["data" if ok else "error"] = data if ok else error
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False))
    sys.exit(0 if ok else 1)
if config.get("sleep"):
    time.sleep(config["sleep"])
if config.get("big"):
    sys.stdout.write("x" * 5_000_000)
    sys.exit(0)
if config.get("garbage"):
    sys.stdout.write("not json")
    sys.exit(0)
if command == "session":
    if rest[1] == "start":
        out({{"session_id": "sess-1", "rendered": config.get("rendered", True)}})
    out({{}})
if command == "cursor-overlay":
    out({{}})
if config.get("error") and command not in ("status", "skills"):
    out(ok=False, error=config["error"])
if command == "status":
    out({{"permissions": config.get("permissions", {{"accessibility": {{"state": "granted"}}, "screen_recording": {{"state": "denied"}}}}), "version": "1.2.0"}})
if command == "skills":
    out({{"content": "GUIDE " + " ".join(rest[2:])}})
if command == "list-windows":
    out([{{"id": "w-7", "app_name": "Finder", "pid": 1, "title": "t"}}])
if command == "snapshot":
    out({{"app": flag("app"), "snapshot_id": "snap1", "tree": {{}}}})
if command == "screenshot":
    open(rest[-1], "wb").write(open(config["png"], "rb").read())
    out({{"path": rest[-1]}})
out({{}})
'''


def png(path, size=(10, 10)):
    Image.new("RGB", size, "red").save(path, format="PNG")
    return path


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
    binary = tmp_path / "agent-desktop"
    binary.write_text(FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log, config = tmp_path / "calls.jsonl", tmp_path / "config.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_CONFIG", str(config))
    monkeypatch.setenv("AGENT_DESKTOP_SESSION", "outer")
    monkeypatch.setattr(service, "binary_path", lambda: binary)
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
    (tmp_path / "data").mkdir()
    service._sessions.clear()
    service._snapshot_apps.clear()
    service._window_apps.clear()
    service._guide_cache.clear()
    service._revoked.clear()

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def configure(**values):
        config.write_text(json.dumps(values))

    yield {
        "path": tmp_path,
        "project": project,
        "chat": chat,
        "turn": turn,
        "calls": calls,
        "configure": configure,
        "runner": ToolRunner(tmp_path, project, turn),
    }
    service._sessions.clear()


def enable(computer):
    service.set_consent(True)
    return computer["runner"]


def use(runner, **args):
    return runner.run("computer.use", args, approved=True)


def commands(computer):
    """Вызовы без служебных session/cursor-overlay/skills."""
    done = []
    for item in computer["calls"]():
        argv = item["argv"][2:] if item["argv"][:1] == ["--session"] else item["argv"]
        if argv[0] not in {"session", "cursor-overlay"}:
            done.append(argv)
    return done


def test_default_enabled_and_actions_in_an_app_need_approval_even_when_trusted(computer):
    runner = computer["runner"]
    storage.set_setting("permission_profile", "trusted")
    assert service.consent_enabled()
    service.set_consent(False)
    assert runner.run("computer.use", {"command": "list-apps"})["blocked"]
    enable(computer)
    # обзор приложений ничего не трогает и не спрашивается; действие в приложении - вопрос человеку
    assert runner.run("computer.use", {"command": "list-apps"})["ok"]
    calls = len(computer["calls"]())
    assert runner.run("computer.use", {"command": "snapshot", "app": "Finder"})["approval_required"]
    assert len(computer["calls"]()) == calls
    assert use(runner, command="snapshot", app="Finder")["ok"]
    storage.set_setting("permission_profile", "read-only")
    assert runner.run("computer.use", {"command": "list-apps"}, approved=True)["blocked"]


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"command": "click", "ref": "@abc:e3"}, ["click", "@abc:e3"]),
        ({"command": "find", "app": "Finder", "role": "button", "query": "ok", "options": {"count": 2}},
         ["find", "--app=Finder", "--role=button", "--text=ok", "--count=2"]),
        ({"command": "snapshot", "app": "Finder", "window_id": "w-1", "options": {"max_depth": 3, "interactive_only": True}},
         ["snapshot", "--app=Finder", "--window-id=w-1", "--max-depth=3", "--interactive-only"]),
        ({"command": "mouse-click", "x": -5, "y": 10}, ["--headed", "mouse-click", "--xy=-5,10"]),
        ({"command": "mouse-wheel", "x": 1, "y": -2, "options": {"dy": -3}}, ["--headed", "mouse-wheel", "--x=1", "--y=-2", "--dy=-3"]),
        ({"command": "drag", "ref": "@a:e1", "to_ref": "@a:e2"}, ["--headed", "drag", "--from=@a:e1", "--to=@a:e2"]),
        ({"command": "press", "combo": "cmd+c", "app": "Notes", "headed": True}, ["--headed", "press", "cmd+c", "--app=Notes"]),
        ({"command": "move-window", "x": -10, "y": 5, "app": "Notes"}, ["move-window", "--app=Notes", "--x=-10", "--y=5"]),
        ({"command": "scroll", "ref": "@a:e1", "direction": "up", "amount": 5}, ["scroll", "@a:e1", "--direction=up", "--amount=5"]),
        ({"command": "launch", "app": "Notes", "options": {"arg": ["a b", "-x"], "activate": True}},
         ["launch", "Notes", "--arg=a b", "--arg=-x", "--activate"]),
        ({"command": "wait", "amount": 500}, ["wait", "500"]),
    ],
)
def test_arguments_become_whitelisted_flags(args, expected):
    assert service.build_invocation(args).argv == expected


@pytest.mark.parametrize(
    "args",
    [
        {"command": "rm"},
        {"command": "click", "ref": "bad"},
        {"command": "click", "ref": "@a:e1", "session": "x"},
        {"command": "click", "ref": "@a:e1", "options": {"trace": "/tmp/x"}},
        {"command": "click", "ref": "@a:e1", "options": {"screenshot": "a.html"}},
        {"command": "snapshot", "options": {"debug": True}},
        {"command": "snapshot", "options": {"cdp": "x"}},
        {"command": "launch", "app": "--cdp"},
        {"command": "launch", "app": "Notes", "options": {"cdp": "9222"}},
        {"command": "select", "ref": "@a:e1", "value": "-x"},
        {"command": "click"},
        {"command": "type", "ref": "@a:e1"},
        {"command": "scroll", "ref": "@a:e1", "direction": "sideways"},
        {"command": "mouse-click", "x": 1},
        {"command": "mouse-click", "x": 1, "y": True},
        {"command": "press", "combo": "cmd+c", "ref": "@a:e1"},
        {"command": "click", "ref": "@a:e1", "headed": "yes"},
        {"command": "list-apps", "options": {"x": 1}},
        {"command": "guide", "query": "other"},
        {"command": "snapshot", "app": "a\x00b"},
    ],
)
def test_extra_or_forbidden_arguments_are_rejected(args):
    with pytest.raises(ValueError):
        service.build_invocation(args)


def test_batch_has_no_nesting_text_or_session():
    item = {"command": "click", "ref": "@a:e1"}
    done = service.build_invocation({"command": "batch", "items": [item, {"command": "press", "combo": "cmd+c"}], "options": {"stop_on_error": True}})
    assert json.loads(done.argv[1]) == [
        {"command": "click", "args": {"ref_id": "@a:e1"}},
        {"command": "press", "args": {"combo": "cmd+c"}},
    ]
    assert done.argv[2:] == ["--stop-on-error"]
    for bad in (
        [{"command": "batch", "items": [item]}],
        [{"command": "type", "ref": "@a:e1", "text": "x"}],
        [{"command": "set-value", "ref": "@a:e1", "value": "x"}],
        [{"command": "clipboard-set", "text": "x"}],
        [{**item, "session": "s"}],
        [{**item, "options": {"trace": "x"}}],
        [item] * 65,
        [],
    ):
        with pytest.raises(ValueError):
            service.build_invocation({"command": "batch", "items": bad})


def test_text_goes_through_stdin_not_argv(computer):
    runner = enable(computer)
    secret = "пароль-sentinel-123"
    for args in (
        {"command": "type", "ref": "@a:e1", "text": secret},
        {"command": "set-value", "ref": "@a:e1", "value": secret},
        {"command": "clipboard-set", "text": secret},
    ):
        assert use(runner, **args)["ok"]
        call = [item for item in computer["calls"]() if args["command"] in item["argv"]][-1]
        assert call["stdin"] == secret and "--stdin" in call["argv"]
        assert secret not in " ".join(call["argv"])


def test_state_home_is_private_and_foreign_variables_are_dropped(computer):
    runner = enable(computer)
    assert use(runner, command="list-apps")["ok"]
    call = computer["calls"]()[-1]
    home = computer["path"] / "data" / "agent-desktop"
    assert call["home"] == str(home.resolve()) and call["leak"] is None
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_chat_session_with_cursor_is_started_once_and_ended_on_revoke(computer):
    runner = enable(computer)
    assert use(runner, command="list-apps")["ok"] and use(runner, command="list-windows")["ok"]
    seen = [item["argv"] for item in computer["calls"]()]
    assert seen[0][:2] == ["session", "start"] and "--cursor" in seen[0]
    overlay = seen[1]
    assert overlay[:4] == ["--session", "sess-1", "cursor-overlay", "enable"]
    assert "--label=Докки" in overlay and "--accent=#438dff" in overlay
    assert sum(1 for argv in seen if argv[:2] == ["session", "start"]) == 1
    assert seen[-1][:2] == ["--session", "sess-1"] and seen[-1][2] == "list-windows"
    service.set_consent(False)
    assert computer["calls"]()[-1]["argv"] == ["session", "end", "sess-1"]
    assert not service._sessions


def test_unrendered_cursor_is_not_an_error(computer):
    computer["configure"](rendered=False)
    assert use(enable(computer), command="list-apps")["ok"]
    assert service._sessions[computer["chat"]] == "sess-1"


def test_guide_is_cached(computer):
    runner = enable(computer)
    first = use(runner, command="guide")
    assert first["ok"] and "GUIDE desktop" in first["data"]["guide"] and "GUIDE platform" in first["data"]["guide"]
    count = len(computer["calls"]())
    assert use(runner, command="guide")["data"] == first["data"]
    assert len(computer["calls"]()) == count
    assert "commands-system" in use(runner, command="guide", query="commands-system")["data"]["guide"]


def test_target_app_from_app_snapshot_window_and_batch(computer):
    runner = enable(computer)
    chat = computer["chat"]
    assert service.target_app({"command": "launch", "app": "Notes"}, chat) == "Notes"
    assert service.target_app({"command": "click", "ref": "@snap1:e2"}, chat) is None
    assert use(runner, command="snapshot", app="Finder")["ok"]
    assert service.target_app({"command": "click", "ref": "@snap1:e2"}, chat) == "Finder"
    assert service.target_app({"command": "click", "ref": "@snap1:e2"}, chat + 1) is None
    assert service.target_app({"command": "focus-window", "window_id": "w-7"}, chat) is None
    assert use(runner, command="list-windows")["ok"]
    assert service.target_app({"command": "focus-window", "window_id": "w-7"}, chat) == "Finder"
    same = [{"command": "click", "ref": "@snap1:e1"}, {"command": "click", "ref": "@snap1:e2"}]
    assert service.target_app({"command": "batch", "items": same}, chat) == "Finder"
    mixed = [*same, {"command": "press", "combo": "cmd+c", "app": "Notes"}]
    assert service.target_app({"command": "batch", "items": mixed}, chat) is None
    assert service.target_app({"command": "batch", "items": "bad"}, chat) is None
    assert service.target_app({"command": "list-apps"}, chat) is None
    assert service.READ_COMMANDS == {"status", "list-apps", "list-windows", "list-displays", "guide"}


@pytest.mark.parametrize(
    "name",
    ["Terminal", "com.apple.Terminal", "iTerm2.app", "1Password", "System Settings", "Keychain Access", "Dokky", "Windows Terminal", "Ledger Live"],
)
def test_sensitive_apps_are_recognised_by_normalised_name(name):
    assert service.sensitive_app(name) and service.normalize_app(name) in service.SENSITIVE_APPS


def test_ordinary_apps_are_not_sensitive():
    assert not service.sensitive_app("Finder") and not service.sensitive_app(None) and not service.sensitive_app("")


@pytest.mark.parametrize(
    "code",
    [
        "STALE_REF", "AMBIGUOUS_TARGET", "PERM_DENIED", "POLICY_DENIED", "APP_NOT_FOUND", "APP_UNRESPONSIVE",
        "TIMEOUT", "ACTION_NOT_SUPPORTED", "ACTION_FAILED", "SNAPSHOT_NOT_FOUND", "ELEMENT_NOT_FOUND",
        "INVALID_ARGS", "WINDOW_NOT_FOUND", "PLATFORM_NOT_SUPPORTED", "NOTIFICATION_NOT_FOUND", "INTERNAL",
    ],
)
def test_error_code_gets_actionable_hint(computer, code):
    computer["configure"](error={"code": code, "message": "m", "suggestion": "s", "disposition": {"retry": "never"}})
    result = use(enable(computer), command="click", ref="@a:e1")
    assert result["ok"] is False and result["error_code"] == code
    assert result["hint"] and result["suggestion"] == "s" and result["retry"] == "never"


def test_windows_elevation_error_points_to_restart(computer, monkeypatch):
    runner = enable(computer)
    computer["configure"](error={"code": "ACTION_FAILED", "message": "The target process runs at a higher integrity level than this process; UIPI blocks input"})
    assert "администратора" not in str(use(runner, command="click", ref="@a:e1"))
    monkeypatch.setattr(service.sys, "platform", "win32")
    monkeypatch.setattr(service.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    result = use(runner, command="click", ref="@a:e1")
    assert result["needs_elevation"] is True and "Перезапустить от администратора" in result["hint"]


def test_windows_process_flags(computer, monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def popen(command, **kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(service.sys, "platform", "win32")
    monkeypatch.setattr(service.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(service.subprocess, "Popen", popen)
    with pytest.raises(Stop):
        service.run_process(["list-apps"])
    assert seen["creationflags"] == 0x08000000 and seen["shell"] is False


def test_screenshot_is_private_attachment_and_temp_file_is_removed(computer):
    png(computer["path"] / "shot.png")
    computer["configure"](png=str(computer["path"] / "shot.png"))
    runner = enable(computer)
    result = use(runner, command="screenshot", app="Finder", window_id="w-7")
    assert result["ok"] and result["data"]["attachment_id"] and "path" not in result["data"]
    path = next(a for a in commands(computer) if a[0] == "screenshot")[-1]
    assert not __import__("pathlib").Path(path).exists()
    client = TestClient(create_app())
    response = client.get(result["data"]["image_url"])
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    storage.set_setting("dokky_user", {"id": "user-b"})
    assert client.get(result["data"]["image_url"]).status_code == 404


def test_large_screenshot_is_downscaled_and_invalid_is_rejected(computer):
    png(computer["path"] / "big.png", (4000, 3000))
    computer["configure"](png=str(computer["path"] / "big.png"))
    runner = enable(computer)
    result = use(runner, command="screenshot")
    assert result["ok"] and result["data"]["width"] == 4000
    content = TestClient(create_app()).get(result["data"]["image_url"]).content
    with Image.open(io.BytesIO(content)) as image:
        assert max(image.size) <= 2000
    (computer["path"] / "fake.png").write_bytes(b"not an image")
    computer["configure"](png=str(computer["path"] / "fake.png"))
    assert use(runner, command="screenshot")["ok"] is False


def test_consent_api_does_not_accept_truthy_strings(computer):
    client = TestClient(create_app())
    assert client.put("/api/computer/consent", json={"enabled": "true"}).status_code == 422
    assert service.consent_enabled()
    assert client.put("/api/computer/consent", json={"enabled": True}).status_code == 200
    assert service.consent_enabled()
    assert client.put("/api/computer/consent", json={"enabled": False}).status_code == 200
    assert service._revoked.is_set()


def test_missing_active_task_cancel_and_owner_switch_deny(computer):
    runner = enable(computer)
    standalone = ToolRunner(computer["path"], computer["project"])
    assert use(standalone, command="list-apps")["ok"] is False
    storage.set_setting("dokky_user", {"id": "user-b"})
    assert use(runner, command="list-apps")["ok"] is False
    storage.set_setting("dokky_user", {"id": "user-a"})
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE id=?", (computer["turn"],))
    assert use(runner, command="list-apps")["ok"] is False
    runner.cancel_event.set()
    assert use(runner, command="list-apps")["cancelled"]
    assert not computer["calls"]()


def test_process_timeout_and_cancellation_do_not_leave_processes(computer):
    computer["configure"](sleep=30)
    with pytest.raises(ValueError, match="отведённое время"):
        service.run_process(["list-apps"], timeout=1)
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    try:
        with pytest.raises(ValueError, match="остановлено"):
            service.run_process(["list-apps"], cancel_event=stop)
    finally:
        timer.join()


def test_output_is_bounded_and_garbage_is_rejected(computer):
    computer["configure"](big=True)
    with pytest.raises(ValueError):
        service.run_process(["list-apps"])
    computer["configure"](garbage=True)
    with pytest.raises(ValueError, match="Некорректный"):
        service.run_process(["list-apps"])


def test_one_action_at_a_time(computer):
    runner = enable(computer)
    assert service._device_lock.acquire(blocking=False)
    try:
        assert use(runner, command="list-apps")["ok"] is False
    finally:
        service._device_lock.release()
    assert use(runner, command="list-apps")["ok"]


def test_revoked_consent_stops_running_call(computer):
    computer["configure"](sleep=5)
    runner = enable(computer)
    timer = threading.Timer(0.3, lambda: service.set_consent(False))
    timer.start()
    try:
        assert use(runner, command="list-apps")["ok"] is False
    finally:
        timer.join()


def test_status_reports_permissions_and_links(computer):
    status = service.status()
    assert status["native_available"] and status["accessibility"] is True and status["screen_capture"] is False
    assert status["ok"] is (sys.platform != "darwin")
    assert status["permissions"]["screen_recording"] == "denied"
    if sys.platform == "darwin":
        assert status["settings_links"]["accessibility"].startswith("x-apple.systempreferences:")


def test_status_without_binary_is_not_available(computer, monkeypatch):
    def missing():
        raise ValueError("нет")

    monkeypatch.setattr(service, "binary_path", missing)
    status = service.status()
    assert status["ok"] is False and status["native_available"] is False
