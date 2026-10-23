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


def check_default_ui(exe, output):
    """Основной интерфейс поставки (React): страница загрузилась, кнопки окна на месте, ошибок нет, выход штатный."""
    port, debug_port = free_port(), free_port()
    env = isolated_env(output, "profile-next", port, debug_port)
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
                page.screenshot(path=str(output / "next-home.png"))
                assert not errors, errors
                page.locator('[data-window-action="close"]').click()
                process.wait(timeout=20)
                assert process.returncode == 0, "Нештатное завершение приложения"
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
    check_default_ui(exe, output)
    port, debug_port = free_port(), free_port()
    env = isolated_env(output, "profile", port, debug_port)
    # агентный сценарий написан под глобальные функции прежнего интерфейса, поэтому идёт на нём
    env["KODDI_UI"] = "legacy"
    with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(
            [str(exe)],
            cwd=exe.parent,
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            wait_api(process, base, output)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{debug_port}", timeout=30000, no_defaults=True
                )
                page = browser.contexts[0].pages[0]
                def wait(expression, timeout=30000):
                    deadline = time.monotonic() + timeout / 1000
                    while time.monotonic() < deadline:
                        if page.evaluate("() => (" + expression + ")"):
                            return
                        page.wait_for_timeout(50)
                    raise RuntimeError("Ожидание интерфейса истекло: " + expression)

                wait(
                    "typeof state !== 'undefined' && state.settings.permission_profile",
                    timeout=30000,
                )
                assert page.title() == "Докки"
                assert page.evaluate("window.koddiDesktop")
                wait(
                    "document.documentElement.dataset.windowControls === 'ready'"
                )
                assert page.locator(".desktop-window-controls button").count() == 3
                page.locator('[data-window-action="maximize"]').click()
                wait("document.querySelector('[data-window-action=maximize]').getAttribute('aria-label') === 'Восстановить'")
                page.locator('[data-window-action="maximize"]').click()
                wait("document.querySelector('[data-window-action=maximize]').getAttribute('aria-label') === 'Развернуть'")
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.screenshot(path=str(output / "home.png"))

                def request(path, method="GET", body=None):
                    return page.evaluate(
                        """async ({path,method,body}) => {
                      const response=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body==null?undefined:JSON.stringify(body)});
                      if(!response.ok)throw new Error('Проверка API: '+response.status+' '+path);
                      return response.json();
                    }""",
                        {"path": path, "method": method, "body": body},
                    )

                request("/api/ai/test-enable", "POST")
                project = request(
                    "/api/projects", "POST", {"path": str(workspace), "name": "Тестовый проект"}
                )
                page.evaluate("async project => {await refresh();newChat(project);}", project)
                page.locator("#prompt").fill("Создай скрипт приветствия и проверь его")
                page.locator("#send").click()
                page.get_by_role('button',name='Разрешить',exact=True).wait_for()
                page.get_by_role('button',name='Разрешить',exact=True).click()
                wait(
                    "state.chat && !state.running && document.querySelector('#feed').textContent.includes('Файл greeting.py')",
                    timeout=45000,
                )
                assert (workspace / "greeting.py").is_file()
                page.locator("#prompt").fill("Добавь обработку пустого ввода")
                page.locator("#send").click()
                page.get_by_role('button',name='Разрешить',exact=True).wait_for()
                page.get_by_role('button',name='Разрешить',exact=True).click()
                wait(
                    "!state.running && document.querySelectorAll('.message.user').length >= 2",
                    timeout=45000,
                )
                content = (workspace / "greeting.py").read_text(encoding="utf-8")
                assert "Введите приветствие." in content
                namespace = {}
                exec(compile(content, str(workspace / "greeting.py"), "exec"), namespace)
                assert namespace["greet"]("привет") == "Привет!"
                assert namespace["greet"]("") == "Введите приветствие."
                assert page.evaluate("typeof window.koddiClipboardWrite === 'function'")
                page.locator(".message.user").last.get_by_role("button", name="Скопировать сообщение", exact=True).click()
                wait("document.querySelector('#toast')?.textContent === 'Сообщение скопировано'")
                page.evaluate("""() => {
                    const node=document.createElement('section');node.id='package-clipboard-test';
                    document.querySelector('#feed').append(node);
                    renderMarkdown(node,'```python\\nprint(1)\\n```');
                }""")
                page.locator("#package-clipboard-test").get_by_role("button", name="Скопировать код", exact=True).click()
                wait("document.querySelector('#toast')?.textContent === 'Код скопирован'")
                page.evaluate("document.querySelector('#package-clipboard-test').remove()")
                completed = page.locator(".journal-completed").last
                assert not completed.evaluate("node => node.open")
                completed.locator(":scope > summary").click()
                assert completed.locator(".journal-segment").count() > 0
                for summary in completed.locator(".journal-segment > summary").all():
                    summary.click()
                assert completed.locator(".journal-commentary").first.is_visible()
                assert completed.locator(".activity-line").count() > 0
                assert any(value.strip() for value in completed.locator(".journal-duration").all_text_contents())
                page.screenshot(path=str(output / "journal-open.png"), animations="disabled")
                completed.locator(":scope > summary").click()
                wait("!document.querySelector('#feed').getAnimations({subtree:true}).some(a => a.playState === 'running' && a.effect.getTiming().iterations !== Infinity)")
                page.screenshot(path=str(output / "chat.png"))
                chat_id = page.evaluate("state.chat.id")
                turns = request(f"/api/chats/{chat_id}/turn")
                assert turns["turn"]["status"] == "completed"
                messages = request(f"/api/chats/{chat_id}/messages")
                assert any(m.get("metadata", {}).get("events") for m in messages)
                request("/api/settings", "PUT", {"project_id":project['id'],"permission_profile": "trusted",'private_network':True})
                opened = request(
                    "/api/tools/run",
                    "POST",
                    {
                        "project_id": project["id"],
                        "name": "browser.open",
                        "arguments": {"url": base + "/ui/"},
                    },
                )
                assert opened["ok"], opened
                dom = request(
                    "/api/tools/run",
                    "POST",
                    {
                        "project_id": project["id"],
                        "name": "browser.dom",
                        "arguments": {"session_id": opened["session_id"]},
                    },
                )
                assert dom["ok"] and "Недоступно" in dom["html"], dom
                assert httpx.get(base + "/ui/").status_code == 404
                report = {
                    "ok": True,
                    "exe": str(exe),
                    "workspace": str(workspace),
                    "chat_id": chat_id,
                    "checks": [
                        "next_ui_boot",
                        "native_bridge",
                        "web_ui",
                        "test_http_api",
                        "file_create",
                        "file_edit",
                        "verification",
                        "greeting_behavior",
                        "clipboard_user",
                        "clipboard_code",
                        "journal_segments",
                        "completed_fold",
                    ],
                    "test_provider_only": True,
                }
                (output / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                page.locator('[data-window-action="close"]').click()
                process.wait(timeout=20)
                assert process.returncode == 0, 'Нештатное завершение приложения'
                browser.close()
            print(output)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
