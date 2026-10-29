"""Гоняет собранный EXE с его WebEngine на отдельных тестовых данных."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_api(process, base, output):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Приложение завершилось: {process.returncode}. Логи: {output}")
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 404:
                return
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        time.sleep(0.2)
    raise RuntimeError("Приложение не запустило API")


def isolated_env(output, profile, port, debug_port):
    env = os.environ.copy()
    for key in ("KODDI_AI_BASE_URL", "KODDI_AI_MODEL", "KODDI_AI_PROVIDER", "QT_QPA_PLATFORM", "KODDI_UI"):
        env.pop(key, None)
    env.update(
        KODDI_PORT=str(port),
        KODDI_DATA_DIR=str(output / profile),
        KODDI_INTERNAL_TEST="1",
        QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{debug_port}",
    )
    return env


def check_default_ui(exe, output, workspace):
    """Основной интерфейс поставки (React): страница загрузилась, кнопки окна на месте, ошибок нет, выход штатный."""
    port, debug_port = free_port(), free_port()
    env = isolated_env(output, "profile-next", port, debug_port)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["KODDI_UI"] = "legacy"
    with (output / "next-stdout.log").open("wb") as stdout, (output / "next-stderr.log").open("wb") as stderr:
        process = subprocess.Popen(
            [str(exe)], cwd=exe.parent, env=env, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            wait_api(process, f"http://127.0.0.1:{port}", output)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{debug_port}", timeout=30000, no_defaults=True
                )
                page = browser.contexts[0].pages[0]
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                deadline = time.monotonic() + 30
                ready = "() => !document.body.classList.contains('booting') && document.documentElement.dataset.windowControls === 'ready' && document.querySelector('#root')?.childElementCount > 0"
                while not page.evaluate(ready):
                    if time.monotonic() > deadline:
                        raise RuntimeError("React-интерфейс не загрузился")
                    page.wait_for_timeout(100)
                assert page.title() == "Докки"
                assert page.locator(".desktop-window-controls button").count() == 3
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.wait_for_timeout(1500)
                assert page.evaluate("!!window.koddiDesktop")
                def request(path, method="GET", body=None):
                    return page.evaluate("""async ({path, method, body}) => {
                        const response = await fetch(path, {
                            method, headers: {'Content-Type': 'application/json'},
                            body: body == null ? undefined : JSON.stringify(body)
                        });
                        const data = await response.json();
                        return {status: response.status, data};
                    }""", {"path": path, "method": method, "body": body})

                project = request("/api/projects", "POST", {"path": str(workspace), "name": "Тестовый проект"})
                assert project["status"] == 200, project
                base = "/api/projects/" + str(project["data"]["id"])
                value = request(base + "/file?path=smoke.txt")
                assert value["status"] == 200 and value["data"]["content"] == "before\n", value
                updated = request(base + "/file", "PUT", {"path": "smoke.txt", "content": "after\n", "hash": value["data"]["hash"]})
                assert updated["status"] == 200, updated
                assert (workspace / "smoke.txt").read_text(encoding="utf-8") == "after\n"
                assert request(base + "/file?path=../outside.txt")["status"] == 422
                page.screenshot(path=str(output / "next-home.png"))
                assert not errors, errors
                page.locator('[data-window-action="close"]').click()
                process.wait(timeout=20)
                assert process.returncode == 0, "Нештатное завершение приложения"
                (output / "report.json").write_text(json.dumps({
                    "ok": True, "exe": str(exe),
                    "checks": ["react_ui_boot", "legacy_override_ignored", "native_bridge", "project_file_read_write", "path_traversal_rejected", "clean_exit"],
                    "limitations": ["Агентный сценарий и реальный сервер не проверялись"]
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                browser.close()
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description="Проверка собранного Докки")
    parser.add_argument("exe", type=Path)
    args = parser.parse_args()
    exe = args.exe.resolve()
    if not exe.is_file():
        parser.error("EXE не найден")
    output = Path(tempfile.mkdtemp(prefix="koddi-package-smoke-"))
    workspace = output / "workspace"
    workspace.mkdir()
    (workspace / "smoke.txt").write_bytes(b"before\n")
    check_default_ui(exe, output, workspace)
    print(output)


if __name__ == "__main__":
    main()
