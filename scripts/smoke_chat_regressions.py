"""Регрессии отправки и поверхностей чата без реального провайдера."""
from __future__ import annotations

from smoke_next import check, development_api, open_chat, sse
from ui_testkit import browser_page


def scenario_enter_single_submission():
    api = development_api()
    created = {"count": 0, "sent": 0}

    def chats(method, _path, _body, _query):
        if method != "POST":
            return api.local_chats
        created["count"] += 1
        return {"id": 50, "title": "Новый чат", "mode": "chat", "model": "dokky-code", "project_id": None}

    api.extra["/api/chats"] = chats
    for suffix, response in [("messages", []), ("turn", {"active": False}), ("plan", {}), ("changes", [])]:
        api.extra[f"/api/chats/50/{suffix}"] = lambda _m, _p, _b, _q, value=response: value

    def message(route):
        if route.request.method != "POST":
            return route.fallback()
        created["sent"] += 1
        return route.fulfill(status=200, content_type="text/event-stream", body=sse([("turn.completed", {"turn_id": 1})]))

    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        page.wait_for_timeout(800)
        page.route("**/api/chats/50/messages", message)
        page.fill("#prompt", "Одна задача")
        page.keyboard.down("Enter")
        for _ in range(8):
            page.keyboard.down("Enter")
        page.wait_for_selector("dialog[open]")
        check(page.locator("dialog[open]").count() == 1, "один выбор рабочей папки при удержании Enter")
        page.click("dialog[open] button[type=submit]")
        # одновременно проверяем защиту отправки до завершения создания чата
        page.evaluate("""() => { const input = document.querySelector('#prompt'); for (let i=0; i<12; i++) input.dispatchEvent(new KeyboardEvent('keydown', { key:'Enter', bubbles:true, repeat:false })); }""")
        page.keyboard.up("Enter")
        page.wait_for_timeout(1000)
        check(created == {"count": 1, "sent": 1}, f"повтор отправки: {created}")
        check(not page.errors, str(page.errors))


def scenario_compact_approval_and_toast():
    api = development_api()
    private_code = "<html>" + "source-code-should-stay-hidden" * 5000
    api.extra["/api/agent/requests"] = lambda _m, _p, _b, _q: [{
        "id": 3, "kind": "approval", "chat_id": 1,
        "payload": {"name": "file.write_patch", "arguments": {"path": "landing/index.html", "content": private_code}},
    }]
    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        open_chat(page)
        approval = page.locator(".approval-panel")
        check("изменить файл" in approval.inner_text().lower(), "понятное название действия")
        check("landing/index.html" in approval.inner_text(), "путь изменяемого файла")
        check("source-code-should-stay-hidden" not in approval.inner_text(), "тело файла скрыто")
        check(approval.bounding_box()["height"] < 220, "подтверждение компактное")
        page.click(".approval-details > summary")
        check(page.locator(".approval-details-scroll").bounding_box()["height"] <= 185, "ограничение раскрытых подробностей")
        page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', { configurable:true, value: { writeText: () => Promise.reject(new Error('denied')) } })""")
        page.locator('[aria-label="Скопировать сообщение"]').first.click()
        page.wait_for_selector(".toast.error")
        page.wait_for_timeout(250)
        toast = page.locator(".toast.error").bounding_box()
        field = page.locator("#composer .composer-field").bounding_box()
        check(toast["y"] + toast["height"] <= field["y"] - 6, "ошибка выше поля ввода")
        check(page.locator(".toast-stack").evaluate("node => node.matches(':popover-open')"), "ошибка в верхнем слое")
        check(not page.errors, str(page.errors))


def scenario_submission_failure_retry():
    api = development_api()
    counts = {"create": 0, "send": 0}

    def create(method, _p, _b, _q):
        if method != "POST":
            return api.local_chats
        counts["create"] += 1
        if counts["create"] == 1:
            return 503, {"detail": "Временный отказ"}
        return {"id": 50, "title": "Новый чат", "mode": "chat", "model": "dokky-code", "project_id": None}

    def send(route):
        if route.request.method != "POST":
            return route.fallback()
        counts["send"] += 1
        route.fulfill(status=200, content_type="text/event-stream", body=sse([("turn.completed", {"turn_id": 1})]))

    api.extra["/api/chats"] = create
    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        page.wait_for_timeout(800)
        page.route("**/api/chats/50/messages", send)
        page.fill("#prompt", "Попытка отправки")
        page.keyboard.down("Enter")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector(".toast.error")
        page.keyboard.up("Enter")
        check(counts == {"create": 1, "send": 0}, str(counts))
        check(page.locator("#prompt").input_value() == "Попытка отправки", "отказ не теряет черновик")
        page.wait_for_function("!document.querySelector('dialog[open]')")
        page.locator("#prompt").press("Enter")
        page.wait_for_timeout(700)
        check(counts == {"create": 2, "send": 1}, str(counts))
        check(not page.errors, str(page.errors))


def scenario_navigation_during_preparation():
    api = development_api()
    pending = []
    sent = []

    def create(route):
        if route.request.method == "POST":
            pending.append(route)
        else:
            route.fallback()

    def send(route):
        if route.request.method != "POST":
            return route.fallback()
        sent.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="text/event-stream", body=sse([("turn.completed", {"turn_id": 1})]))

    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        page.wait_for_timeout(800)
        page.route("**/api/chats", create)
        page.route("**/api/chats/1/messages", send)
        page.fill("#prompt", "Зависшая подготовка")
        page.locator("#prompt").press("Enter")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_timeout(200)
        check(len(pending) == 1, "первый запрос ожидает ответа")
        open_chat(page)
        page.fill("#prompt", "Задача в другом чате")
        page.locator("#prompt").press("Enter")
        page.wait_for_timeout(700)
        check(len(sent) == 1 and sent[0]["content"] == "Задача в другом чате", "старый запрос не блокирует новый чат")
        for route in pending:
            route.abort()
        check(not page.errors, str(page.errors))


def scenario_inline_diff_and_command():
    api = development_api()
    messages = api.extra["/api/chats/1/messages"]("GET", "", None, {})
    messages[1]["metadata"]["events"][-1]["data"]["result"]["change_set_id"] = 5
    api.extra["/api/agent/requests"] = lambda _m, _p, _b, _q: [{
        "id": 4, "kind": "approval", "chat_id": 1,
        "payload": {"name": "shell.run", "arguments": {"command": "pytest tests/test_runs.py -q"}},
    }]
    with browser_page(api) as page:
        page.wait_for_selector("#prompt")
        open_chat(page)
        panel = page.locator(".approval-panel")
        check("Докки хочет выполнить команду" in panel.inner_text(), "название Докки в подтверждении")
        check("pytest tests/test_runs.py -q" not in panel.inner_text(), "точная команда скрыта по умолчанию")
        panel.locator(".approval-details > summary").click()
        check(panel.locator(".approval-preview code").inner_text() == "pytest tests/test_runs.py -q", "точная команда раскрывается")
        reject, approve = panel.get_by_role("button", name="Отклонить"), panel.get_by_role("button", name="Разрешить")
        check(approve.bounding_box()["x"] > reject.bounding_box()["x"], "разрешить справа")
        check(approve.bounding_box()["height"] <= 34, "небольшие кнопки подтверждения")
        page.click(".journal-completed > summary")
        page.wait_for_timeout(300)
        page.click(".journal-segment > summary")
        page.wait_for_timeout(300)
        card = page.locator(".journal-change")
        check(card.count() == 1 and "app.py" in card.inner_text(), "файл изменений в журнале")
        check(card.locator(".diff-line.added").count() == 0, "дифф скрыт до раскрытия")
        card.locator("summary").click()
        card.locator(".diff-line.added").first.wait_for()
        check(card.locator(".diff-line.added").count() == 2, "добавленные строки раскрытого диффа")
        check(card.locator(".diff-line.removed").count() == 1, "удалённая строка раскрытого диффа")
        check(not page.errors, str(page.errors))


SCENARIOS = [scenario_enter_single_submission, scenario_compact_approval_and_toast, scenario_submission_failure_retry, scenario_navigation_during_preparation, scenario_inline_diff_and_command]

if __name__ == "__main__":
    for scenario in SCENARIOS:
        scenario()
        print("OK", scenario.__name__)
