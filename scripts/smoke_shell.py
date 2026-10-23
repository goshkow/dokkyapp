"""Оболочка приложения: кнопки поверх страницы, карточки справа, история переходов, аккаунт и рост окна."""

import base64
import json
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mNkYPhfz0AEYBxVSF+FAP5FDvcfRYWgAAAAAElFTkSuQmCC"
)


def main():
    web = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"
    output = Path(tempfile.mkdtemp(prefix="koddi-shell-"))

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    chats = [
        {"id": 1, "title": "Первый чат", "mode": "development", "model": "dokky-code", "project_id": None,
         "updated_at": "2026-10-16T09:00:00+00:00"},
        {"id": 2, "title": "Второй чат", "mode": "development", "model": "dokky-code", "project_id": None,
         "updated_at": "2026-10-15T09:00:00+00:00"},
    ]
    errors, unexpected = [], []

    def route_api(route):
        request = route.request
        path = urlsplit(request.url).path
        if path in {"/api/dokky/events", "/api/events"}:
            return route.fulfill(content_type="text/event-stream", body="")
        if path == "/api/dokky/cloud/me/avatar":
            return route.fulfill(content_type="image/png", body=PNG)
        value = None
        if path == "/api/settings":
            value = {"theme": "dark", "ui_motion": "full", "permission_profile": "ask"}
        elif path == "/api/dokky/session":
            value = {"internal_test": True, "authenticated": True, "user": {"display_name": "Иван Гошков"},
                     "bootstrap": {"models": []}}
        elif path == "/api/dokky/bootstrap":
            value = {}
        elif path == "/api/projects":
            value = [{"id": 1, "name": "goshcode", "path": "C:/Users/User/Documents/goshcode"}]
        elif path == "/api/chats":
            value = chats
        elif path == "/api/models/options":
            value = [{"tag": "dokky-code", "name": "Докки", "source": "test", "context_tokens": 200000}]
        elif path == "/api/modes":
            value = [{"id": key, "name": key} for key in ["development", "chat", "plan"]]
        elif path == "/api/app/diagnostics":
            value = {"build_number": "test"}
        elif path == "/api/dokky/cloud/subscription":
            value = {"plan": "plus", "remaining_percent": 80, "developer": {}}
        elif path == "/api/dokky/cloud/preferences":
            value = {}
        elif path.endswith(("/messages", "/changes", "/requests")):
            value = []
        elif path.endswith("/queue"):
            value = {"items": [], "paused": False}
        elif path.endswith("/turn"):
            value = {"active": False}
        elif path.endswith("/plan"):
            value = {"steps": [], "document": "", "turn_id": None}
        elif path.endswith("/files"):
            value = {"files": [{"path": "README.md"}]}
        elif "coding-session" in path:
            value = {"id": "s", "reasoning_mode": "light", "version": 1, "history": {"length": 0}, "last_usage": None}
        else:
            unexpected.append(f"{request.method} {path}")
            return route.fulfill(status=404, json={"detail": "Неожиданный запрос"})
        route.fulfill(content_type="application/json", body=json.dumps(value))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", route_api)
        page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
        page.wait_for_function("window.koddiProductScope === 'development'")
        page.wait_for_timeout(1500)

        # кнопки лежат поверх страницы и не занимают отдельную полосу
        assert page.locator("#app-topbar").count() == 0
        controls = page.locator(".shell-controls-left").bounding_box()
        assert controls["y"] < 10 and controls["x"] > 100, controls  # правее названия при открытом меню
        assert page.locator("#left").evaluate("node => node.getBoundingClientRect().top") == 0
        assert page.locator("main").evaluate("node => node.getBoundingClientRect().top") == 0
        assert page.locator("#toggle-left").evaluate("node => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"

        # сайдбар в цвет чата, без отдельной полосы
        assert page.locator("#left").evaluate("node => getComputedStyle(node).backgroundColor") == page.locator("main").evaluate(
            "node => getComputedStyle(node).backgroundColor"
        )

        # сайдбар: «Новый чат» первым, без синей кнопки и подвала со старыми действиями
        assert page.locator(".sidebar-bottom-actions").count() == 0
        assert page.locator(".sidebar-nav > .nav").first.inner_text().strip() == "Новый чат"
        assert page.locator("#left .new-chat").evaluate("node => getComputedStyle(node).backgroundColor") != "rgb(67, 141, 255)"
        assert page.locator("#sidebar-settings").evaluate("node => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"
        assert page.locator(".chat-row .chat-time").count() == 2

        # аватарка из Докки и предзагруженные настройки
        page.wait_for_function("document.querySelector('#connection-badge .connection-dot img')?.src.startsWith('data:image/png')")
        assert page.evaluate("Boolean(productUI.settings) && !productUI.settings.open")
        page.locator("#connection-badge").click()
        page.locator(".settings-window[open]").wait_for()
        assert page.locator("#settings-heading").inner_text() == "Аккаунт"
        page.keyboard.press("Escape")
        page.wait_for_function("!productUI.settings.open")
        page.locator("#sidebar-settings").click()
        page.locator(".settings-window[open]").wait_for()
        assert page.locator("#settings-heading").inner_text() == "Общие"
        page.get_by_role("button", name="Использование").click()
        page.get_by_text("Остаток лимита").wait_for()
        page.keyboard.press("Escape")
        page.wait_for_function("!productUI.settings.open")
        page.locator("#account-menu-toggle").click()
        page.get_by_role("menuitem", name="Использование", exact=True).wait_for()
        page.keyboard.press("Escape")

        # правая панель: карточки, Среда и Контекст плавно уступают место изменениям
        page.locator("#toggle-right").click()
        page.wait_for_timeout(600)
        heads = page.locator("#inspector-content .card-head")
        assert heads.count() == 5
        assert page.locator("#card-environment").evaluate("node => node.classList.contains('open')")
        assert "C:" in page.locator("#environment").inner_text() or "Не выбран" in page.locator("#environment").inner_text()
        page.evaluate("state.latestUsage = {usage: {prompt_tokens: 50000}, dokky: {}}")
        page.wait_for_function("document.getElementById('context-percent').textContent === '25%'")
        page.screenshot(path=str(output / "cards-overview.png"))
        page.locator("#inspector-tab-diff").click()
        page.wait_for_timeout(700)
        assert page.locator("#inspector-content").get_attribute("data-focus") == "diff"
        assert page.locator("#card-environment").evaluate("node => node.getBoundingClientRect().height") < 2
        assert page.locator("#card-context").evaluate("node => node.getBoundingClientRect().height") < 2
        diff_box = page.locator("#inspector-diff").bounding_box()
        assert diff_box["height"] > 500, diff_box
        page.screenshot(path=str(output / "cards-focus.png"))
        page.locator("#inspector-tab-diff").click()
        page.wait_for_timeout(700)
        assert page.locator("#inspector-content").get_attribute("data-focus") == ""
        assert page.locator("#card-environment").evaluate("node => node.getBoundingClientRect().height") > 60
        page.locator("#card-context .card-head").click()
        page.wait_for_timeout(500)
        assert not page.locator("#card-context").evaluate("node => node.classList.contains('open')")
        page.locator("#toggle-right").click()

        # история переходов
        page.evaluate("openChat(state.chats[0])")
        page.wait_for_function("state.chat?.id === 1")
        page.evaluate("openChat(state.chats[1])")
        page.wait_for_function("state.chat?.id === 2")
        page.wait_for_function("document.getElementById('nav-back').disabled === false")
        page.locator("#nav-back").click()
        page.wait_for_function("state.chat?.id === 1")
        page.locator("#nav-forward").click()
        page.wait_for_function("state.chat?.id === 2")
        assert page.locator("#nav-forward").is_disabled()

        # узкое окно: панель просит у окна место, а не сжимает чат
        page.evaluate("""() => {
          window.adjustCalls = [];
          window.koddiWindowBridge = {adjustWidth(side, delta, ms, callback) {
            adjustCalls.push([side, delta, ms]);
            callback(JSON.stringify({[side]: delta}));
          }};
        }""")
        page.set_viewport_size({"width": 800, "height": 700})
        page.wait_for_timeout(300)
        page.evaluate("setPanel('left', true, false)")
        page.wait_for_timeout(400)
        page.evaluate("setPanel('left', false, false)")
        page.wait_for_function("!document.getElementById('shell').classList.contains('left-collapsed')")
        page.wait_for_timeout(500)
        calls = page.evaluate("adjustCalls")
        assert len(calls) == 1 and calls[0][0] == "right" and calls[0][1] == 200 and calls[0][2] == 380, calls
        page.evaluate("setPanel('left', true, false)")
        page.wait_for_function("document.getElementById('shell').classList.contains('left-collapsed')")
        page.wait_for_timeout(500)
        # при закрытии окно не уменьшается
        assert page.evaluate("adjustCalls") == calls, page.evaluate("adjustCalls")
        page.screenshot(path=str(output / "narrow.png"))
        assert page.locator(".shell-controls-left").bounding_box()["x"] < 20  # закрытое меню: кнопки у левого края
        assert not errors, errors
        assert not unexpected, unexpected
        browser.close()
    server.shutdown()
    print(f"Проверка оболочки приложения пройдена. Снимки: {output}")


if __name__ == "__main__":
    main()
