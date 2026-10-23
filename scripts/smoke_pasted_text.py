"""Большая вставка и восстановление исходника в собранном интерфейсе, без модели."""
from email import policy
from email.parser import BytesParser

from smoke_next import check, development_api, open_chat, sse
from ui_testkit import browser_page


def paste(page, text, selector="#prompt"):
    return page.locator(selector).evaluate("""(node, text) => {
      const clipboardData = new DataTransfer(); clipboardData.setData('text/plain', text);
      return node.dispatchEvent(new ClipboardEvent('paste', { bubbles:true, cancelable:true, clipboardData }));
    }""", text)


def scenario_pasted_text_send_and_restore():
    api = development_api()
    files, sent = [], []

    def upload(route):
        request = route.request
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.post_data_buffer
        )
        part = next(message.iter_parts())
        files.append((part.get_filename(), part.get_payload(decode=True)))
        route.fulfill(status=200, json={"id": len(files), "original_name": files[-1][0]})

    def send(route):
        if route.request.method != "POST":
            return route.fallback()
        sent.append(route.request.post_data_json)
        if len(sent) == 1:
            return route.fulfill(status=503, json={"detail": "Временный отказ отправки"})
        route.fulfill(status=200, content_type="text/event-stream", body=sse([("turn.completed", {"turn_id": 8})]))

    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        open_chat(page)
        page.route("**/api/attachments", upload)
        page.route("**/api/chats/1/messages", send)
        page.fill("#prompt", "Проверь текст")
        check(paste(page, "Короткая вставка"), "короткая вставка остаётся обычной")
        source = "Строка без разметки.\r\n" * 400
        check(not paste(page, source), "большая вставка перехвачена")
        card = page.locator(".attachment-card.is-pasted-text")
        card.locator(".attachment-as-text").wait_for()
        page.wait_for_function("!document.querySelector('.attachment-card.is-upload')")
        check(files == [("Вставка.txt", source.encode())], "исходные байты TXT сохранены")
        check(page.input_value("#prompt") == "Проверь текст", "запрос сохранён")
        card.locator(".attachment-as-text").click()
        page.wait_for_function("expected => document.querySelector('#prompt').value === expected", arg="Проверь текст\n\n" + source.replace("\r\n", "\n"))
        check(page.input_value("#prompt") == "Проверь текст\n\n" + source.replace("\r\n", "\n"), "восстановление дополняет запрос")
        page.fill("#prompt", "")
        markdown = "# Задача\n\n" + "**Текст** с разметкой.\n" * 400
        paste(page, markdown)
        page.wait_for_function("document.querySelector('.attachment-card:not(.is-upload)')")
        check(files[-1] == ("Вставка.md", markdown.encode()), "Markdown сохраняется дословно")
        check(page.input_value("#prompt") == "", "тело не дублируется в поле")
        page.wait_for_timeout(350)
        check(page.locator(".attachment-card").bounding_box()["height"] >= 70, "карточка видна после анимации")
        page.screenshot(path="/tmp/dokky-pasted-text.png")
        page.locator("#send").click()
        page.wait_for_selector(".toast.error")
        check(page.input_value("#prompt") == "", "отказ отправки не подставляет служебную подпись в черновик")
        check(page.locator(".attachment-as-text").count() == 1, "отказ отправки сохраняет вложение и источник")
        page.locator("#send").click()
        page.wait_for_timeout(600)
        check(len(sent) == 2 and all(item["attachment_ids"] == [2] for item in sent), "повтор отправки сохраняет то же вложение")
        check(markdown not in str(sent[0]) and "pastedText" not in str(sent[0]), "локальный источник не дублируется в API")
        check(not page.errors, str(page.errors))


def scenario_paste_pending_failed_and_menu():
    api = development_api()
    pending = []
    source = "Текст. " * 1000
    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        open_chat(page)
        page.set_viewport_size({"width": 600, "height": 820})
        page.route("**/api/attachments", lambda route: pending.append(route))
        paste(page, source)
        button = page.locator(".attachment-as-text")
        button.wait_for()
        button.click()
        page.wait_for_function("expected => document.querySelector('#prompt').value === expected", arg=source)
        check(page.input_value("#prompt") == source, "восстановление во время загрузки")
        for route in pending:
            try:
                route.fulfill(status=200, json={"id": 42})
            except Exception:
                pass  # запрос мог уже завершиться отменой
        page.wait_for_timeout(200)
        check(page.locator(".attachment-card").count() == 0, "поздний ответ не возвращает вложение")
        page.unroute("**/api/attachments")
        page.route("**/api/attachments", lambda route: route.fulfill(status=503, json={"detail": "Ошибка загрузки"}))
        page.fill("#prompt", "")
        paste(page, source)
        page.wait_for_selector(".attachment-card.upload-error")
        page.locator(".attachment-as-text").click()
        page.wait_for_function("expected => document.querySelector('#prompt').value === expected", arg=source)
        check(page.input_value("#prompt") == source, "исходник доступен после сетевой ошибки")
        page.fill("#prompt", "")
        page.evaluate("""text => Object.defineProperty(navigator, 'clipboard', { configurable:true, value:{ readText:async () => text } })""", source)
        page.locator("#prompt").click(button="right")
        page.get_by_role("menuitem", name="Вставить", exact=False).click()
        page.wait_for_selector(".attachment-card.upload-error")
        check(page.input_value("#prompt") == "", "вставка через меню тоже становится файлом")
        check(not page.errors, str(page.errors))


SCENARIOS = [scenario_pasted_text_send_and_restore, scenario_paste_pending_failed_and_menu]
