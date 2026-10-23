"""Чат и поиск как на сайте Докки: синтетический API, без запросов к настоящему ИИ."""

import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


def main():
    output = Path(tempfile.mkdtemp(prefix="koddi-search-ui-"))
    web = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"
    models = [
        {"id": "dokky-default", "name": "Докки (авто)", "roles": ["chat", "search"], "available": True},
        {"id": "dokky-fast", "name": "Докки быстрый", "roles": ["chat"], "available": True},
        {"id": "dokky-enhanced", "name": "Докки улучшенный", "roles": ["chat"], "available": True},
        {"id": "dokky-code", "name": "Разработка", "roles": ["developer"], "available": True},
    ]
    search_chat = {"id": "chat_s1", "title": "курс доллара сегодня", "mode": "search", "version": 1}
    created = []
    citations = [
        {"id": "S1", "url": "https://example.org/rates", "title": "Официальный курс", "snippet": "Курс на сегодня."},
        {"id": "S2", "url": "https://news.example.com/market", "title": "Рынок сегодня", "snippet": "Обзор торгов."},
    ]
    answer = "## Курс\n\nОфициальный курс зафиксирован на сегодня.\n\n- Первый источник\n- Второй источник"
    authenticated = True

    def asset(route):
        relative = urlsplit(route.request.url).path.removeprefix("/ui/") or "index.html"
        target = (web / relative).resolve()
        if not target.is_relative_to(web.resolve()) or not target.is_file():
            route.fulfill(status=404, body="Не найдено")
            return
        route.fulfill(path=str(target))

    def upstream(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api/dokky/")
        if path == "cloud/preferences":
            route.fulfill(content_type="application/json", body="{}")
            return
        if path == "cloud/me/avatar":
            route.fulfill(status=404, body="")
            return
        query = parse_qs(urlsplit(request.url).query)
        body = request.post_data_json if request.method in {"POST", "PATCH"} and request.post_data else {}
        value = {}
        if path == "session":
            value = {"authenticated": authenticated, "user": {"display_name": "Иван Петров"},
                     "bootstrap": {"models": models, "preferences": {}, "capabilities": {}}}
        elif path == "bootstrap":
            value = {"models": models, "preferences": {}, "capabilities": {}}
        elif path == "cloud/chats" and request.method == "GET":
            mode = query.get("mode", ["chat"])[0]
            value = {"items": [search_chat] if mode == "search" else [], "next_cursor": None}
        elif path == "cloud/chats" and request.method == "POST":
            created.append(body)
            assert body["mode"] == "search", body
            value = {"id": "chat_s2", "title": body.get("title", ""), "mode": "search", "version": 1}
        elif path == "cloud/chats/chat_s2/messages" and request.method == "POST":
            value = {"run_id": "run_s2"}
        elif path in {"cloud/chats/chat_s2/messages", "cloud/chats/chat_s1/messages"}:
            value = {"items": [
                {"id": "msg_u", "role": "user", "content": "курс доллара сегодня"},
                {"id": "msg_a", "role": "assistant", "content": answer, "citations": citations},
            ], "next_cursor": None}
        elif path == "cloud/runs/run_s2/events":
            stream = [
                (1, "search.started", {"query": "курс доллара сегодня"}),
                (2, "search.source", {"source": citations[0], "index": 0}),
                (3, "search.source", {"source": citations[1], "index": 1}),
                (4, "message.delta", {"message_id": "pending_run_s2", "content": answer[:60], "offset": 0}),
                (5, "message.delta", {"message_id": "pending_run_s2", "content": answer[60:], "offset": 60}),
                (6, "turn.completed", {"message_id": "msg_a"}),
            ]
            route.fulfill(content_type="text/event-stream", body="".join(
                f"id: {i}\nevent: {e}\ndata: {json.dumps(d)}\n\n" for i, e, d in stream))
            return
        elif path == "cloud/subscription":
            value = {"plan": "plus", "remaining_percent": 80, "developer": {}}
        else:
            raise AssertionError(f"Неожиданный маршрут: {request.method} {path}")
        route.fulfill(content_type="application/json", body=json.dumps(value))

    def local_api(route):
        path = urlsplit(route.request.url).path
        if path == "/api/events":
            route.fulfill(content_type="text/event-stream", body="")
        elif path == "/api/app/diagnostics":
            route.fulfill(json={"build_number": "test"})
        elif path in {"/api/projects", "/api/chats", "/api/models/options"}:
            route.fulfill(json=[])
        elif path == "/api/modes":
            route.fulfill(json=[{"id": "development", "name": "development"}])
        else:
            raise AssertionError("Неожиданный локальный запрос: " + path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", local_api)
        page.route("**/api/dokky/**", upstream)
        page.route("**/api/settings", lambda route: route.fulfill(json={"theme": "dark", "ui_motion": "full", "permission_profile": "ask"}))
        page.route("**/ui/**", asset)
        page.goto("http://127.0.0.1:8878/ui/", wait_until="domcontentloaded")
        page.wait_for_function("window.koddiProductScope && typeof dokkyState !== 'undefined' && dokkyState.account?.authenticated")
        page.evaluate("switchDokkyScope('chat')")
        page.wait_for_function("window.koddiProductScope === 'chat'")
        page.wait_for_function("document.querySelector('.welcome h1').textContent.length > 8 && !document.querySelector('.welcome').classList.contains('typing')")
        greeting = page.locator(".welcome h1").inner_text()
        assert "Иван" in greeting, greeting
        assert page.locator(".composer-toolbar").is_hidden()
        assert page.locator("#segmented-mode").is_visible()
        center = page.evaluate("""() => { const s=document.querySelector('#segmented-mode').getBoundingClientRect(), c=document.querySelector('.composer-field').getBoundingClientRect(); return Math.abs((s.left+s.right)/2-(c.left+c.right)/2); }""")
        page.screenshot(path=str(output / "debug.png"))
        assert center < 1, (center, output)
        page.screenshot(path=str(output / "chat-welcome.png"))
        # меню «+» с режимами
        page.locator("#attach").click()
        page.get_by_role("menuitem", name="Режим").click()
        page.get_by_role("menuitemradio", name="Быстрый").wait_for()
        page.screenshot(path=str(output / "chat-mode-menu.png"))
        page.get_by_role("menuitemradio", name="Быстрый").click()
        page.wait_for_function("document.querySelector('#model').value === 'dokky-fast'")
        # поиск
        page.locator("#product-search").click()
        page.locator("#ai-search-surface").wait_for(state="visible")
        assert page.locator("#feed").is_hidden() or page.evaluate("getComputedStyle(document.querySelector('#feed')).visibility") == "hidden"
        assert page.locator(".ai-search-intro h1").inner_text() == "Что хотите найти?"
        assert page.locator("#chats").count() == 1
        page.screenshot(path=str(output / "search-intro.png"))
        page.locator("#ai-search-query").click()
        page.get_by_text("курс доллара сегодня", exact=True).wait_for()
        page.wait_for_timeout(500)
        page.screenshot(path=str(output / "search-history.png"))
        page.locator("#ai-search-query").fill("курс доллара сегодня")
        page.locator(".ai-search-form .send-button").click()
        page.locator(".ai-search-result").first.wait_for()
        page.locator(".ai-search-source-cards .ai-source-card").first.wait_for()
        page.wait_for_function("!document.querySelector('.ai-search-followup').hidden")
        assert len(created) == 1 and created[0]["title"].startswith("курс")
        assert page.locator(".ai-search-result").count() == 2
        assert "Официальный курс" in page.locator(".ai-search-answer").inner_text()
        page.screenshot(path=str(output / "search-result.png"))
        # возврат в чат сохраняет поверхность и открывает список
        page.locator("#product-chat").click()
        page.wait_for_function("document.querySelector('#ai-search-surface').hidden")
        page.locator(".welcome").wait_for()
        page.locator("#product-search").click()
        assert page.locator(".ai-search-result").count() == 2
        # светлая тема и узкие окна
        page.evaluate("document.documentElement.dataset.theme='light'")
        page.wait_for_timeout(300)
        page.screenshot(path=str(output / "light-search.png"))
        page.locator("#product-chat").click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(output / "light-chat.png"))
        page.evaluate("document.documentElement.dataset.theme='dark'")
        for width, height, name in ((390, 800, "mobile"), (860, 700, "narrow")):
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("setPanel('left', true, false)")
            page.wait_for_timeout(500)
            page.screenshot(path=str(output / f"{name}-chat.png"))
            page.locator("#product-search").click()
            page.wait_for_timeout(700)
            page.screenshot(path=str(output / f"{name}-search.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
            page.locator("#product-chat").click()
            page.wait_for_timeout(600)
        assert not errors, errors
        browser.close()
    print(f"Проверка чата и поиска пройдена. Снимки: {output}")


if __name__ == "__main__":
    main()
