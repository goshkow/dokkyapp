"""Картинки, видео, файлы и прочая разметка ответов Докки: синтетический API, без запросов к ИИ."""

import base64
import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mNkYPhfz0AEYBxVSF+FAP5FDvcfRYWgAAAAAElFTkSuQmCC"
)
CONTENT = """Вот фото салона «Атома»:

![Салон Атом](tg://photo?id=media_a)

Второе фото ![Багажник](tg://photo?id=media_b) и внешнее ![Внешнее](https://images.example.org/atom.jpg)

Проект описан на сайте [Кама](https://kama.example.org/atom) [↗](https://kama.example.org/atom).

[Обзор на YouTube](https://www.youtube.com/watch?v=dQw4w9WgXcQ)

Файл: ![Характеристики](https://files.example.org/spec.pdf)

| Параметр | Значение |
| --- | --- |
| Запас хода | 400 км |

- [x] Готово
- [ ] В планах

Секрет: ||скрытый текст|| и <tg-spoiler>ещё один</tg-spoiler>.

<details><summary>Подробнее</summary>

Внутри **жирный** текст.

</details>
"""


def main():
    output = Path(tempfile.mkdtemp(prefix="koddi-rich-ui-"))
    web = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"
    models = [{"id": "dokky-default", "name": "Докки (авто)", "roles": ["chat", "search"], "available": True}]
    chat = {"id": "chat_1", "title": "Машины атом", "mode": "chat", "model": "dokky-default", "version": 1}
    artifacts = [
        {"id": "art_a", "type": "image", "name": "Салон Атом", "file_id": "file_a"},
        {"id": "art_b", "type": "image", "name": "Багажник", "file_id": "file_b"},
        {"id": "art_c", "type": "image", "name": "Лишняя", "file_id": "file_c"},
    ]
    requested = []

    def asset(route):
        relative = urlsplit(route.request.url).path.removeprefix("/ui/") or "index.html"
        target = (web / relative).resolve()
        if not target.is_relative_to(web.resolve()) or not target.is_file():
            route.fulfill(status=404, body="Не найдено")
            return
        route.fulfill(path=str(target))

    def upstream(route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path.removeprefix("/api/dokky/")
        if path == "cloud/preferences":
            route.fulfill(content_type="application/json", body="{}")
            return
        if path == "cloud/me/avatar":
            route.fulfill(status=404, body="")
            return
        if path == "media":
            requested.append(parse_qs(parsed.query)["url"][0])
            route.fulfill(content_type="image/png", body=PNG)
            return
        if path.startswith("cloud/files/") and path.endswith("/content"):
            requested.append(path)
            route.fulfill(content_type="image/png", body=PNG)
            return
        if path == "session":
            value = {"authenticated": True, "user": {"display_name": "Иван"},
                     "bootstrap": {"models": models, "preferences": {}, "capabilities": {}}}
        elif path == "bootstrap":
            value = {"models": models, "preferences": {}, "capabilities": {}}
        elif path == "cloud/chats" and request.method == "GET":
            value = {"items": [chat], "next_cursor": None}
        elif path == "cloud/chats/chat_1":
            value = chat
        elif path == "cloud/chats/chat_1/messages":
            value = {"items": [
                {"id": "msg_1", "role": "user", "content": "найди ее фотки", "version": 1},
                {"id": "msg_2", "role": "assistant", "content": CONTENT, "artifacts": artifacts,
                 "citations": [], "version": 1},
            ], "next_cursor": None}
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
        page = browser.new_page(viewport={"width": 1280, "height": 1100}, bypass_csp=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", local_api)
        page.route("**/api/dokky/**", upstream)
        page.route("**/api/settings", lambda route: route.fulfill(json={"theme": "dark", "ui_motion": "full", "permission_profile": "ask"}))
        page.route("**/ui/**", asset)
        page.goto("http://127.0.0.1:8878/ui/", wait_until="domcontentloaded")
        page.wait_for_function("typeof dokkyState !== 'undefined' && dokkyState.account?.authenticated")
        page.evaluate("switchDokkyScope('chat')")
        page.wait_for_function("window.koddiProductScope === 'chat' && dokkyState.chats.length")
        page.evaluate("openChat(dokkyState.chats[0])")
        page.locator(".message-image").first.wait_for()
        page.wait_for_timeout(800)
        body = page.locator(".turn-message.assistant .message-body")
        images = page.locator(".turn-message.assistant .message-body img.message-image")
        # три картинки в тексте: две вложения и одна внешняя; «Лишняя» остаётся в ленте вложений
        assert images.count() == 3, images.count()
        assert page.locator(".message-attachments .message-attachment").count() == 1
        sources = page.evaluate("[...document.querySelectorAll('.message-body img.message-image')].map(i=>i.getAttribute('src'))")
        assert sources[0].endswith("/files/file_a/content") and sources[1].endswith("/files/file_b/content"), sources
        assert sources[2].startswith("/api/dokky/media?url=https%3A%2F%2Fimages.example.org"), sources
        page.wait_for_function("[...document.querySelectorAll('.message-body img.message-image')].every(i=>i.complete&&i.naturalWidth>0)")
        # ссылки: дубль стрелки убран, подпись в .link-label
        text = body.inner_text()
        assert "↗" not in text, text
        assert page.locator(".message-body a[href='https://kama.example.org/atom'] .link-label").count() == 1
        # видео, файл, таблица, список, спойлер, details
        assert page.locator(".rich-video-card").count() == 1
        assert page.locator(".rich-video-card img").first.get_attribute("src").startswith("/api/dokky/media?url=https%3A%2F%2Fi.ytimg.com")
        assert page.locator("a.message-file").count() == 1
        assert page.locator(".message-body table").count() == 1
        probe = page.evaluate("() => { const d = document.createElement('div'); renderMarkdown(d, '- [x] a', true, {}); return d.innerHTML; }")
        assert "☑" in text and "☐" in text, probe
        assert page.locator(".rich-spoiler").count() == 2
        page.locator(".rich-spoiler").first.click()
        assert page.locator(".rich-spoiler.revealed").count() == 1
        assert page.locator(".message-body details summary").inner_text() == "Подробнее"
        page.locator(".message-body details summary").click()
        assert "жирный" in page.locator(".message-body details").inner_text()
        page.screenshot(path=str(output / "rich-chat.png"))
        # просмотр картинки по щелчку
        images.first.click()
        page.locator(".cloud-image-viewer[open]").wait_for()
        page.screenshot(path=str(output / "rich-viewer.png"))
        assert not errors, errors
        browser.close()
    print(f"Проверка разметки ответов пройдена. Снимки: {output}")


if __name__ == "__main__":
    main()
