"""Нативная приёмка на собственной тестовой программе. Реальные проекты и аккаунт не используются."""

from __future__ import annotations

import argparse
import base64
import json
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def smoke(output: Path) -> dict:
    from koddi.computer_use.service import native_command

    command = native_command()
    checks = []

    def call(request):
        value = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
        )
        if not value.stdout.strip():
            raise RuntimeError(f"helper_no_output rc={value.returncode} {value.stderr[-300:]}")
        result = json.loads(value.stdout)
        if not result.get("ok"):
            raise RuntimeError(result.get("error_code", "native_failure"))
        return result

    permissions = call({"action": "status"})
    if permissions.get("session_locked"):
        return {
            "status": "interactive_session_locked",
            "permissions": permissions,
            "checks": [],
            "limitations": ["Рабочий стол заблокирован; тест ввода и снимков не выполняется."],
        }
    if not permissions.get("accessibility") or not permissions.get("screen_capture"):
        return {"status": "permissions_required", "permissions": permissions, "checks": []}
    with tempfile.TemporaryDirectory(prefix="dokky-computer-smoke-") as tmp:
        folder = Path(tmp)
        if sys.platform == "darwin":
            application = folder / "DokkyFixture.app"
            executable = application / "Contents/MacOS/fixture"
            executable.parent.mkdir(parents=True)
            (application / "Contents/Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleExecutable": "fixture",
                        "CFBundleIdentifier": "com.goshkow.dokky.computer-fixture",
                        "CFBundleName": "DokkyFixture",
                        "CFBundlePackageType": "APPL",
                        "CFBundleVersion": "1",
                    }
                )
            )
            subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-framework",
                    "AppKit",
                    str(ROOT / "tests/fixtures/computer_fixture.swift"),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
        elif sys.platform == "win32":
            application = folder / "DokkyFixture.exe"
            compiler = (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
            )
            subprocess.run(
                [
                    str(compiler),
                    "/nologo",
                    "/target:winexe",
                    "/r:System.Windows.Forms.dll",
                    "/r:System.Drawing.dll",
                    "/out:" + str(application),
                    str(ROOT / "tests/fixtures/computer_fixture.cs"),
                ],
                check=True,
            )
            executable = application
        else:
            raise RuntimeError("Поддерживаются macOS и Windows")
        launched = call({"action": "launch", "application": str(application)})
        checks.append("launch")
        pid = launched["pid"]
        try:
            time.sleep(1)

            def observe():
                return call({"action": "observe", "pid": pid})

            snapshot = observe()
            checks.append("accessibility_tree")
            identity = snapshot["identity"]

            def has_text(text):
                # интерфейс обновляется не мгновенно: ждём до трёх секунд
                for _ in range(15):
                    if any(text in (node["title"], node.get("value")) for node in observe()["elements"]):
                        return True
                    time.sleep(0.2)
                return False

            def action(name, **args):
                return call({"action": name, "pid": pid, "identity": identity, **args})

            action("focus")
            checks.append("focus")
            time.sleep(0.2)
            button = next(node for node in observe()["elements"] if node["title"] == "Count 0")
            expected = {key: value for key, value in button.items() if key not in {"path", "frame"}}
            action(
                "press",
                element_path=button["path"],
                expected=expected,
                expected_frame=button["frame"],
            )
            time.sleep(0.2)
            snapshot = observe()
            button = next(node for node in snapshot["elements"] if node["title"] == "Count 1")
            checks.append("structured_press")
            x, y, width, height = button["frame"]
            action("move", x=int(x + width / 2), y=int(y + height / 2))
            action("click", x=int(x + width / 2), y=int(y + height / 2))
            time.sleep(0.2)
            assert any(node["title"] == "Count 2" for node in observe()["elements"])
            checks.extend(["move", "coordinate_click"])
            textfield = next(
                node
                for node in observe()["elements"]
                if node["role"] in {"AXTextField", "ControlType.Edit"}
            )
            x, y, width, height = textfield["frame"]
            action("click", x=int(x + width / 2), y=int(y + height / 2))
            # поле получает фокус не сразу: помощник отказывает без звуков, пока ввод некуда отправить
            attempts = 0
            while True:
                attempts += 1
                try:
                    action("type", text="Привет 🌍")
                    break
                except RuntimeError as error:
                    if str(error) != "no_text_focus" or attempts >= 10:
                        raise
                    time.sleep(0.4)
            assert has_text("Typed: Привет 🌍")
            keyboard_attempts = attempts
            checks.append("unicode_type")
            action("hotkey", keys=["cmd" if sys.platform == "darwin" else "ctrl", "a"])
            action("type", text="replaced")
            assert has_text("Typed: replaced")
            checks.append("hotkey")
            action("scroll", x=int(x + 10), y=int(y + height + 100), delta_y=-80)
            checks.append("scroll_event")
            if sys.platform == "darwin":
                # список окон не снимает экран; затем выбирается только окно тестового процесса
                request = subprocess.run(
                    command,
                    input=json.dumps(
                        {"action": "screenshot", "pid": pid, "identity": identity, "window_id": 0}
                    ),
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                windows = json.loads(request.stdout)["windows"]
                window_id = next(
                    window["window_id"]
                    for window in windows
                    if window["title"] == "Dokky Computer Use Fixture"
                )
            else:
                window_id = observe()["window_id"]
            screenshot = action("screenshot", window_id=window_id)
            raw = base64.b64decode(screenshot["png_base64"], validate=True)
            assert raw.startswith(b"\x89PNG\r\n\x1a\n")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.with_suffix(".png").write_bytes(raw)
            checks.append("selected_window_screenshot")
            invalid = subprocess.run(
                command,
                input=json.dumps({"action": "focus", "pid": pid, "identity": "stale"}),
                text=True,
                capture_output=True,
                timeout=15,
            )
            assert json.loads(invalid.stdout)["ok"] is False
            checks.append("stale_identity_denied")
        finally:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    return {
        "status": "passed",
        "platform": sys.platform,
        "permissions": permissions,
        "checks": checks,
        "keyboard_attempts": keyboard_attempts,
        "limitations": [
            "Это тест native helper на собственной программе; сквозная работа модели, инсталлятора и подписанного приложения проверяется отдельно."
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path(tempfile.gettempdir()) / "dokky-computer-smoke.json"
    )
    args = parser.parse_args()
    report = smoke(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report["status"] == "passed" else 2)
