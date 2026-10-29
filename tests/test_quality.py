"""Качество результата: проверки проекта и цикл исправления, контракт, ревьюер, чекпоинт хода, ui.verify."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import koddi.storage.db as storage
from koddi.projectmap import service
from koddi.quality import checks, gate
from koddi.tools.runner import ToolRunner

OWNER = "__test__"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def new_turn(chat_id: int) -> int:
    with storage.db() as conn:
        return int(conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat_id,)).lastrowid)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from koddi.projects import add_project

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "quality.sqlite3")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "full")
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: OWNER)
    # без виртуального окружения в папке теста инструменты берутся из окружения, где идут тесты
    monkeypatch.setattr(checks, "_python_tool", lambda root, package, module: [sys.executable, "-m", module]
                        if module in {"pytest", "ruff"} else None)
    storage.init_db()
    root = tmp_path / "calc"
    write(root, "pyproject.toml", "[project]\nname = 'calc'\n\n[tool.pytest.ini_options]\ntestpaths = ['tests']\n"
                                  "pythonpath = ['.']\n\n[tool.ruff]\nline-length = 120\n")
    write(root, "calc.py", "def add(a, b):\n    return a + b\n")
    write(root, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    project = add_project(str(root))
    with storage.db() as conn:
        chat = int(conn.execute("INSERT INTO chats(title,project_id,mode,owner_id) VALUES ('t',?,'development',?)",
                                (project["id"], OWNER)).lastrowid)
    turn = new_turn(chat)
    yield SimpleNamespace(root=root.resolve(), project_id=project["id"], chat_id=chat, turn_id=turn,
                          runner=ToolRunner(root.resolve(), project["id"], turn))
    service.shutdown()


def edit(runner: ToolRunner, path: str, old: str, new: str) -> dict:
    result = runner.run("file.edit", {"path": path, "old_text": old, "new_text": new})
    assert result.get("ok"), result
    return result


def evaluate(env, **extra) -> gate.GateDecision:
    return gate.evaluate(turn_id=env.turn_id, workspace=env.root, project_id=env.project_id, mode="development",
                         tool_names={"qa.run"}, evidence=extra.get("evidence"), review_enabled=extra.get("review", False))


# ---------- обнаружение и разбор

def test_detects_project_checks_and_user_command_wins(env, tmp_path, monkeypatch):
    root = env.root
    write(root, "web/package.json", json.dumps({"scripts": {"typecheck": "tsc --noEmit", "test": "vitest run", "build": "vite build"}}))
    write(root, "web/package-lock.json", "{}")
    write(root, "web/tsconfig.json", "{}")
    write(root, "web/src/App.tsx", "export const App = () => null;\n")
    write(root, "web/node_modules/.bin/vitest" + (".cmd" if os.name == "nt" else ""), "")
    found, notes = checks.detect(root, ["calc.py", "web/src/App.tsx"], project_id=env.project_id)
    ids = [check.id for check in found]
    assert ids[:2] == ["python:lint:ruff@", "node:typecheck:typecheck@web"]
    pytest_check = next(check for check in found if check.tool == "pytest")
    assert pytest_check.argv[-1] == "tests/test_calc.py"  # связанный тест найден по импорту
    vitest = next(check for check in found if check.tool == "vitest")
    assert vitest.cwd == "web" and vitest.argv[1:] == ["related", "--run", "src/App.tsx"]
    assert all(check.kind != "build" for check in found)  # сборка - только scope=full
    full, _ = checks.detect(root, [], scope="full", project_id=env.project_id)
    assert {"node:build:build@web", "node:test:test@web", "python:test:pytest@", "python:lint:ruff@"} <= {check.id for check in full}
    checks.set_user_checks(env.project_id, [{"kind": "test", "command": "make test", "cwd": ""}])
    custom, _ = checks.detect(root, ["calc.py"], project_id=env.project_id)
    assert [check.id for check in custom if check.kind == "test"] == ["user:test:0"]
    with pytest.raises(ValueError):
        checks.set_user_checks(env.project_id, [{"kind": "test", "command": "x", "cwd": "../other"}])


def test_missing_tool_timeout_and_no_tests_are_never_success(env):
    check = checks.Check("python:test:pytest@", "test", "pytest", "", ["pytest"])
    assert checks.classify(check, {"returncode": 127, "stderr": "zsh: command not found: pytest"}, env.root)["status"] == "environment"
    assert checks.classify(check, {"returncode": 5, "stdout": "no tests ran"}, env.root)["status"] == "no_tests"
    assert checks.classify(check, {"timed_out": True}, env.root)["status"] == "timeout"
    assert checks.overall([{"status": "environment"}]) == "unverified"
    assert checks.overall([{"status": "passed"}, {"status": "timeout"}]) == "partial"
    assert checks.overall([]) == "no_checks"
    failed = checks.classify(checks.Check("ts", "typecheck", "tsc", "", ["tsc"]),
                             {"returncode": 2, "stdout": "src/a.ts(3,7): error TS2322: Type 'string' is not assignable"}, env.root)
    assert failed["diagnostics"][0] == {"path": "src/a.ts", "line": 3, "message": "error TS2322: Type 'string' is not assignable"}


@pytest.mark.skipif(os.name != "nt", reason="проверка кода выхода PowerShell")
def test_windows_check_preserves_exit_codes(env):
    executable = sys.executable.replace("'", "''")
    items = [
        checks.Check("passed", "test", "pytest", "", [sys.executable, "-c", "import sys; sys.exit(0)"]),
        checks.Check("failed", "test", "pytest", "", [sys.executable, "-c", "import sys; sys.exit(2)"]),
        checks.Check("no_tests", "test", "pytest", "", [sys.executable, "-c", "import sys; sys.exit(5)"]),
        checks.Check("missing", "test", "pytest", "", ["dokky-test-missing-command"]),
        checks.Check("shell_comment", "test", "custom", "", [
            "__shell__", f"& '{executable}' -c 'import sys; sys.exit(3)' # комментарий",
        ]),
    ]
    results = checks.run(env.runner, items)
    assert [(item["status"], item["exit_code"]) for item in results[:3]] == [
        ("passed", 0), ("failed", 2), ("no_tests", 5),
    ]
    assert results[3]["status"] == "environment" and results[3]["exit_code"] != 0
    assert results[4]["status"] == "failed" and results[4]["exit_code"] == 3


def test_python_tool_prefers_project_virtualenv(tmp_path):
    from koddi.quality.checks import _venv, _venv_has

    venv = tmp_path / ".venv"
    write(venv, "pyvenv.cfg", "home = /usr/bin\n")
    site = venv / ("Lib/site-packages" if os.name == "nt" else "lib/python3.11/site-packages")
    (site / "pytest").mkdir(parents=True)
    assert _venv(tmp_path, tmp_path) == venv and _venv_has(venv, "pytest") and not _venv_has(venv, "ruff")


# ---------- цикл исправления и гейт

def test_broken_code_is_caught_repaired_and_verified(env):
    edit(env.runner, "calc.py", "return a + b", "return a - b")
    blocked = evaluate(env)
    assert not blocked.allow and "qa.run" in blocked.error
    failed = env.runner.run("qa.run", {})
    assert failed["status"] == "failed" and failed["state"] == "repairing" and not failed["ok"]
    test_result = next(item for item in failed["results"] if item["tool"] == "pytest")
    assert any(item["path"] == "tests/test_calc.py" for item in test_result["diagnostics"])
    assert "guidance" in failed and failed["attempt"] == 1
    assert not evaluate(env).allow  # проверки упали - завершать рано
    edit(env.runner, "calc.py", "return a - b", "return a + b")
    partial = env.runner.run("qa.run", {"checks": [test_result["id"]]})
    # прошла только перезапущенная проверка: остальные шли на старом коде - это ещё не «проверено»
    assert partial["status"] == "passed" and partial["state"] == "verification_pending" and partial["stale"]
    assert not evaluate(env).allow
    passed = env.runner.run("qa.run", {})
    assert passed["status"] == "passed" and passed["state"] == "verified" and not passed.get("stale")
    done = evaluate(env)
    assert done.allow and done.state == "verified" and "проверено" in done.report and "pytest" in done.report


def test_persistent_failure_ends_failed_not_green(env):
    edit(env.runner, "calc.py", "return a + b", "return a * b")
    first = env.runner.run("qa.run", {})
    second = env.runner.run("qa.run", {})
    assert first["state"] == "repairing"
    assert second["state"] == "failed" and any(item.get("repeated") for item in second["results"])
    decision = evaluate(env)
    assert decision.allow and decision.state == "failed" and "проверки не прошли" in decision.report


def test_gate_gives_up_honestly_after_limit_and_skips_unchanged_turns(env):
    assert evaluate(env).allow  # нет правок - гейт не мешает
    edit(env.runner, "calc.py", "return a + b", "return b + a")
    for _ in range(gate.MAX_GATE_BLOCKS):
        assert not evaluate(env).allow
    final = evaluate(env)
    assert final.allow and final.state == "verification_pending" and "не запускались" in final.report
    old_session = gate.evaluate(turn_id=env.turn_id, workspace=env.root, project_id=env.project_id, mode="development",
                                tool_names={"file.edit"}, evidence=None, review_enabled=False)
    assert old_session.allow and old_session.report == ""  # без qa.run в наборе (старая сессия) всё как раньше


def test_contract_evidence_and_restore_in_next_turn(env):
    contract = gate.save_contract(env.turn_id, {"goal": "Fix add", "acceptance": ["add(2, 3) == 5", "docs updated"],
                                                "test_plan": ["tests/test_calc.py"], "unknown": "x"})
    assert contract == {"goal": "Fix add", "acceptance": ["add(2, 3) == 5", "docs updated"], "test_plan": ["tests/test_calc.py"]}
    edit(env.runner, "calc.py", "return a + b", "return (a + b)")
    qa = env.runner.run("qa.run", {})
    pytest_id = next(item["id"] for item in qa["results"] if item["tool"] == "pytest")
    missing = evaluate(env)
    assert not missing.allow and missing.details["acceptance"] == contract["acceptance"]
    done = evaluate(env, evidence=[
        {"criterion": "add(2, 3) == 5", "status": "met", "check": pytest_id},
        {"criterion": "docs updated", "status": "met", "proof": "I updated them"},
    ])
    assert done.allow and done.state == "unverified"  # второй критерий - только со слов агента
    assert f"✓ add(2, 3) == 5 ({pytest_id})" in done.report and "со слов агента" in done.report
    following = new_turn(env.chat_id)
    assert gate.inherit_contract(env.chat_id, following) == contract
    assert gate.load(following)["contract"] == contract


@pytest.mark.skipif(not os.environ.get("KODDI_TEST_TYPESCRIPT") or not shutil.which("node"), reason="нужен TypeScript 5 и node")
def test_broken_typescript_is_detected_with_location(env):
    root = env.root
    write(root, "web/package.json", json.dumps({"name": "web"}))
    write(root, "web/tsconfig.json", json.dumps({"compilerOptions": {"strict": True, "noEmit": True}, "include": ["src"]}))
    write(root, "web/src/price.ts", "export function price(value: number): number {\n  return value * 2;\n}\n")
    modules = root / "web/node_modules"
    modules.mkdir(parents=True)
    (modules / "typescript").symlink_to(Path(os.environ["KODDI_TEST_TYPESCRIPT"]))
    (modules / ".bin").mkdir()
    (modules / ".bin/tsc").symlink_to(modules / "typescript/bin/tsc")
    edit(env.runner, "web/src/price.ts", "return value * 2;", "return String(value * 2);")
    result = env.runner.run("qa.run", {})
    tsc = next(item for item in result["results"] if item["tool"] == "tsc")
    assert tsc["status"] == "failed" and tsc["diagnostics"][0]["path"] == "web/src/price.ts" and tsc["diagnostics"][0]["line"] == 2


# ---------- ревьюер

class FakeClient:
    def __init__(self, reply: dict):
        self.reply = reply
        self.calls = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield {"content": json.dumps(self.reply)}


async def passthrough(iterator):
    async for item in iterator:
        yield item


def test_reviewer_finds_missing_null_check_and_drops_unfounded(env):
    from koddi.quality.reviewer import review_changes

    write(env.root, "users.py", "def name(user):\n    return user.name\n")
    edit(env.runner, "users.py", "    return user.name\n", "    profile = user.profile\n    return profile.display_name.strip()\n")
    changes = gate.turn_changes(env.turn_id)
    client = FakeClient({"summary": "x", "findings": [
        {"severity": "high", "path": "users.py", "line": 3, "finding": "profile может быть None", "reason": "AttributeError",
         "reproducer_or_test": "name(User(profile=None))", "suggestion": "проверить None", "confidence": "high"},
        {"severity": "blocker", "path": "other.py", "line": 1, "finding": "выдумка", "reason": "", "reproducer_or_test": "",
         "suggestion": "", "confidence": "high"},
        {"severity": "high", "path": "users.py", "line": 99, "finding": "нет такой строки", "reason": "", "reproducer_or_test": "",
         "suggestion": "", "confidence": "high"},
    ]})
    verdict = asyncio.run(review_changes(client, model="m", responsive=passthrough, task="show display name", root=env.root,
                                         changes=changes, contract={}, qa={}))
    assert verdict["available"] and verdict["dropped"] == 2
    assert verdict["findings"][0]["path"] == "users.py" and verdict["findings"][0]["line"] == 3 and verdict["findings"][0]["blocking"]
    payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert "user.profile" in payload["diff"] and "show display name" == payload["user_task"]
    empty = FakeClient({"summary": "", "findings": []})
    assert asyncio.run(review_changes(empty, model="m", responsive=passthrough, task="", root=env.root, changes={}, contract={},
                                      qa={}))["findings"] == [] and not empty.calls  # без diff модель не вызывается


def test_blocking_review_rejects_then_rounds_are_bounded(env):
    edit(env.runner, "calc.py", "return a + b", "return a + b  # sum")
    env.runner.run("qa.run", {})
    decision = evaluate(env, review=True)
    assert decision.allow and decision.review_request is not None
    finding = {"severity": "high", "path": "calc.py", "line": 2, "finding": "x", "blocking": True}
    refused = gate.apply_review(env.turn_id, decision.review_request["fingerprint"], {"available": True, "findings": [finding]})
    assert refused is not None and not refused.allow and refused.details["findings"][0]["path"] == "calc.py"
    assert not evaluate(env, review=True).allow  # не исправлено - снова отказ
    for _ in range(gate.MAX_GATE_BLOCKS):
        evaluate(env, review=True)
    final = evaluate(env, review=True)
    assert final.allow and "Независимое ревью" in final.report and "блокирующих: 1" in final.report


def test_review_policy_skips_docs_and_small_changes(env, monkeypatch):
    from koddi.quality import reviewer

    assert not reviewer.needed({"README.md": "a"}, env.root)
    assert not reviewer.needed({"calc.py": "def add(a, b):\n    return a + b\n"}, env.root)
    assert reviewer.needed({"app/auth.py": ""}, env.root)
    monkeypatch.setattr(reviewer, "get_setting", lambda key, default=None: "off")
    assert not reviewer.needed({"app/auth.py": ""}, env.root)


# ---------- чекпоинт хода

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_checkpoint_restores_only_agent_changes_and_keeps_user_work(env):
    from koddi.changes.checkpoints import preview, restore
    from koddi.changes.service import rollback_change_set

    root = env.root
    write(root, "notes.py", "VALUE = 1\n")
    git(root, "init", "-q")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    write(root, "notes.py", "VALUE = 2  # правка пользователя до хода\n")
    write(root, "scratch.txt", "черновик пользователя\n")
    edit(env.runner, "notes.py", "VALUE = 2", "VALUE = 3")
    edit(env.runner, "calc.py", "return a + b", "return a - b")
    env.runner.run("file.create", {"path": "extra.py", "content": "X = 1\n"})
    snapshot = gate.load(env.turn_id)["checkpoint"]
    assert snapshot["git"] and "notes.py" in snapshot["dirty"] and "scratch.txt" in snapshot["untracked"]
    write(root, "calc.py", "def add(a, b):\n    return a - b  # пользователь поправил после агента\n")
    view = preview(env.turn_id, OWNER)
    assert view["conflicts"] == ["calc.py"] and {item["action"] for item in view["files"]} == {"restore", "delete"}
    refused = restore(env.turn_id, OWNER)
    assert not refused["restored"] and refused["conflicts"] == ["calc.py"]
    assert (root / "notes.py").read_text() == "VALUE = 3  # правка пользователя до хода\n"  # ничего не тронуто
    done = restore(env.turn_id, OWNER, ["notes.py", "extra.py"])
    assert done["restored"] and set(done["paths"]) == {"notes.py", "extra.py"}
    assert (root / "notes.py").read_text() == "VALUE = 2  # правка пользователя до хода\n"
    assert not (root / "extra.py").exists() and (root / "scratch.txt").read_text() == "черновик пользователя\n"
    assert "пользователь поправил" in (root / "calc.py").read_text()
    undone = rollback_change_set(done["change_set_id"])
    assert undone["rolled_back"] and (root / "notes.py").read_text().startswith("VALUE = 3") and (root / "extra.py").exists()
    with pytest.raises(LookupError):
        preview(env.turn_id, "someone-else")


# ---------- политика

def test_policy_for_quality_tools(env, monkeypatch):
    from koddi.policy.engine import decide

    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "ask")
    assert decide("qa.run", {}, env.root, env.project_id).action == "ask"
    assert decide("qa.run", {"paths": ["../outside.py"]}, env.root, env.project_id).action == "block"
    assert decide("ui.verify", {"url": "http://127.0.0.1:5173/"}, env.root, env.project_id).action == "allow"
    assert decide("ui.verify", {"url": "https://example.com/"}, env.root, env.project_id).action == "block"
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "read-only")
    assert decide("qa.run", {}, env.root, env.project_id).action == "block"
    assert decide("ui.verify", {"url": "http://127.0.0.1:5173/"}, env.root, env.project_id).action == "block"


# ---------- ui.verify на настоящем Chromium

PAGE = """<!doctype html><html><head><title>Счётчик</title><style>body{margin:0;font:16px sans-serif}</style></head><body>
<h1>Счётчик</h1><button id="inc" onclick="document.getElementById('n').textContent='1'; %s">Плюс</button>
<p id="n">0</p>%s</body></html>"""


@pytest.fixture
def site(env, monkeypatch):
    import http.server
    import threading

    from koddi.browser import service as browser

    try:
        browser._BROWSER_THREAD.submit(browser._ensure_browser).result()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium недоступен: {exc}")
    state = {"script": "", "extra": ""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (PAGE % (state["script"], state["extra"])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr("koddi.tools.runner.local_preview_allowed", lambda project_id, workspace: True)
    yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_address[1]}/", state=state)
    httpd.shutdown()
    browser.browser_close_all()


def test_ui_verify_clicks_asserts_and_catches_console_error(env, site):
    args = {"url": site.url, "name": "counter", "widths": [800, 375], "steps": [{"action": "click", "selector": "#inc"}],
            "expect": [{"selector": "#n", "text": "1"}, {"text": "Ошибка", "absent": True}]}
    ok = env.runner.run("ui.verify", args)
    assert ok["status"] == "passed", ok["failures"]
    assert ok["check_id"] == "ui:counter" and all(item.get("attachment_id") for item in ok["widths"])
    assert ok["vision"].startswith("not_available")
    assert gate.load(env.turn_id)["qa"]["ui_passed"] == ["ui:counter"]
    site.state["script"] = "console.error('сломалось')"
    broken = env.runner.run("ui.verify", args)
    assert broken["status"] == "failed" and any("сломалось" in item for item in broken["failures"])
    site.state.update(script="", extra='<div style="width:900px;height:10px"></div>')
    wide = env.runner.run("ui.verify", {**args, "widths": [375]})
    assert wide["status"] == "failed" and any("горизонтальная прокрутка" in item for item in wide["failures"])
    assert env.runner.run("ui.verify", {"url": "https://example.com/"})["ok"] is False


def test_ui_verify_baseline_mismatch_is_detected(env, site):
    args = {"url": site.url, "name": "base", "widths": [600], "baseline": "compare"}
    first = env.runner.run("ui.verify", args)
    assert first["status"] == "passed" and first["widths"][0]["baseline"]["status"] == "created"
    assert env.runner.run("ui.verify", args)["widths"][0]["baseline"]["status"] == "match"
    site.state["extra"] = '<div style="height:200px;background:#e00"></div>'
    changed = env.runner.run("ui.verify", args)
    assert changed["status"] == "failed" and changed["widths"][0]["baseline"]["status"] == "mismatch"
    assert changed["widths"][0]["baseline"]["diff"]["attachment_id"]
