"""Живая приёмка Computer Use на настоящем agent-desktop. Только чтение: ничего не нажимается и не вводится."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def smoke(output: Path) -> dict:
    from PIL import Image

    from koddi.computer_use import service

    checks = []
    with tempfile.TemporaryDirectory(prefix="dokky-computer-smoke-") as tmp:
        # состояние agent-desktop живёт во временной папке, данные пользователя не трогаются
        service.data_dir = lambda: Path(tmp)
        status = service.status()
        if not status["native_available"]:
            return {"status": "binary_missing", "details": status.get("error"), "checks": []}
        if not status["ok"]:
            return {"status": "permissions_required", "permissions": status["permissions"], "checks": []}
        checks.append("status")

        def call(argv, **kwargs):
            reply = service.run_process(argv, **kwargs)
            if not reply.get("ok"):
                raise RuntimeError(f"{argv[0]}: {reply.get('error')}")
            return reply["data"]

        apps = call(["list-apps"])
        assert apps, "список приложений пуст"
        checks.append("list_apps")
        windows = call(["list-windows"])
        usable = [w for w in windows if w.get("visible") and w.get("accessible", True)]
        assert usable, "нет видимых окон"
        checks.append("list_windows")
        preferred = [w for w in usable if w.get("app_name") in {"Finder", "Explorer", "File Explorer"}]
        window = (preferred or usable)[0]
        session = call(["session", "start", "--name=dokky-smoke", "--cursor"])["session_id"]
        try:
            call(["--session", session, "cursor-overlay", "enable", f"--label={service.CURSOR_LABEL}", f"--accent={service.CURSOR_ACCENT}"])
            checks.append("cursor_overlay")
            snapshot = call(["--session", session, "snapshot", f"--window-id={window['id']}", "--interactive-only"], timeout=35)
            assert snapshot["snapshot_id"] and snapshot["ref_count"] >= 0
            checks.append("snapshot")
            picture = Path(tmp) / "window.png"
            call(["--session", session, "screenshot", f"--window-id={window['id']}", str(picture)], timeout=30)
            with Image.open(picture) as image:
                assert image.format == "PNG" and image.width > 0
                output.parent.mkdir(parents=True, exist_ok=True)
                image.save(output.with_suffix(".png"))
            checks.append("window_screenshot")
        finally:
            service.run_process(["session", "end", session], timeout=10)
        checks.append("session_end")
        guide = service.ComputerSession(None, None, __import__("threading").Event())
        assert "agent-desktop" in guide._guide(None)["data"]["guide"]
        checks.append("guide")
    return {
        "status": "passed",
        "platform": sys.platform,
        "window_app": window.get("app_name"),
        "checks": checks,
        "limitations": [
            "Проверено чтение и снимки на настоящем agent-desktop; нажатия, ввод и сквозная работа модели в этот тест не входят."
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(tempfile.gettempdir()) / "dokky-computer-smoke.json")
    args = parser.parse_args()
    report = smoke(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report["status"] == "passed" else 2)
