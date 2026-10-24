"""Проверки нового интерфейса (React) в браузере: разработка, облачный чат, поиск, настройки, страницы, правая панель.

Запуск: python scripts/smoke_next.py (сначала `npm run build` в frontend/). Ответы сервера подставные, ИИ не вызывается.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from billing_mock import Billing  # noqa: E402
from ui_testkit import Api, browser_page  # noqa: E402

TURN = {"id": 7, "status": "completed", "created_at": "2026-10-17T10:00:00+00:00", "completed_at": "2026-10-17T10:02:13+00:00", "timeline": []}
DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,4 @@\n import os\n-print('a')\n+print('b')\n+print('c')\n x = 1\n"


def sse(events):
    out = ""
    for index, (kind, data) in enumerate(events):
        out += f"id: {index}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    return out


def development_api():
    api = Api()
    events = [
        {"event": "tool.call", "data": {"id": "a1", "name": "file.read", "arguments": {"path": "app.py"}}, "sequence": 3, "created_at": "2026-10-17T10:00:10+00:00"},
        {"event": "tool.result", "data": {"id": "a1", "name": "file.read", "arguments": {"path": "app.py"}, "result": {"ok": True}}, "sequence": 4, "created_at": "2026-10-17T10:00:11+00:00"},
        {"event": "tool.call", "data": {"id": "a2", "name": "file.write_patch", "arguments": {"path": "app.py"}}, "sequence": 5, "created_at": "2026-10-17T10:01:10+00:00"},
        {"event": "tool.result", "data": {"id": "a2", "name": "file.write_patch", "arguments": {"path": "app.py"}, "result": {"ok": True, "added_lines": 4, "removed_lines": 1}}, "sequence": 6, "created_at": "2026-10-17T10:01:11+00:00"},
    ]
    messages = [
        {"id": 1, "role": "user", "content": "Почему приложение фризится?", "metadata": {"turn_id": 7}, "created_at": "2026-10-17T10:00:00+00:00"},
        {"id": 2, "role": "assistant", "content": "Сначала посмотрю код.", "metadata": {"kind": "activity", "channel": "commentary", "status": "completed", "sequence": 2, "turn": TURN, "turn_id": 7, "events": events}, "created_at": "2026-10-17T10:00:05+00:00"},
        {"id": 3, "role": "assistant", "content": "## Итог\n\nПричина в **таймере**:\n\n```python\nprint('hello')\n```\n\n- первый\n- второй", "metadata": {"channel": "final", "status": "completed", "turn_id": 7, "sequence": 9, "turn": TURN}, "created_at": "2026-10-17T10:02:13+00:00"},
    ]
    change = {"id": 5, "description": "Правка app.py", "status": "applied", "diff": DIFF, "files": [{"path": "app.py", "before_hash": "a", "after_hash": "b"}]}
    api.extra["/api/chats/1/messages"] = lambda m, p, b, q: messages
    api.extra["/api/chats/1/turn"] = lambda m, p, b, q: {"active": False}
    api.extra["/api/chats/1/changes"] = lambda m, p, b, q: [change]
    api.extra["/api/chats/1/plan"] = lambda m, p, b, q: {"turn_id": 7, "steps": [{"title": "Прочитать код", "status": "completed"}, {"title": "Исправить таймер", "status": "running"}], "document": "# План\n\n1. Прочитать\n2. Исправить", "ready": True}
    api.extra["/api/projects/1/files"] = lambda m, p, b, q: {"items": [{"path": "app.py"}, {"path": "README.md"}]}
    api.extra["/api/projects/1/file"] = lambda m, p, b, q: {"content": "print(1)\n", "hash": "h1", "read_only": False}
    api.extra["/api/changes"] = lambda m, p, b, q: [change]
    api.extra["/api/chats/1/context-breakdown"] = lambda m, p, b, q: {
        "window": 200000, "used": 3000, "compact_at": 150000, "exact": None, "estimate": 3000,
        "rows": [{"key": "system", "name": "Системные инструкции", "tokens": 3000, "items": []}, {"key": "free", "name": "Свободно", "tokens": 147000, "items": []}],
    }
    api.extra["/api/browser/live"] = lambda m, p, b, q: []
    return api


def open_chat(page, title="Фризы при возврате из фона"):
    page.click("text=zaprethub.new")
    page.wait_for_timeout(400)
    page.click(f"text={title}")
    page.wait_for_timeout(900)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def scenario_boot_and_open():
    api = development_api()
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        page.wait_for_timeout(1400)
        greeting = page.locator(".welcome h1").inner_text().strip()
        check(len(greeting) > 8 and page.locator(".welcome .actions button").count() == 2, f"приветствие разработки печатается, кнопки проектов на месте: {greeting!r}")
        check(page.locator("#title").inner_text() == "", "у нового чата нет названия")
        open_chat(page)
        check(page.locator("#title").inner_text() == "Фризы при возврате из фона", "название чата")
        check(page.locator(".journal-completed .journal-summary-copy").inner_text() == "Работал 2 мин. 13 сек.", "итог журнала")
        check(page.locator("article.message.user").count() == 1, "одно сообщение пользователя")
        check(page.locator(".code-block").count() == 1, "блок кода")
        # журнал раскрывается плавно и показывает этапы
        page.click(".journal-completed > summary")
        page.wait_for_timeout(500)
        check(page.locator(".journal-segment").count() == 1, "этап журнала")
        page.click(".journal-segment > summary")
        page.wait_for_timeout(500)
        check(page.locator(".journal-segment .activity-line").count() == 2, "действия этапа")
        check("Правка" in page.locator(".journal-segment .journal-badge").inner_text() or "+4" in page.locator(".journal-segment .journal-badge").inner_text(), "счётчик правок")
        check(not page.errors, f"ошибки страницы: {page.errors}")
        check(not api.unexpected, f"лишние запросы: {api.unexpected}")


def scenario_send_stream():
    api = development_api()
    state = {"sent": False}
    answer = "Всё **отлично**, спасибо!\n\n1. один\n2. два"
    user = {"id": 21, "role": "user", "content": "Привет", "metadata": {"turn_id": 9}, "created_at": "2026-10-17T11:00:00+00:00"}
    final = {"id": 22, "role": "assistant", "content": answer, "metadata": {"channel": "final", "status": "completed", "turn_id": 9, "sequence": 5, "turn": {**TURN, "id": 9}}, "created_at": "2026-10-17T11:00:19+00:00"}
    api.extra["/api/chats"] = lambda m, p, b, q: ({"id": 50, "title": "Новый чат", "mode": "chat", "model": "dokky-code", "project_id": None, "updated_at": "2026-10-17T11:00:00+00:00"} if m == "POST" else api.local_chats)
    api.extra["/api/chats/50/messages"] = lambda m, p, b, q: [user, final] if state["sent"] else []
    api.extra["/api/chats/50/turn"] = lambda m, p, b, q: {"active": False}
    api.extra["/api/chats/50/plan"] = lambda m, p, b, q: {"steps": [], "document": "", "turn_id": None}
    api.extra["/api/chats/50/changes"] = lambda m, p, b, q: []

    def send_route(route):
        if route.request.method != "POST":
            return route.fallback()
        state["sent"] = True
        body = sse([
            ("turn.started", {"turn_id": 9, "chat_title": "Приветствие"}),
            ("message.delta", {"message_id": 22, "content": "Всё **отлично**, ", "offset": 0}),
            ("message.delta", {"message_id": 22, "content": "спасибо!\n\n1. один\n2. два", "offset": 16}),
            ("turn.completed", {"turn_id": 9}),
        ])
        return route.fulfill(status=200, content_type="text/event-stream", body=body)

    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        page.route("**/api/chats/50/messages", send_route)
        page.fill("#prompt", "Привет")
        page.click("#send")
        page.wait_for_timeout(300)
        page.click("text=Продолжить")
        page.wait_for_timeout(2500)
        check(page.locator("#title").inner_text() == "Приветствие", "название чата пришло с сервера")
        check(page.locator("article.message.user").count() == 1, "ровно одно сообщение пользователя")
        check(page.locator("article.message.assistant").count() == 1, "ровно один ответ")
        check("отлично" in page.locator("article.message.assistant").inner_text(), "текст ответа")
        check(page.locator("article.message.assistant .message-actions").count() == 1, "кнопки готового ответа")
        check(page.locator("#send").get_attribute("data-icon") == "send", "кнопка снова отправляет")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_requests_queue_plan():
    api = development_api()
    api.extra["/api/agent/requests"] = lambda m, p, b, q: [{"id": 3, "kind": "approval", "chat_id": 1, "payload": {"name": "shell.run", "arguments": {"command": "ls"}}}]
    api.extra["/api/agent/requests/3/respond"] = lambda m, p, b, q: {"ok": True}
    api.extra["/api/chats/1/queue"] = lambda m, p, b, q: {"paused": False, "items": [{"id": 1, "version": 1, "status": "queued", "content": "Потом сделай тесты"}]}
    queued = {"paused": False, "items": [{"id": 1, "version": 1, "status": "queued", "content": "Потом сделай тесты"}]}
    api.extra["/api/chats/1/queue/1/promote"] = lambda m, p, b, q: queued
    api.extra["/api/chats/1/queue/resume"] = lambda m, p, b, q: queued
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        check(page.locator(".approval-panel").count() == 1, "запрос подтверждения")
        check(page.locator("#local-queue .queue-row").count() == 1, "очередь запросов")
        check("1 в очереди" in page.locator("#local-queue .queue-header").inner_text(), "заголовок очереди считает запросы")
        page.screenshot(path=str(Path(__file__).resolve().parents[1] / "_screens" / "queue-and-approval.png"))
        # «выполнить сейчас»: запрос встаёт первым и очередь продолжается
        page.click("#local-queue .queue-row button[aria-label='Выполнить сейчас']")
        page.wait_for_timeout(500)
        check(any(c[0] == "POST" and c[1] == "/api/chats/1/queue/1/promote" for c in api.calls) and any(c[0] == "POST" and c[1] == "/api/chats/1/queue/resume" for c in api.calls), "запрос из очереди не запущен сейчас")
        check(page.locator("#live-plan .plan-list li").count() == 2, "план в ленте")
        check(page.locator("#plan-approval").count() == 1, "документ плана")
        page.click(".approval-panel .primary")
        page.wait_for_timeout(500)
        check(page.locator(".approval-panel").count() == 0, "подтверждение закрыто")
        check(any(call[1] == "/api/agent/requests/3/respond" and call[2] == {"approved": True} for call in api.calls), "ответ отправлен")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_permission_modes():
    """Три режима подтверждений; «Разрешить всё» только через предупреждение; карточка разрешения без JSON."""
    api = development_api()
    action = {"title": "Выполнить команду", "target": "", "detail": "Remove-Item build -Recurse", "kind": "command", "outside_project": [], "dangerous": True}
    review = {"available": True, "allowed": False, "necessity": 7, "safety": 3, "reason": "Команда удаляет папку сборки, решите сами."}
    api.extra["/api/agent/requests"] = lambda m, p, b, q: [{"id": 4, "kind": "approval", "chat_id": 1, "payload": {"name": "shell.run", "arguments": {"command": "Remove-Item build -Recurse"}, "action": action, "review": review}}]
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        card = page.locator(".approval-panel")
        text = card.inner_text()
        check("Докки хочет выполнить команду" in text and "Remove-Item build -Recurse" not in text, f"краткая карточка действия: {text!r}")
        check("{" not in text and "Докки: Команда удаляет папку сборки" in text, f"в карточке JSON или нет вердикта: {text!r}")
        card.locator('.approval-details > summary').click()
        check('Remove-Item build -Recurse' in card.inner_text(), 'команда раскрывается')
        check(card.locator("pre.approval-command").count() == 1 and card.locator(".approval-note.warn").count() == 1, "команда и предупреждение")
        page.click("button[aria-label='Разрешения действий']")
        page.wait_for_selector("[role=option]:has-text('Разрешить всё')", timeout=3000)
        labels = [item.inner_text().strip() for item in page.locator("[role=option]").all()]
        check(labels == ["Вручную", "Автоматически", "Разрешить всё"], f"режимы: {labels}")
        page.click("[role=option]:has-text('Разрешить всё')")
        page.wait_for_selector("dialog.dialog[open]", timeout=3000)
        dialog = page.locator("dialog.dialog[open]")
        body = dialog.inner_text()
        check("любые действия без подтверждений" in body and "Рекомендуем вместо этого автоматический режим" in body, f"текст предупреждения: {body!r}")
        check(dialog.locator("button.btn-primary").inner_text().strip() == "Включить автоматический режим", "рекомендованная кнопка")
        page.wait_for_timeout(700)
        page.screenshot(path=str(Path(__file__).resolve().parents[1] / "_screens" / "permission-full-dialog.png"))
        # Enter выбирает безопасный рекомендованный вариант
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        puts = [call[2] for call in api.calls if call[0] == "PUT" and call[1] == "/api/settings" and isinstance(call[2], dict) and "permission_profile" in call[2]]
        check(puts and puts[-1]["permission_profile"] == "trusted" and "confirm_full_access" not in puts[-1], f"выбран автоматический: {puts}")
        page.click("button[aria-label='Разрешения действий']")
        page.click("[role=option]:has-text('Разрешить всё')")
        page.wait_for_selector("dialog.dialog[open]", timeout=3000)
        page.locator("dialog.dialog[open] .actions.choice button:has-text('Разрешить всё')").click()
        page.wait_for_timeout(500)
        puts = [call[2] for call in api.calls if call[0] == "PUT" and call[1] == "/api/settings" and isinstance(call[2], dict) and "permission_profile" in call[2]]
        check(puts[-1] == {"permission_profile": "full", "confirm_full_access": True, "project_id": 1}, f"полный доступ с подтверждением: {puts[-1]}")
        check(not page.errors, f"ошибки страницы: {page.errors}")

def scenario_inspector():
    api = development_api()
    # лента должна прокручиваться, иначе полосы нет
    api.extra["/api/chats/1/messages"] = lambda m, p, b, q: [{"id": i, "role": "user" if i % 2 else "assistant", "content": "Строка сообщения. " * 40, "metadata": {}} for i in range(1, 15)]
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        page.wait_for_timeout(700)
        check(page.locator("#toggle-right[aria-expanded=true]").count() == 1, "правая панель в чате разработки должна быть открыта по умолчанию")
        check(page.locator("#card-environment .environment-list").count() == 1, "карточка среды")
        # правая панель - часть чата: без разделителя и с тем же фоном
        look = page.evaluate("""(() => { const r = getComputedStyle(document.querySelector('#right')), p = getComputedStyle(document.querySelector('.inspector-panel')), m = getComputedStyle(document.querySelector('#main'));
          return {divider: p.borderLeftWidth, shadow: r.boxShadow, same: p.backgroundColor === m.backgroundColor}; })()""")
        check(look["divider"] == "0px" and look["shadow"] == "none" and look["same"], f"у правой панели есть разделитель или другой фон: {look}")
        # полоса прокрутки ленты остаётся у правого края окна, правее панели
        page.mouse.move(1268, 400)
        page.wait_for_timeout(400)
        thumb = page.evaluate("""(() => { const t = document.querySelector('.overlay-thumb.visible'); if (!t) return null; const b = t.getBoundingClientRect(); return {right: b.right, width: b.width, window: innerWidth}; })()""")
        check(thumb is not None and thumb["right"] > 1250 and thumb["width"] <= 6, f"полоса прокрутки: {thumb}")
        # ползунок остаётся на прежнем месте, а зона захвата уходит влево от зоны изменения размера окна (3 px у края)
        grab = page.evaluate("""(() => { const t = document.querySelector('.overlay-thumb.visible'); const b = t.getBoundingClientRect(); const y = b.top + b.height / 2;
          return {left: b.left, width: b.width, atEdge: document.elementFromPoint(innerWidth - 2, y) === t, atLeft: document.elementFromPoint(b.left - 6, y) === t}; })()""")
        check(grab["width"] <= 6 and grab["atLeft"] and not grab["atEdge"], f"зона захвата полосы прокрутки: {grab}")
        page.click("#inspector-diff .card-head")
        page.wait_for_timeout(700)
        check(page.locator(".change-file").count() == 1, "файл изменений")
        page.click(".change-file")
        page.wait_for_timeout(400)
        check(page.locator(".diff-line.added").count() == 2, "добавленные строки")
        page.click("#inspector-files .card-head")
        page.wait_for_timeout(700)
        check(page.locator(".file-row").count() == 2, "файлы проекта")
        page.click(".file-row >> nth=0")
        page.wait_for_timeout(700)
        check(page.locator(".file-editor").count() == 1, "редактор файла")
        page.click("#card-plan .card-head")
        page.wait_for_timeout(700)
        check(page.locator("#task-plan .plan-list li").count() == 2, "план в панели")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_settings_pages_library():
    api = development_api()
    api.extra["/api/tasks"] = lambda m, p, b, q: [{"id": 1, "title": "Исправить фризы", "mode": "development", "status": "completed", "chat_id": 1}]
    api.extra["/api/logs"] = lambda m, p, b, q: [{"level": "INFO", "event": "turn.completed", "created_at": "2026-10-17", "message": "ok", "data_json": "{}"}]
    api.extra["/api/skills"] = lambda m, p, b, q: [{"id": 1, "name": "Ревью", "description": "Проверка кода", "workflow": {"checklist": ["a"]}}]
    api.extra["/api/automations"] = lambda m, p, b, q: [{"id": 1, "name": "Диагностика", "status": "pending", "action": {"tool": "diagnostics.collect"}}]
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        page.click("#connection-badge .account-name")
        page.wait_for_timeout(700)
        check(page.locator("dialog.settings-window[open]").count() == 1, "окно настроек")
        check(page.locator(".pref-row").count() >= 6, "строки настроек")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        check(page.locator("dialog.settings-window").count() == 0, "окно настроек закрыто")
        for label, selector in [("Проекты", ".project-row"), ("Задачи", ".task-card"), ("Автоматизации", ".auto-card"), ("Проверка изменений", ".review-file")]:
            tile = page.locator(f'#left .side-tile[aria-label="{label}"]')
            if page.locator("#side-more").get_attribute("aria-expanded") != "true" and label in {"Автоматизации", "Проверка изменений"}:
                page.click("#side-more")
                page.wait_for_timeout(450)
            tile.click()
            page.wait_for_timeout(800)
            check(page.locator(selector).count() >= 1, f"страница «{label}»")
        check(page.locator("#title").inner_text() == "Изменения", "название страницы")
        # назад и вперёд
        page.click("#nav-back")
        page.wait_for_timeout(600)
        check(page.locator("#title").inner_text() == "Автоматизации", "назад")
        page.click("#nav-forward")
        page.wait_for_timeout(600)
        check(page.locator("#title").inner_text() == "Изменения", "вперёд")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def cloud_api():
    api = Api(scope="chat")
    text = "Атом — **компактная** машина.\n\n- плюс\n- минус"
    messages = [
        {"id": "msg_1", "role": "user", "content": "Расскажи про Атом", "created_at": "2026-10-17T10:00:00+00:00", "attachments": [], "artifacts": [], "activity": [], "citations": []},
        {"id": "msg_2", "role": "assistant", "content": text, "created_at": "2026-10-17T10:00:09+00:00", "status": "completed", "attachments": [],
         "artifacts": [{"file_id": "file_abc", "name": "report.md", "media_type": "text/markdown"}],
         "activity": [{"kind": "web_search", "text": "Ищу информацию"}, {"kind": "commentary", "text": "Нашёл три источника"}],
         "citations": [{"url": "https://example.com/atom", "title": "Обзор Атома", "snippet": "Подробный обзор", "spans": []}]},
    ]
    api.extra["/api/dokky/cloud/chats/chat_1/messages"] = lambda m, p, b, q: {"items": messages, "next_cursor": None}
    api.extra["/api/dokky/cloud/chats/chat_1"] = lambda m, p, b, q: {"id": "chat_1", "title": "Машины Атом", "mode": "chat", "model": "dokky-default", "version": 1}
    return api


def scenario_cloud():
    api = cloud_api()
    with browser_page(api) as page:
        page.wait_for_timeout(1300)
        check(page.locator(".welcome h1").inner_text().strip() != "", "приветствие облака")
        check(page.locator("#segmented-mode").is_visible(), "переключатель чат/поиск")
        page.click("text=Машины Атом")
        page.wait_for_timeout(1200)
        check(page.locator("article.message").count() == 2, "два сообщения")
        check(page.locator(".work-progress-toggle").count() == 1, "ход работы")
        page.click(".work-progress-toggle")
        page.wait_for_timeout(500)
        check(page.locator(".work-progress-entry").count() == 2, "этапы работы")
        check(page.locator(".cloud-sources .ai-source-card").count() == 1, "источники ответа")
        check(page.locator(".message-file-tile").count() == 1, "файл ответа")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_cloud_send():
    api = cloud_api()
    state = {"sent": False}
    answer = {"id": "msg_9", "role": "assistant", "content": "Готово: **ответ**", "created_at": "2026-10-17T12:00:09+00:00", "status": "completed", "attachments": [], "artifacts": [], "activity": [{"kind": "commentary", "text": "Думал"}], "citations": []}
    user = {"id": "msg_8", "role": "user", "content": "Новый вопрос", "created_at": "2026-10-17T12:00:00+00:00", "attachments": [], "artifacts": [], "activity": [], "citations": []}

    def chats(m, p, b, q):
        if m == "POST":
            return {"id": "chat_9", "title": "Новый вопрос", "mode": "chat", "model": "dokky-default", "version": 1}
        return {"items": api.cloud_chats, "next_cursor": None}

    api.extra["/api/dokky/cloud/chats"] = chats
    api.extra["/api/dokky/cloud/chats/chat_9/messages"] = lambda m, p, b, q: ({"run_id": "run_1", "message_id": "msg_8"} if m == "POST" else {"items": [user, answer] if state["sent"] else [], "next_cursor": None})
    api.extra["/api/dokky/cloud/chats/chat_9"] = lambda m, p, b, q: {"id": "chat_9", "title": "Новый вопрос", "mode": "chat", "model": "dokky-default", "version": 1, "active_run": None if state["sent"] else {"id": "run_1"}}

    def events(route):
        state["sent"] = True
        body = sse([
            ("activity.updated", {"run_id": "run_1", "activity": {"kind": "commentary", "text": "Думаю над ответом"}}),
            ("message.delta", {"run_id": "run_1", "message_id": "pending_run_1", "content": "Готово: **ответ**", "offset": 0}),
            ("turn.completed", {"run_id": "run_1", "message_id": "msg_9"}),
        ])
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    with browser_page(api) as page:
        page.wait_for_timeout(1300)
        page.route("**/api/dokky/cloud/runs/run_1/events*", events)
        page.fill("#prompt", "Новый вопрос")
        page.click("#send")
        page.wait_for_timeout(3000)
        check(page.locator("#title").inner_text() == "Новый вопрос", "название нового чата")
        check(page.locator("article.message.user").count() == 1, "одно сообщение пользователя")
        check(page.locator("article.message.assistant").count() == 1, "один ответ")
        check("ответ" in page.locator("article.message.assistant").inner_text(), "текст ответа")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_search():
    api = cloud_api()
    answer = {"id": "msg_s2", "role": "assistant", "content": "Ответ поиска", "created_at": "2026-10-17T12:00:09+00:00", "status": "completed", "attachments": [], "artifacts": [], "activity": [], "citations": [{"url": "https://example.com/a", "title": "Страница", "snippet": "Описание", "spans": []}]}

    def chats(m, p, b, q):
        if m == "POST":
            return {"id": "chat_s", "title": "поиск", "mode": "search", "model": "dokky-default", "version": 1}
        return {"items": api.cloud_chats, "next_cursor": None}

    api.extra["/api/dokky/cloud/chats"] = chats
    api.extra["/api/dokky/cloud/chats/chat_s/messages"] = lambda m, p, b, q: ({"run_id": "run_s", "message_id": "m"} if m == "POST" else {"items": [answer], "next_cursor": None})

    def events(route):
        body = sse([
            ("search.started", {}),
            ("search.source", {"source": {"url": "https://example.com/a", "title": "Страница", "snippet": "Описание"}}),
            ("message.delta", {"content": "Ответ поиска", "offset": 0}),
            ("turn.completed", {}),
        ])
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    with browser_page(api) as page:
        page.wait_for_timeout(1300)
        page.route("**/api/dokky/cloud/runs/run_s/events*", events)
        page.click("#product-search")
        page.wait_for_timeout(600)
        check(page.locator("#ai-search-surface").is_visible(), "поверхность поиска")
        page.fill("#ai-search-query", "что такое атом")
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
        check(page.locator(".ai-search-result").count() == 1, "результат поиска")
        check("Ответ поиска" in page.locator(".ai-search-answer").inner_text(), "ответ поиска")
        check(page.locator(".ai-search-followup").is_visible(), "кнопка продолжения")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_attachments_and_mode():
    api = development_api()
    api.extra["/api/attachments"] = lambda m, p, b, q: {"id": 77, "original_name": "note.txt", "media_type": "text/plain"}
    patched = []
    api.extra["/api/chats/1/coding-session"] = lambda m, p, b, q: (patched.append(b) or {"id": "s", "reasoning_mode": "light", "pending_reasoning_mode": b.get("reasoning_mode") if b else None, "version": 2, "history": [1], "last_usage": None}) if m == "PATCH" else {"id": "s", "reasoning_mode": "light", "version": 1, "history": [1], "last_usage": None}
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        page.set_input_files("#files", {"name": "note.txt", "mimeType": "text/plain", "buffer": b"hello"})
        page.wait_for_timeout(800)
        check(page.locator(".attachment-card").count() == 1, "плитка вложения")
        check(page.locator("#attachments.has-files").count() == 1, "полоса вложений раскрыта")
        page.click(".attachment-card button")
        page.wait_for_timeout(500)
        check(page.locator(".attachment-card").count() == 0, "вложение убрано")
        # смена режима только запоминается и подтверждается диалогом
        page.click("button[aria-label='Режим разработки']")
        page.click("[role=option]:has-text('Pro')")
        page.wait_for_timeout(400)
        check(page.locator("dialog.dialog[open]").count() == 1, "подтверждение смены режима")
        page.click("dialog.dialog button:has-text('Сменить режим')")
        page.wait_for_timeout(600)
        check(patched and patched[0].get("reasoning_mode") == "pro", "режим отправлен на сервер")
        check(not any(call[0] == "POST" and "compact" in call[1] for call in api.calls), "сжатия контекста нет")
        check(not page.errors, f"ошибки страницы: {page.errors}")


STREAM_SCRIPT = """
(() => {
  const original = window.fetch;
  const text = Array.from({length: 40}, (_, i) => `Абзац номер ${i + 1}: **важная** мысль про таймер и кадры, которая занимает не одну строку и заставляет ленту прокручиваться.`).join('\\n\\n');
  window.__text = text;
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url.includes('/api/chats/50/messages') && (!init || !init.method || init.method === 'GET')) return original(url + (url.includes('?') ? '&' : '?') + 'done=' + (window.__streamDone ? 1 : 0), init);
    if (!url.includes('/api/chats/50/messages') || (init && init.method) !== 'POST') return original(input, init);
    const encoder = new TextEncoder();
    const send = (controller, kind, data, id) => controller.enqueue(encoder.encode(`id: ${id}\\nevent: ${kind}\\ndata: ${JSON.stringify(data)}\\n\\n`));
    const stream = new ReadableStream({
      async start(controller) {
        send(controller, 'turn.started', {turn_id: 9}, 1);
        let offset = 0, id = 2;
        while (offset < text.length) {
          const chunk = text.slice(offset, offset + 60);
          send(controller, 'message.delta', {message_id: 22, content: chunk, offset}, id++);
          offset += chunk.length;
          await new Promise((resolve) => setTimeout(resolve, 25));
        }
        window.__streamDone = true;
        send(controller, 'turn.completed', {turn_id: 9}, id);
        controller.close();
      },
    });
    return Promise.resolve(new Response(stream, {status: 200, headers: {'content-type': 'text/event-stream'}}));
  };
})();
"""


def scenario_live_stream():
    api = development_api()
    state = {"done": False}
    user = {"id": 21, "role": "user", "content": "Расскажи", "metadata": {"turn_id": 9}, "created_at": "2026-10-17T11:00:00+00:00"}
    api.extra["/api/chats"] = lambda m, p, b, q: ({"id": 50, "title": "Расскажи", "mode": "chat", "model": "dokky-code", "project_id": None, "updated_at": "2026-10-17T11:00:00+00:00"} if m == "POST" else api.local_chats)
    api.extra["/api/chats/50/messages"] = lambda m, p, b, q: [user, {"id": 22, "role": "assistant", "content": "x", "metadata": {"channel": "final", "status": "completed", "turn_id": 9, "sequence": 5, "turn": {**TURN, "id": 9}}, "created_at": "2026-10-17T11:00:19+00:00"}] if q.get("done") == ["1"] else [user]
    api.extra["/api/chats/50/turn"] = lambda m, p, b, q: {"active": not state["done"]}
    api.extra["/api/chats/50/plan"] = lambda m, p, b, q: {"steps": [], "document": "", "turn_id": None}
    api.extra["/api/chats/50/changes"] = lambda m, p, b, q: []
    with browser_page(api, init_script=STREAM_SCRIPT) as page:
        page.wait_for_timeout(1200)
        page.fill("#prompt", "Расскажи")
        page.click("#send")
        page.wait_for_timeout(300)
        page.click("text=Продолжить")
        page.wait_for_timeout(900)
        check(page.locator("article.message.assistant.streaming").count() == 1, "идёт потоковый ответ")
        page.evaluate("document.querySelector('article.message.assistant .message-body').__mark = 'same'")
        check(page.locator("#send").get_attribute("data-icon") in {"send", "stop"}, "кнопка отправки")
        page.wait_for_timeout(600)
        # как на сайте: когда пошёл текст ответа, его начало встаёт у верхнего края и лента не уезжает вниз за ним
        start = page.evaluate("(() => { const f = document.querySelector('#feed'); return document.querySelector('article.message.assistant').getBoundingClientRect().top - f.getBoundingClientRect().top; })()")
        check(0 <= start < 120, f"начало ответа не у верхнего края ленты: {start}px")
        check(page.locator(".stream-word").count() > 5, "слова появляются по одному")
        # читатель поднялся вверх: лента больше не тянет вниз, появляется кнопка
        page.mouse.move(700, 400)
        page.mouse.wheel(0, -700)
        page.wait_for_timeout(300)
        top = page.evaluate("document.querySelector('#feed').scrollTop")
        page.wait_for_timeout(500)
        check(abs(page.evaluate("document.querySelector('#feed').scrollTop") - top) < 40, "лента не уезжает от читателя")
        # как на сайте: кнопка показывается, когда до конца больше трёх экранов
        far = page.evaluate("(() => { const f = document.querySelector('#feed'); return f.scrollHeight - f.scrollTop - f.clientHeight >= f.clientHeight * 3; })()")
        check((page.locator("#jump-bottom:not([hidden])").count() == 1) == far, "кнопка «К последнему сообщению» показывается не по правилу сайта")
        page.wait_for_function("window.__streamDone === true", timeout=20000)
        page.wait_for_timeout(4500)
        check(page.locator("article.message.assistant").count() == 1, "ровно один ответ")
        check(page.evaluate("document.querySelector('article.message.assistant .message-body').__mark") == "same", "узел ответа не пересоздавался")
        check(page.locator("article.message.assistant .message-actions").count() == 1, "кнопки готового ответа")
        check(page.locator("article.message.assistant.streaming").count() == 0, "поток завершён")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_shell_stability():
    """Панели не сжимают и не растягивают содержимое, список чатов не дрожит, кольцо фокуса не остаётся после мыши."""
    api = development_api()
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        row = ".chat-row[data-selected]"
        idle = page.evaluate("(s) => { const r = document.querySelector(s); return [r.querySelector('.chat-item').getBoundingClientRect().width, getComputedStyle(r.querySelector('.chat-time')).opacity]; }", row)
        page.hover(".chat-row:not([data-selected])")
        page.wait_for_timeout(300)
        hovered = page.evaluate("(s) => { const r = document.querySelector(s); return [r.querySelector('.chat-item').getBoundingClientRect().width, getComputedStyle(r.querySelector('.chat-time')).opacity]; }", row)
        check(idle[0] == hovered[0], f"заголовок чата меняет ширину при наведении: {idle} -> {hovered}")
        widths = []
        page.click("#toggle-left")
        for _ in range(6):
            widths.append(page.evaluate("document.querySelector('.sidebar-panel').getBoundingClientRect().width"))
            page.wait_for_timeout(70)
        check(len({round(value) for value in widths}) == 1, f"содержимое сайдбара сжимается при закрытии: {widths}")
        check(page.evaluate("getComputedStyle(document.activeElement).outlineStyle") in {"none", ""} or page.evaluate("document.documentElement.dataset.input") == "keyboard", "кольцо фокуса после клика мышью")
        page.wait_for_timeout(500)
        page.click("#toggle-left")
        page.wait_for_timeout(500)
        right = []
        page.click("#toggle-right")
        for _ in range(6):
            right.append(page.evaluate("document.querySelector('.inspector-panel').getBoundingClientRect().width"))
            page.wait_for_timeout(70)
        check(len({round(value) for value in right}) == 1, f"содержимое правой панели сжимается: {right}")
        check(page.evaluate("document.querySelector('.side-tile[aria-current]')?.getAttribute('aria-label') ?? ''") != "Новый чат", "«Новый чат» не залипает")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_audit_items():
    """Пункты аудита: выделение текста, размер текста, заголовок на линии кнопок, запоминание проектов и порядка, плавность «Code»."""
    api = development_api()
    with browser_page(api) as page:
        page.wait_for_timeout(1200)

        def select(sel):
            return page.evaluate("(s) => getComputedStyle(document.querySelector(s)).userSelect", sel)

        check(select(".side-tile") == "none" and select("#left .brand-word") == "none", "интерфейс выделяется")
        check(select("#prompt") == "text", "поле ввода должно выделяться")
        # бренд и заголовок
        brand = page.evaluate("[parseFloat(getComputedStyle(document.querySelector('.brand')).fontSize), getComputedStyle(document.querySelector('.brand')).fontFamily]")
        check(brand[0] == 17 and "Unbounded" in brand[1], f"бренд: 17 px и шрифт Unbounded: {brand}")
        check(page.evaluate("document.querySelector('#scope-toggle').getAttribute('aria-pressed')") == "true", "«Code» должен быть включён в разработке")
        # проекты и порядок запоминаются между запусками
        page.click("text=zaprethub.new")
        page.wait_for_timeout(500)
        page.click("text=Фризы при возврате из фона")
        page.wait_for_timeout(900)
        title = page.evaluate("(() => { const t = document.querySelector('#title').getBoundingClientRect(), b = document.querySelector('#toggle-left').getBoundingClientRect(); return [t.top + t.height / 2, b.top + b.height / 2]; })()")
        check(abs(title[0] - title[1]) <= 3, f"заголовок не на линии кнопок: {title}")
        page.reload()
        page.wait_for_timeout(1500)
        check(page.locator(".project-group[data-open] .chat-row").count() >= 1, "раскрытый проект не запомнился")
        # размер текста действует на всё и считается от базового
        api.settings["text_size"] = 20
        page.reload()
        page.wait_for_timeout(1500)
        sizes = page.evaluate("[parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--base')), parseFloat(getComputedStyle(document.querySelector('.chat-item')).fontSize), parseFloat(getComputedStyle(document.querySelector('.section-title')).fontSize)]")
        check(sizes[0] == 20 and sizes[1] == 21 and sizes[2] == 20, f"размер текста не применился ко всему: {sizes}")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_sidebar_search_reorder():
    """Поиск по чатам выезжает из строки, чаты перетаскиваются, порядок помнится, «Ещё» ничего не открывает."""
    api = development_api()
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        page.click("text=zaprethub.new")
        page.wait_for_timeout(500)
        page.click("#chat-search")
        page.fill("#chat-search-input", "приложения")
        page.wait_for_timeout(400)
        check(page.locator(".chat-row").count() == 1 and page.locator("#chat-search-row[data-open]").count() == 1, "поиск по чатам не фильтрует список на месте")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        check(page.locator(".chat-row").count() >= 3 and page.locator("#chat-search-row[data-open]").count() == 0, "поиск не закрылся по Escape")
        before = page.locator(".project-group[data-open] .chat-row .chat-item").all_inner_texts()
        page.drag_and_drop(".project-group[data-open] .chat-row >> nth=1", ".project-group[data-open] .chat-row >> nth=0", target_position={"x": 40, "y": 3})
        page.wait_for_timeout(500)
        after = page.locator(".project-group[data-open] .chat-row .chat-item").all_inner_texts()
        check(after == before[::-1], f"порядок не изменился: {before} -> {after}")
        page.reload()
        page.wait_for_timeout(1500)
        check(page.locator(".project-group[data-open] .chat-row .chat-item").all_inner_texts() == after, "порядок чатов не запомнился")
        # «Ещё» только раскрывает список и не открывает страниц
        page.click("#side-more")
        page.wait_for_timeout(400)
        check(page.locator("#title").inner_text() == "" and page.locator(".side-tile[aria-current]").count() == 0, "«Ещё» что-то открыло или подсветило")
        check(not page.errors, f"ошибки страницы: {page.errors}")

def scenario_pin_and_expand():
    """Закрепление чата Докки и развёрнутое поле ввода (как на сайте)."""
    api = cloud_api()
    patched = []

    def chats(m, p, b, q):
        items = [{**chat, "pinned": chat["id"] in patched} for chat in api.cloud_chats]
        return {"items": items, "next_cursor": None}

    def chat_two(m, p, b, q):
        if m == "PATCH":
            if b.get("pinned"):
                patched.append("chat_2")
            else:
                patched.clear()
            return {"id": "chat_2", "title": "Рецепт борща", "mode": "chat", "version": 2, "pinned": bool(b.get("pinned"))}
        return {"id": "chat_2", "title": "Рецепт борща", "mode": "chat", "version": 1, "pinned": "chat_2" in patched}

    api.extra["/api/dokky/cloud/chats"] = chats
    api.extra["/api/dokky/cloud/chats/chat_2"] = chat_two
    with browser_page(api) as page:
        page.wait_for_timeout(1300)
        rows = page.locator(".chat-row .chat-item")
        check(rows.all_inner_texts() == ["Машины Атом", "Рецепт борща"], "исходный порядок")
        page.hover(".chat-row >> nth=1")
        page.click(".chat-row >> nth=1 >> .chat-more")
        page.click("[role=menuitem]:has-text('Закрепить')")
        page.wait_for_timeout(900)
        check(rows.all_inner_texts()[0] == "Рецепт борща" and page.locator(".chat-pin").count() == 1, "закреплённый чат не поднялся наверх")
        # развёрнутое поле ввода
        page.fill("#prompt", "\n".join(f"строка {i}" for i in range(9)))
        page.wait_for_timeout(300)
        check(page.locator("#expand-composer").count() == 1, "кнопка «Развернуть поле ввода»")
        page.click("#expand-composer")
        page.wait_for_timeout(500)
        check(page.locator(".expanded-editor.open").count() == 1, "развёрнутое поле не открылось")
        page.fill(".expanded-editor textarea", "новый длинный текст")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        check(page.locator(".expanded-editor.open").count() == 0 and page.input_value("#prompt") == "новый длинный текст", "текст не вернулся в поле ввода")
        check(not page.errors, f"ошибки страницы: {page.errors}")

def scenario_shell_rules():
    """Правая панель только в разработке и только с контекстом, переключатель чата и поиска только в новом чате, затухание у краёв, шапка на одной линии."""
    api = development_api()
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        check(page.locator("#toggle-right").is_hidden(), "кнопка правой панели видна без чата и проекта")
        check(page.evaluate("document.getElementById('shell').classList.contains('right-collapsed')"), "правая панель открыта без контекста")
        open_chat(page)
        check(page.locator("#toggle-right").is_visible(), "в чате разработки нет кнопки правой панели")
        check(not page.evaluate("document.getElementById('shell').classList.contains('right-collapsed')"), "правая панель не открылась по умолчанию в чате")
        # колонки сетки меняются сразу, а плавность идёт через transform: пересчёта чата на каждом кадре нет
        page.click("#toggle-left")
        columns = page.evaluate("getComputedStyle(document.getElementById('shell')).gridTemplateColumns.split(' ')[0]")
        check(columns == "0px", f"левая колонка закрывается не сразу: {columns}")
        page.wait_for_timeout(600)
        page.click("#toggle-left")
        page.wait_for_timeout(600)
        # шапка: название и кнопки на одной линии, у верха ленты затухания нет
        line = page.evaluate("(() => { const t = document.querySelector('#title').getBoundingClientRect(), b = document.querySelector('#toggle-left').getBoundingClientRect(), s = document.querySelector('.brand-word').getBoundingClientRect(); return [t.top + t.height / 2, b.top + b.height / 2, s.top + s.height / 2]; })()")
        check(abs(line[0] - line[1]) <= 2 and abs(line[2] - line[1]) <= 3, f"шапка не на одной линии: {line}")
        fade = page.evaluate("(() => { const f = document.querySelector('.chat-edge-fade-bottom'); const c = document.getElementById('composer'); return [getComputedStyle(f).opacity, f.getBoundingClientRect().height, c.getBoundingClientRect().height]; })()")
        check(fade[0] == "1" and fade[1] >= fade[2] - 1, f"затемнение под полем ввода пропало у конца ленты: {fade}")
        check(page.evaluate("getComputedStyle(document.querySelector('.chat-edge-fade-top')).opacity") == "0" or page.evaluate("!document.getElementById('main').hasAttribute('data-at-top')"), "затухание сверху не выключается у начала ленты")
    cloud = cloud_api()
    with browser_page(cloud) as page:
        page.wait_for_timeout(1200)
        check(page.locator("#toggle-right").is_hidden(), "правая панель в обычном чате")
        check(page.evaluate("document.getElementById('product-chat') && !document.querySelector('.ai-product-switch').hasAttribute('data-away')"), "в новом чате нет переключателя чата и поиска")
        page.click("text=Машины Атом")
        page.wait_for_timeout(900)
        check(page.evaluate("document.querySelector('.ai-product-switch').hasAttribute('data-away')"), "в начатом чате остался переключатель чата и поиска")


def scenario_motion_rules():
    """Плавность: закрытие панелей, раскрытие журнала, короткая строка состояния, чат открывается на конце, время в UTC."""
    api = development_api()
    ev = [{"event": "tool.call", "data": {"id": "a1", "name": "search.text", "arguments": {"query": "QTimer|singleShot|setInterval|timeout.connect|threading.Thread|QThread"}}, "sequence": 3, "created_at": "2026-10-17T10:00:10+00:00"}]
    running = [
        {"id": 1, "role": "user", "content": "Найди баги", "metadata": {"turn_id": 7}, "created_at": "2026-10-17T10:00:00+00:00"},
        {"id": 4, "role": "assistant", "content": "", "metadata": {"kind": "activity", "channel": "commentary", "status": "running", "sequence": 3, "turn_id": 7, "events": ev}, "created_at": "2026-10-17T10:00:08+00:00"},
    ]
    api.local_chats[0]["updated_at"] = "2026-10-17 10:00:00"
    api.extra["/api/chats/2/messages"] = lambda m, p, b, q: running
    api.extra["/api/chats/2/turn"] = lambda m, p, b, q: {"active": True}
    with browser_page(api) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        sample = """async (sel) => { const out = []; let stop = false; const loop = () => { out.push(document.querySelector(sel).getBoundingClientRect().left); if (!stop) requestAnimationFrame(loop); }; requestAnimationFrame(loop); document.querySelector('#toggle-right').click(); await new Promise(r => setTimeout(r, 600)); stop = true; return out; }"""
        lefts = page.evaluate(sample, ".inspector-panel")
        # считаем промежуточные положения, а не шаг между кадрами: на медленной машине браузер пропускает кадры
        low, high = min(lefts), max(lefts)
        between = {round(x) for x in lefts if low + 10 < x < high - 10}
        check(high - low > 200 and len(between) >= 2, f"правая панель закрывается рывком: {lefts[:12]}")
        page.click("#toggle-right")
        page.wait_for_timeout(600)
        check(page.evaluate("(() => { const f = document.querySelector('#feed'); return f.scrollHeight - f.scrollTop - f.clientHeight <= 2; })()"), "чат открылся не на конце переписки")
        # журнал: высота растёт плавно, без паузы и скачка
        page.click("text=Работал")
        heights = []
        for _ in range(10):
            heights.append(page.evaluate("document.querySelector('.journal-completed').getBoundingClientRect().height"))
            page.wait_for_timeout(25)
        check(len({round(h) for h in heights}) > 3 and heights[-1] > heights[0], f"журнал раскрывается не плавно: {heights}")
        page.wait_for_timeout(500)
        # время чата читается как UTC
        page.click("text=Фризы приложения")
        page.wait_for_timeout(900)
    api2 = development_api()
    api2.extra["/api/chats/1/messages"] = lambda m, p, b, q: running
    api2.extra["/api/chats/1/turn"] = lambda m, p, b, q: {"active": True}
    with browser_page(api2) as page:
        page.wait_for_timeout(1200)
        open_chat(page)
        label = page.evaluate("document.querySelector('.journal-segment > summary .journal-summary-copy')?.textContent ?? ''")
        check(label == "Ищу в файлах проекта", f"строка состояния длинная: {label!r}")
        box = page.evaluate("(() => { const s = document.querySelector('.journal-segment > summary'); const c = s.querySelector('.journal-summary-copy'); return [c.scrollWidth <= c.clientWidth + 1, getComputedStyle(c).whiteSpace]; })()")
        check(box == [True, "nowrap"], f"строка состояния переносится: {box}")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_long_chat_list():
    """Длинный список чатов прокручивается внутри меню: поле ввода и аккаунт остаются в окне."""
    api = cloud_api()
    api.cloud_chats = [{"id": f"chat_{i}", "title": f"Чат номер {i} с достаточно длинным названием", "mode": "chat", "model": "dokky-default", "version": 1, "updated_at": f"2026-10-{16 - i // 6:02d}T09:00:00+00:00"} for i in range(1, 41)]
    with browser_page(api, viewport=(1200, 800)) as page:
        page.wait_for_timeout(1500)
        box = page.evaluate("(() => { const r = (s) => document.querySelector(s).getBoundingClientRect(); return [Math.round(r('#shell').bottom), Math.round(r('#composer').bottom), Math.round(r('.side-bottom').bottom), Math.round(r('.sidebar-scroll').bottom)]; })()")
        check(box[0] == 800 and box[1] <= 800 and box[2] <= 800 and box[3] <= 800, f"список чатов раздвинул страницу: {box}")
        page.evaluate("document.querySelector('.sidebar-scroll').scrollTop = 400")
        page.wait_for_timeout(400)
        check(page.evaluate("document.querySelector('.side-top').classList.contains('is-scrolled')"), "затемнение под шапкой не появилось при прокрутке")
        check(page.locator("#new-chat").is_visible() and page.locator("#connection-badge").is_visible(), "нижний блок меню не виден")
        # «Code» переключает режим сразу, без анимации меню
        page.click("#scope-toggle")
        page.wait_for_timeout(200)
        check(page.evaluate("document.querySelector('#scope-toggle').getAttribute('aria-pressed')") == "true", "«Code» не включил разработку")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_tiles_library_media_auth():
    """Плитки режима и «Ещё», библиотека-страница, медиа и блоки формата app/1, вход без «Вход» с регистрацией, полоса списка чатов."""
    cloud = cloud_api()
    media = [{"id": "photo_1", "kind": "image", "file_id": "file_abc", "url": "/api/v1/files/file_abc/content", "alt": "Салон", "media_type": "image/png"},
             {"id": "prod_1", "kind": "image", "file_id": "file_prod", "url": "/api/v1/files/file_prod/content", "alt": "Товар"}]
    blocks = [{"type": "product_carousel", "title": "Выбор", "items": [{"title": "Товар один", "price": "100 ₽", "url": "https://example.org/p", "image_media_id": "prod_1"}]}]
    reply = {"id": "msg_9", "role": "assistant", "content": "Вот фото:\n\n![Салон](dokky:media/photo_1)", "content_format": "dokky-md/1", "media": media, "blocks": blocks,
             "created_at": "2026-10-17T10:00:09+00:00", "status": "completed", "attachments": [], "artifacts": [], "activity": [], "citations": []}
    ask = {"id": "msg_8", "role": "user", "content": "найди фотки", "created_at": "2026-10-17T10:00:00+00:00", "attachments": [], "artifacts": [], "activity": [], "citations": []}
    cloud.extra["/api/dokky/cloud/chats/chat_1/messages"] = lambda m, p, b, q: {"items": [ask, reply], "next_cursor": None}
    cloud.extra["/api/dokky/cloud/library"] = lambda m, p, b, q: {"items": [
        {"id": "file_abc", "name": "Салон.png", "kind": "image", "viewer_url": "/api/dokky/cloud/files/file_abc/content", "chat": "Машины", "created_at": "2026-10-16 09:00:00"},
        {"id": "file_doc", "name": "Отчёт.pptx", "kind": "file", "chat": "История", "created_at": "2026-10-15 09:00:00"}], "next_cursor": None, "retention_days": 30}
    cloud.cloud_chats = [{"id": f"chat_{i}", "title": f"Чат номер {i} с длинным названием", "mode": "chat", "model": "dokky-default", "version": 1, "updated_at": f"2026-10-{16 - i // 6:02d}T09:00:00+00:00"} for i in range(1, 41)]
    cloud.cloud_chats[0]["id"] = "chat_1"
    pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    with browser_page(cloud, viewport=(1200, 800)) as page:
        page.route("**/files/file_*/content", lambda route: route.fulfill(status=200, content_type="image/png", body=pixel))
        page.wait_for_timeout(1500)
        check(page.locator("#left .side-tiles > .side-tile").count() == 3 and page.locator("#side-more").count() == 0, "в режиме чата три плитки и нет «Ещё»")
        check(page.locator(".mode-badge").inner_text() == "Чат", "подпись режима в чате")
        # в списке чатов полосы нет, но он прокручивается
        before = page.evaluate("document.querySelector('.sidebar-scroll').scrollTop")
        page.mouse.move(150, 400)
        page.mouse.wheel(0, 240)
        page.wait_for_timeout(500)
        check(page.evaluate("document.querySelector('.sidebar-scroll').scrollTop") > before, "список чатов не прокручивается")
        check(page.evaluate("document.querySelectorAll('.overlay-thumb.visible').length") == 0 and page.evaluate("getComputedStyle(document.querySelector('.sidebar-scroll')).scrollbarWidth") == "none", "у списка чатов не должно быть полосы прокрутки")
        # библиотека - страница на месте чата
        page.click("#cloud-library-button")
        page.wait_for_timeout(900)
        check(page.locator(".library-page .lib-card").count() == 2 and page.locator("#composer").is_hidden(), "библиотека не стала страницей")
        check(page.locator(".library-page .lib-preview.is-file").count() == 1 and "30 дн." in page.locator(".library-page .muted").inner_text(), "превью и срок хранения в библиотеке")
        # медиа и блоки app/1 в чате
        page.evaluate("document.querySelector('.sidebar-scroll').scrollTop = 0")
        page.locator(".chat-item", has_text="Чат номер 1 с").first.click()
        page.wait_for_timeout(1200)
        src = page.evaluate("document.querySelector('.message-body img')?.getAttribute('src') ?? document.querySelector('.turn-message.assistant')?.innerHTML.slice(0, 700) ?? 'нет сообщения'")
        check("files/file_abc/content" in src and "dokky:media" not in src, f"картинка app/1 не подставилась: {src!r}")
        check(page.locator(".product-card").count() == 1 and "Товар один" in page.locator(".product-card").inner_text(), "карусель товаров не показана")
        # плитка режима переключает разработку, «Ещё» есть только когда плитки не помещаются
        page.click("#scope-toggle")
        page.wait_for_timeout(400)
        check(page.locator(".mode-badge").inner_text() == "Code" and page.locator("#side-more").count() == 1, "в разработке подпись Code и «Ещё»")
        check(not page.errors, f"ошибки страницы: {page.errors}")
    guest = cloud_api()
    guest.extra["/api/dokky/session"] = lambda m, p, b, q: {"authenticated": False, "internal_test": False}
    with browser_page(guest, viewport=(900, 700)) as page:
        page.wait_for_timeout(1200)
        check(page.locator("#auth-screen h2").count() == 0 and page.locator("#auth-register").inner_text() == "Регистрация", "экран входа: без «Вход», с регистрацией")


def scenario_par_login():
    """Вход через серверные попытки: короткая ссылка, «Отмена», «Время на вход вышло» и «Войти снова», ошибка PAR без браузера."""
    api = cloud_api()
    state = {"status": "pending", "message": "", "authed": False, "n": 0}

    def start(method, path, body, query):
        state["n"] += 1
        return {"authorize_url": f"https://dokky.goshkow.com/oauth/authorize?client_id=dokky-app&request_uri=urn%3Aattempt%3A{state['n']}", "expires_in": 600}

    api.extra["/api/dokky/session"] = lambda m, p, b, q: api.session() if state["authed"] else {"authenticated": False, "internal_test": False}
    api.extra["/api/dokky/auth/start"] = start
    api.extra["/api/dokky/auth/status"] = lambda m, p, b, q: {"status": state["status"], "message": state["message"], "pending": state["status"] == "pending"}
    api.extra["/api/dokky/auth/cancel"] = lambda m, p, b, q: {"ok": True}
    with browser_page(api, viewport=(900, 700)) as page:
        page.wait_for_timeout(1200)
        page.evaluate("(() => { window.__opened = []; window.open = (url) => { window.__opened.push(url); return null; }; })()")
        page.click("#auth-login")
        page.wait_for_timeout(300)
        opened = page.evaluate("window.__opened")
        check(len(opened) == 1 and "client_id=dokky-app" in opened[0] and "request_uri=urn" in opened[0], f"ссылка входа: {opened}")
        check("state" not in opened[0] and "code_challenge" not in opened[0], "в ссылке нет state и code_challenge")
        check(page.locator("#auth-cancel").count() == 1 and page.locator("#auth-login").is_disabled(), "во время ожидания есть «Отмена»")
        # отмена закрывает слушатель и возвращает кнопку входа
        page.click("#auth-cancel")
        page.wait_for_timeout(300)
        check(any(path == "/api/dokky/auth/cancel" for _, path, _ in api.calls), "«Отмена» не закрыла попытку на сервере")
        check(page.locator("#auth-cancel").count() == 0 and page.locator("#auth-login").is_enabled(), "после отмены кнопка входа свободна")
        # время вышло: сообщение и «Войти снова»
        state["status"] = "expired"
        state["message"] = "Время на вход вышло"
        page.click("#auth-login")
        page.wait_for_timeout(1800)
        check(page.locator("#auth-status").inner_text() == "Время на вход вышло", "нет сообщения «Время на вход вышло»")
        check(page.locator("#auth-login").inner_text() == "Войти снова" and page.locator("#auth-login").is_enabled(), "нет кнопки «Войти снова»")
        # сгоревший код
        state["status"] = "invalid"
        state["message"] = "Попытка входа закончилась, войдите снова"
        page.click("#auth-login")
        page.wait_for_timeout(1800)
        check("войдите снова" in page.locator("#auth-status").inner_text(), "нет сообщения о закончившейся попытке")
        # новая попытка - новая ссылка, успех пускает в приложение
        state["status"] = "done"
        state["authed"] = True
        page.click("#auth-login")
        page.wait_for_selector("#composer", timeout=8000)
        check(state["n"] == 4 and len(page.evaluate("window.__opened")) == 4, "каждая попытка входа должна получать новую ссылку")
        check(not page.errors, f"ошибки страницы: {page.errors}")
    refused = cloud_api()
    refused.extra["/api/dokky/session"] = lambda m, p, b, q: {"authenticated": False, "internal_test": False}
    with browser_page(refused, viewport=(900, 700)) as page:
        page.wait_for_timeout(1200)
        page.evaluate("(() => { window.__opened = []; window.open = (url) => { window.__opened.push(url); return null; }; })()")
        page.route("**/api/dokky/auth/start", lambda route: route.fulfill(status=429, content_type="application/json", body=json.dumps({"detail": {"code": "rate_limited", "message": "Слишком много попыток входа. Подождите минуту и повторите"}})))
        page.click("#auth-login")
        page.wait_for_timeout(500)
        check(page.evaluate("window.__opened.length") == 0, "при ошибке PAR браузер не должен открываться")
        check("Слишком много попыток" in page.locator("#auth-status").inner_text() and page.locator("#auth-login").is_enabled(), "ошибка PAR не показана")


def scenario_context_menu_rail_title():
    """Своё контекстное меню вместо родного, рейка сообщений не сдвигает карточку при клике, название чата не режется."""
    api = cloud_api()
    items = []
    for i in range(8):
        items.append({"id": f"msg_u{i}", "role": "user", "content": f"Вопрос номер {i} про длинную тему", "created_at": "2026-10-17T10:00:00+00:00", "attachments": [], "artifacts": [], "activity": [], "citations": []})
        items.append({"id": f"msg_a{i}", "role": "assistant", "content": ("Ответ номер %d. " % i) + "Абзац текста для высоты. " * 60, "created_at": "2026-10-17T10:00:09+00:00", "status": "completed", "attachments": [], "artifacts": [], "activity": [], "citations": []})
    api.extra["/api/dokky/cloud/chats/chat_1/messages"] = lambda m, p, b, q: {"items": items, "next_cursor": None}
    api.extra["/api/dokky/cloud/chats/chat_1"] = lambda m, p, b, q: {"id": "chat_1", "title": "сколько стоит плащ майнкрафт moonlight trial", "mode": "chat", "model": "dokky-default", "version": 1}
    api.cloud_chats[0]["title"] = "сколько стоит плащ майнкрафт moonlight trial"
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        page.evaluate("(() => { window.__prevented = []; window.addEventListener('contextmenu', (e) => window.__prevented.push(e.defaultPrevented)); })()")
        # меню чата в списке остаётся своим, наше не появляется
        page.click(".chat-item >> nth=0", button="right")
        page.wait_for_timeout(300)
        check(page.locator(".context-menu").count() == 0 and page.locator(".popover").count() == 1, "у чата должно остаться своё меню")
        page.keyboard.press("Escape")
        page.click(".chat-item >> nth=0")
        page.wait_for_selector("article.message", timeout=8000)
        page.wait_for_timeout(900)
        # название чата целиком: хвосты букв не режутся
        title = page.evaluate("(() => { const t = document.getElementById('title'); const s = getComputedStyle(t); return {clipped: t.scrollHeight > t.clientHeight + 1, ratio: parseFloat(s.lineHeight) / parseFloat(s.fontSize)}; })()")
        check(not title["clipped"] and title["ratio"] >= 1.3, f"название чата режется снизу: {title}")
        # пустое место: родное меню подавлено, своего нет
        page.click("#feed", button="right", position={"x": 5, "y": 300})
        page.wait_for_timeout(250)
        check(page.evaluate("window.__prevented.at(-1)") is True and page.locator(".context-menu").count() == 0, "на пустом месте не должно быть меню")
        # выделенный текст: копировать
        page.evaluate("(() => { const p = document.querySelector('article.message.assistant .message-body p'); const r = document.createRange(); r.selectNodeContents(p); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()")
        page.click("article.message.assistant .message-body p", button="right")
        page.wait_for_selector(".context-menu .menu-item", timeout=3000)
        names = page.locator(".context-menu .menu-item").all_inner_texts()
        check(len(names) == 1 and names[0].startswith("Копировать"), f"меню выделенного текста: {names}")
        check("Save page" not in page.inner_text("body") and "View page source" not in page.inner_text("body"), "родное меню браузера не должно появляться")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        # поле ввода: вырезать, копировать, вставить, выделить всё
        page.fill("#prompt", "привет мир")
        page.click("#prompt", button="right")
        page.wait_for_selector(".context-menu .menu-item", timeout=3000)
        names = [name.split("\n")[0] for name in page.locator(".context-menu .menu-item").all_inner_texts()]
        check(names == ["Вырезать", "Копировать", "Вставить", "Выделить всё"], f"меню поля ввода: {names}")
        page.click(".context-menu .menu-item:has-text('Выделить всё')")
        page.wait_for_timeout(200)
        check(page.evaluate("(() => { const p = document.getElementById('prompt'); return p.selectionStart === 0 && p.selectionEnd === p.value.length; })()"), "«Выделить всё» не выделило текст в поле")
        # рейка: клик по черте не сдвигает карточку и оболочку
        page.mouse.move(650, 400)
        page.wait_for_timeout(200)
        ticks = page.locator(".message-rail-tick")
        check(ticks.count() == 8, f"черт рейки: {ticks.count()}")
        ticks.nth(2).hover()
        page.wait_for_timeout(300)
        card = page.evaluate("(() => { const c = document.getElementById('messageRailCard'); return {visible: c.classList.contains('is-visible'), transform: c.style.transform, text: c.textContent}; })()")
        check(card["visible"] and "Вопрос номер 2" in card["text"], f"карточка рейки: {card}")
        before = page.evaluate("document.getElementById('feed').scrollTop")
        ticks.nth(2).click()
        page.wait_for_timeout(120)
        mid = page.evaluate("(() => { const c = document.getElementById('messageRailCard'); return {transform: c.style.transform, text: c.textContent}; })()")
        check(mid["transform"] == card["transform"] and "Вопрос номер 2" in mid["text"], f"карточка рейки прыгнула или опустела при клике: {card} -> {mid}")
        page.wait_for_timeout(900)
        shell = page.evaluate("[document.scrollingElement.scrollTop, document.getElementById('main').scrollTop, document.body.scrollTop, document.getElementById('shell')?.scrollTop ?? 0]")
        check(shell == [0, 0, 0, 0], f"клик по рейке сдвинул оболочку: {shell}")
        check(page.evaluate("document.getElementById('feed').scrollTop") != before, "клик по рейке не прокрутил ленту")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_media_files_viewer():
    """Созданное изображение стоит в ответе один раз (без плитки «файл»), файлы - плитки, окно просмотра с действиями как на сайте."""
    api = cloud_api()
    shape = {"url": "/api/v1/files/file_gen/content", "name": "Созданное изображение", "media_type": "image/png", "size": 1100278, "width": 736, "height": 912}
    user_media = {"id": "m_in", "kind": "image", "file_id": "file_in", "artifact_id": "art_in", "url": "/api/v1/files/file_in/content", "name": "pug.jpg", "media_type": "image/jpeg", "size": 61864}
    gen_media = {"id": "m_gen", "kind": "generated_image", "file_id": "file_gen", "artifact_id": "art_gen", "alt": "Созданное изображение", **shape}
    items = [
        {"id": "msg_u", "role": "user", "content": "сделай его инопланетянином", "created_at": "2026-10-17T10:00:00+00:00", "attachments": [{"id": "file_in", "name": "pug.jpg", "media_type": "image/jpeg", "size": 61864, "url": "/api/v1/files/file_in/content"}],
         "media": [user_media], "artifacts": [{"id": "art_in", "type": "image", "media_id": "m_in", "file_id": "file_in", "url": "/api/v1/files/file_in/content", "name": "pug.jpg"}], "activity": [], "citations": [], "blocks": []},
        {"id": "msg_a", "role": "assistant", "content": "Готово - изображение создано.\n\n![Созданное изображение](dokky:media/m_gen)", "content_format": "dokky-md/1", "created_at": "2026-10-17T10:00:09+00:00", "status": "completed",
         "attachments": [], "media": [gen_media], "blocks": [], "activity": [], "citations": [],
         "artifacts": [{"id": "art_gen", "type": "generated_image", "media_id": "m_gen", "file_id": "file_gen", "url": "/api/v1/files/file_gen/content", "name": "Созданное изображение"},
                       {"id": "art_ppt", "type": "document", "file_id": "file_ppt", "url": "/api/v1/files/file_ppt/content", "name": "Презентация.pptx"}]},
    ]
    sent = []

    def messages(m, path, body, q):
        if m == "POST":
            sent.append(body)
            return {"run_id": "run_1", "message_id": "msg_x"}
        return {"items": items, "next_cursor": None}

    api.extra["/api/dokky/cloud/chats/chat_1/messages"] = messages
    api.extra["/api/dokky/cloud/library"] = lambda m, p, b, q: {"items": [], "next_cursor": None}
    api.extra["/api/dokky/cloud/library/file_gen/favorite"] = lambda m, p, b, q: {"id": "file_gen", "favorite": True}
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAEklEQVR4nGP8z8Dwn4EIwDiqEAAbGAECdtZ9jQAAAABJRU5ErkJggg==")

    def files(route):
        url = route.request.url
        if "file_ppt" in url:
            return route.fulfill(status=200, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", body=b"PK\x03\x04", headers={"Content-Disposition": "attachment; filename=pres.pptx"})
        return route.fulfill(status=200, content_type="image/png", body=png)

    with browser_page(api, viewport=(1300, 860)) as page:
        page.route("**/api/dokky/cloud/files/*/content*", files)
        page.wait_for_timeout(1300)
        page.click("text=Машины Атом")
        page.wait_for_selector("article.message.assistant", timeout=8000)
        page.wait_for_timeout(900)
        assistant = page.locator("section.turn-message.assistant")
        check(assistant.locator("img.rich-inline-image").count() == 1, "созданное изображение должно стоять в ответе один раз")
        check(assistant.locator(".message-media").count() == 0 and assistant.locator(".message-file-tile").count() == 1, "дубль картинки плиткой или лишние плитки")
        check("Презентация.pptx" in assistant.locator(".message-file-tile").inner_text() and "PPTX" in assistant.locator(".message-file-tile").inner_text(), "плитка презентации с видом файла")
        check(page.locator("section.turn-message.user .message-attachment.is-image").count() == 1, "вложение пользователя - миниатюра")
        # презентация скачивается по щелчку
        with page.expect_download() as download:
            assistant.locator(".message-file-tile").click()
        check(download.value.suggested_filename.endswith(".pptx"), f"скачивание презентации: {download.value.suggested_filename}")
        # у плитки своё меню
        assistant.locator(".message-file-tile").click(button="right")
        page.wait_for_selector(".popover .menu-item", timeout=3000)
        check([x.strip() for x in page.locator(".popover .menu-item").all_inner_texts()] == ["Открыть", "Скачать"], "меню файла")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        # окно просмотра: действия как на сайте
        assistant.locator("img.rich-inline-image").click()
        page.wait_for_selector("dialog.media-viewer[open] .mv-actions", timeout=3000)
        page.wait_for_timeout(500)
        names = [x.strip() for x in page.locator(".mv-actions > *").all_inner_texts()]
        check(names == ["В избранное", "Скачать", "Копировать", "Редактировать"], f"действия просмотра: {names}")
        page.click(".mv-actions button:has-text('В избранное')")
        page.wait_for_timeout(500)
        check(any(c[0] == "PUT" and c[1].endswith("/library/file_gen/favorite") for c in api.calls) and page.locator(".mv-actions button[aria-pressed='true']").count() == 1, "избранное")
        page.click(".mv-actions button:has-text('Редактировать')")
        page.wait_for_timeout(450)
        check(page.locator(".mv-edit.is-open textarea").is_visible(), "форма правки не раскрылась")
        page.fill(".mv-edit textarea", "сделай фон синим")
        page.click(".mv-edit button[type=submit]")
        page.wait_for_timeout(900)
        check(sent and sent[-1].get("content") == "сделай фон синим" and sent[-1].get("attachment_ids") == ["file_gen"], f"правка уходит сообщением с файлом: {sent}")
        check(page.locator("dialog.media-viewer").count() == 0, "окно просмотра не закрылось после отправки")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_voice_segments():
    """Голосовой ввод: запись режется на куски, куски уходят на сервер по мере записи, текст склеивается по порядку."""
    api = cloud_api()
    api.extra["/api/dokky/session"] = lambda m, p, b, q: {**api.session(), "internal_test": False}
    sent = []

    def speech(route):
        sent.append(route.request.post_data_buffer or b"")
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": f"часть {len(sent)}"}, ensure_ascii=False))

    # непрерывный тон вместо коротких сигналов поддельного микрофона: это и есть «речь» для определения пауз
    import math
    import struct
    import tempfile
    import wave
    tone = Path(tempfile.mkdtemp(prefix="voice-")) / "tone.wav"
    with wave.open(str(tone), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"".join(struct.pack("<h", int(14000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000 * 4)))
    with browser_page(api, viewport=(1300, 860), args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={tone}"],
                      init_script="localStorage.setItem('dokky-microphone-consent','1')") as page:
        page.route("**/api/dokky/cloud/speech/transcriptions", speech)
        page.wait_for_timeout(1300)
        page.fill("#prompt", "было")
        page.click("#voice")
        page.wait_for_selector("#composer.recording .voice-wave", timeout=5000)
        check(page.locator("#composer textarea").is_hidden(), "во время записи вместо поля - волна")
        # непрерывный звук закрывает первый кусок через ~6 с, его текст виден над полем ещё во время записи
        page.wait_for_selector(".voice-preview", timeout=15000)
        check("часть 1" in page.locator(".voice-preview").inner_text(), "готовый кусок виден во время записи")
        # после завершения кусков не меньше двух, текст склеен по порядку и стоит после прежнего черновика
        page.wait_for_timeout(2500)
        page.click("#voice")
        page.wait_for_function("document.querySelector('#prompt').value.includes('часть')", timeout=15000)
        value = page.input_value("#prompt")
        check(value.startswith("было часть 1") and len(sent) >= 2 and "часть 2" in value, f"текст записи: {value!r}, кусков {len(sent)}")
        check(page.locator("#composer.recording, #composer.transcribing").count() == 0, "режим записи не выключился")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_settings_overhaul():
    """Настройки: разделы по группам, выпадающие списки поверх окна, сохранение, личные инструкции, оплата и запасной вариант."""
    api = cloud_api()
    saved = []
    prefs = {"detail": "standard", "sources": "both", "memory_enabled": True, "default_model": "dokky-default"}

    def preferences(m, p, b, q):
        if m == "PATCH":
            prefs.update(b or {})
            saved.append(("PATCH", b))
        return prefs

    api.extra["/api/dokky/cloud/preferences"] = preferences
    mock = Billing("super", remaining=80).install(api)
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1400)
        page.evaluate("(() => { window.__opened = []; window.open = (url) => { window.__opened.push(url); return null; }; })()")
        page.click("#connection-badge .account-name")
        page.wait_for_selector("dialog.settings-window[open]")
        page.wait_for_timeout(500)
        groups = page.locator(".settings-group-title").all_inner_texts()
        sections = [x.strip() for x in page.locator(".settings-nav nav button").all_inner_texts()]
        check(groups == ["Настройки", "Разработка", "Этот компьютер"] and sections == ["Общие", "Аккаунт", "Подписка и оплата", "Общение", "Разработка", "Доступ с телефона", "Система", "Обновления", "О приложении"], f"разделы настроек: {groups} {sections}")
        # выпадающий список открывается поверх окна настроек и принимает щелчок
        page.click(".pref-row[data-setting='Поверхность'] .select-trigger")
        page.wait_for_selector(".popover[role=listbox] .menu-item", timeout=3000)
        page.wait_for_timeout(250)
        top = page.evaluate("""(() => { const o = [...document.querySelectorAll('.popover[role=listbox] .menu-item')].find((x) => x.textContent.includes('Тёмный чат')); const r = o.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return {onTop: Boolean(hit && hit.closest('.popover')), inside: r.bottom <= innerHeight && r.top >= 0}; })()""")
        check(top["onTop"] and top["inside"], f"список открылся под окном настроек: {top}")
        page.click(".popover[role=listbox] .menu-item:has-text('Тёмный чат')")
        page.wait_for_timeout(500)
        check(any(c[0] == "PUT" and c[1] == "/api/settings" and (c[2] or {}).get("ui_surface") == "chat-dark" for c in api.calls), "поверхность не сохранилась")
        # оформление «как в системе» и шрифты
        page.click(".pref-row[data-setting='Оформление'] button:has-text('Как в системе')")
        page.click(".pref-row[data-setting='Шрифт интерфейса'] button:has-text('Как в системе')")
        page.wait_for_timeout(500)
        check(page.evaluate("document.documentElement.dataset.uiFont") == "system", "шрифт интерфейса не применился")
        # шрифт кода выбирается из установленных, образец раскрашен как код в чате
        page.click(".pref-row[data-setting='Шрифт кода'] .select-trigger")
        page.wait_for_timeout(250)
        fonts = page.locator(".popover[role=listbox] .menu-item").all_inner_texts()
        check(fonts[0] == "Как в системе" and len(fonts) >= 2, f"список шрифтов кода: {fonts}")
        chosen = next(name for name in fonts[1:] if "не найден" not in name)
        page.click(f".popover[role=listbox] .menu-item:has-text('{chosen}')")
        page.wait_for_timeout(500)
        check(chosen in page.evaluate("document.documentElement.style.getPropertyValue('--code-font')"), f"шрифт кода не применился: {chosen}")
        check(page.locator(".pref-preview .hljs-keyword").count() >= 2, "образец кода не раскрашен")
        page.click(".pref-row[data-setting='Отправка сообщения'] button:nth-child(2)")
        page.wait_for_timeout(400)
        check(any(c[0] == "PUT" and c[1] == "/api/settings" and (c[2] or {}).get("enter_sends") is False for c in api.calls), "клавиша отправки не сохранилась")
        # доступ с телефона: выключен, имя и ссылка появляются только после включения
        page.click("[data-settings-section=remote]")
        page.wait_for_selector(".pref-row[data-setting='Доступ с телефона'] [role=switch][aria-checked=false]")
        check(page.locator(".pref-row[data-setting='Имя компьютера']").count() == 0, "имя компьютера видно при выключенном доступе")
        check(page.locator(".pref-row[data-setting='Не давать компьютеру засыпать']").count() == 1, "нет настройки сна")
        # общение: настройки сайта
        page.click("[data-settings-section=conversation]")
        page.wait_for_selector(".pref-row[data-setting='Полнота ответов']")
        page.click(".pref-row[data-setting='Полнота ответов'] .select-trigger")
        page.click(".popover[role=listbox] .menu-item:has-text('Подробно')")
        page.wait_for_timeout(400)
        page.click(".pref-row[data-setting='Память Докки'] .pref-switch")
        page.wait_for_timeout(400)
        check(("PATCH", {"detail": "full"}) in saved and ("PATCH", {"memory_enabled": False}) in saved, f"настройки общения: {saved}")
        # разработка: личные инструкции
        page.click("[data-settings-section=development]")
        page.wait_for_selector(".pref-textarea")
        page.fill(".pref-textarea", "Комментарии по-русски.")
        page.click(".pref-block-actions .primary")
        page.wait_for_timeout(500)
        check(any(c[0] == "PUT" and (c[2] or {}).get("dev_instructions") == "Комментарии по-русски." for c in api.calls), "личные инструкции не сохранились")
        # система: автозапуск
        page.click("[data-settings-section=system]")
        page.click(".pref-row[data-setting='Запускать вместе с Windows'] .pref-switch")
        page.wait_for_timeout(500)
        check(any(c[0] == "PUT" and (c[2] or {}).get("launch_at_login") is True for c in api.calls), "автозапуск")
        # подписка и оплата: каталог, цены, иконки и кнопки приходят с сервера, оплата открывается ссылкой в браузере
        page.click("[data-settings-section=billing]")
        page.wait_for_selector(".plan-tabs")
        text = page.locator(".settings-main").inner_text()
        check(page.locator(".billing-limit").count() == 2 and "Общее использование" in text, "использование не объединено с подпиской")
        check("Ваш план" in text and page.locator(".plan-detail.active").count() == 1 and page.locator(".plan-tabs button").count() == 4, "каталог планов не показан")
        check(page.locator(".plan-detail.active .plan-icon svg").count() >= 6, "иконки функций с сервера не вставлены")
        check("Активно до 18.11" in page.locator(".plan-detail.active .plan-buy").inner_text(), f"кнопка текущего плана: {page.locator('.plan-detail.active .plan-buy').inner_text()}")
        page.click(".plan-tabs button[data-plan-tab=ultra]")
        page.wait_for_timeout(300)
        check("Улучшить до Ultra" in page.locator(".plan-detail.active .plan-buy").inner_text() and "700" in page.locator(".plan-detail.active .plan-buy").inner_text(), f"доплата за переход: {page.locator('.plan-detail.active .plan-buy').inner_text()}")
        page.click(".plan-detail.active .plan-buy")
        page.wait_for_selector("dialog.payment-window[open] .payment-body[data-stage=waiting]", timeout=5000)
        check(mock.created and mock.created[0] == {"intent": "plan", "plan": "ultra", "return_app": "koddi"}, f"запрос ссылки: {mock.created}")
        check(page.evaluate("window.__opened") == ["https://dokky.goshkow.com/pay/" + "A" * 43], "ссылка на оплату не открылась в браузере")
        mock.finish("paid")
        page.wait_for_selector("dialog.payment-window .payment-body[data-stage=paid]", timeout=9000)
        check("Ultra" in page.locator(".payment-body h2").inner_text(), "после оплаты нет итога")
        page.click(".payment-actions .primary")
        page.wait_for_timeout(500)
        # поиск находит настройку и подсвечивает строку
        page.fill(".settings-search input", "автозапуск")
        page.wait_for_timeout(300)
        check([x.strip() for x in page.locator(".settings-nav nav button").all_inner_texts()] == ["Система"], "поиск по названию настройки")
        check(not page.errors, f"ошибки страницы: {page.errors}")
    # каталог не загрузился: понятная ошибка, повтор и сайт
    broken = cloud_api()
    Billing("free", remaining=0).install(broken).catalog_error = (404, {"detail": {"code": "not_found", "message": "нет"}})
    with browser_page(broken, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1400)
        page.evaluate("(() => { window.__opened = []; window.open = (url) => { window.__opened.push(url); return null; }; })()")
        page.click("#connection-badge .account-name")
        page.wait_for_selector("dialog.settings-window[open]")
        page.click("[data-settings-section=billing]")
        page.wait_for_selector("text=Не удалось загрузить планы", timeout=5000)
        page.click("text=Открыть сайт")
        page.wait_for_timeout(300)
        check(page.evaluate("window.__opened") == ["https://dokky.goshkow.com/"], "сайт для оплаты не открылся")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_math_and_map():
    """Формулы рисует KaTeX, а не текстом; места показаны настоящей картой с метками."""
    api = cloud_api()
    content = "Формула: $$E = mc^2$$\n\nВнутри строки \\(x^2 + 1\\) и сумма $5 не формула."
    items = [{"id": "msg_u", "role": "user", "content": "формулу и места", "created_at": "2026-10-17T10:00:00+00:00", "attachments": [], "artifacts": [], "activity": [], "citations": [], "media": [], "blocks": []},
             {"id": "msg_a", "role": "assistant", "content": content, "content_format": "dokky-md/1", "has_math": True, "created_at": "2026-10-17T10:00:09+00:00", "status": "completed", "attachments": [], "artifacts": [], "activity": [], "citations": [], "media": [],
              "blocks": [{"type": "place_map", "title": "Места", "places": [{"name": "Красная площадь", "lat": 55.7539, "lon": 37.6208}, {"name": "ВДНХ", "lat": 55.8266, "lon": 37.6377}]}]}]
    api.extra["/api/dokky/cloud/chats/chat_1/messages"] = lambda m, p, b, q: {"items": items, "next_cursor": None}
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAEklEQVR4nGP8z8Dwn4EIwDiqEAAbGAECdtZ9jQAAAABJRU5ErkJggg==")
    with browser_page(api, viewport=(1300, 860)) as page:
        page.route("**/api/dokky/tile/**", lambda r: r.fulfill(status=200, content_type="image/png", body=png))
        page.wait_for_timeout(1300)
        page.click("text=Машины Атом")
        page.wait_for_selector("article.message.assistant", timeout=8000)
        page.wait_for_selector(".place-map .leaflet-container", timeout=8000)
        page.wait_for_timeout(1500)
        check(page.locator("article.message.assistant .message-body .katex").count() >= 2, f"формулы KaTeX: {page.locator('article.message.assistant .message-body .katex').count()}")
        check("E = mc" in page.locator("article.message.assistant .message-body .katex-mathml").first.inner_text() or page.locator("article.message.assistant .message-body .katex-display").count() == 1, "формула не нарисована блоком")
        check("$5" in page.locator("article.message.assistant .message-body").inner_text(), "сумма в долларах не должна становиться формулой")
        check(page.locator(".place-map .leaflet-tile").count() >= 1 and page.locator(".place-map path.leaflet-interactive").count() == 2, "на карте нет плиток или меток")
        check(not page.errors, f"ошибки страницы: {page.errors}")


OPEN_STUB = "(() => { window.__opened = []; window.open = (url) => { window.__opened.push(url); return null; }; })()"


def gated_api(plan, **options):
    api = development_api()
    return api, Billing(plan, **options).install(api)


def scenario_developer_access():
    """Free, пробный период и Base видят приглашение вместо поля ввода; старые чаты читаются; оплата открывает доступ."""
    for plan, price in (("free", "300"), ("trial", "300"), ("base", "200")):
        api, mock = gated_api(plan, remaining=100)
        with browser_page(api, viewport=(1300, 860)) as page:
            page.wait_for_timeout(1500)
            check(page.locator("[data-testid=dev-gate]").is_visible(), f"{plan}: нет приглашения")
            check(page.locator("#prompt").count() == 0 and page.locator("#send").count() == 0, f"{plan}: поле ввода доступно")
            text = page.locator("[data-testid=dev-gate]").inner_text()
            check("Докки Code включается с подпиской Plus" in text and f"Перейти на Plus - {price}" in text, f"{plan}: текст приглашения: {text!r}")
            check(page.locator("[data-testid=dev-gate] .plan-icon svg").count() >= 3, f"{plan}: иконки функций плана")
            # старый чат остаётся: сообщения видны, писать нельзя
            open_chat(page)
            check(page.locator("article.message").count() >= 2, f"{plan}: прежний чат не читается")
            check(page.locator("[data-testid=locked-composer]").is_visible() and page.locator("#prompt").count() == 0, f"{plan}: в старом чате можно писать")
            check(not page.errors, f"{plan}: ошибки страницы: {page.errors}")
    # тестер с кредитами на Base имеет доступ
    api, mock = gated_api("base", tester_credits=40)
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        check(page.locator("[data-testid=dev-gate]").count() == 0 and page.locator("#prompt").is_visible(), "тестер с кредитами не должен быть заперт")
    # Plus: всё открыто
    api, mock = gated_api("super")
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        check(page.locator("[data-testid=dev-gate]").count() == 0 and page.locator("#prompt").is_visible(), "Plus не должен видеть приглашение")
    # оплата прямо из приглашения: ссылка, ожидание, доступ появляется сам
    api, mock = gated_api("free", remaining=100)
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        page.evaluate(OPEN_STUB)
        page.click("[data-testid=dev-gate] .primary")
        page.wait_for_selector("dialog.payment-window .payment-body[data-stage=waiting]", timeout=5000)
        check(mock.created == [{"intent": "plan", "plan": "super", "return_app": "koddi"}], f"ссылка на оплату: {mock.created}")
        mock.finish("paid")
        page.wait_for_selector("dialog.payment-window .payment-body[data-stage=paid]", timeout=9000)
        page.click(".payment-actions .primary")
        page.wait_for_selector("#prompt", timeout=5000)
        check(page.locator("[data-testid=dev-gate]").count() == 0, "приглашение осталось после оплаты")
        check(not page.errors, f"ошибки страницы: {page.errors}")
    # сервер отказал посреди работы: запись закрывается, чат остаётся
    api, mock = gated_api("super")
    history = api.extra["/api/chats/1/messages"]

    def messages(method, path, body, query):
        if method == "POST":
            mock.plan = "free"
            return 403, {"detail": {"code": "plan_required", "message": "Режим разработки доступен с Plus"}}
        return history(method, path, body, query)

    api.extra["/api/chats/1/messages"] = messages
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        open_chat(page)
        page.fill("#prompt", "сделай")
        page.press("#prompt", "Enter")
        page.wait_for_selector("[data-testid=locked-composer]", timeout=6000)
        check(page.locator("article.message").count() >= 2, "после отказа чат стал нечитаемым")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_usage_hints():
    """Подсказки про остаток: сброс при остатке меньше 10%, улучшение плана при исчерпанном окне (кроме Ultra)."""
    api = cloud_api()
    mock = Billing("super", remaining=7).install(api)
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        page.evaluate(OPEN_STUB)
        check("Использование заканчивается" in page.locator("[data-testid=usage-notice]").inner_text(), "нет подсказки про остаток")
        check(page.locator(".side-reset").is_visible(), "нет кнопки сброса в меню")
        page.click("[data-testid=usage-notice] .primary")
        page.wait_for_selector("[data-testid=usage-reset]")
        page.click("[data-testid=usage-reset] button[data-percent='50']")
        check("57%" in page.locator(".usage-reset-result").inner_text(), f"итог после сброса: {page.locator('.usage-reset-result').inner_text()}")
        page.click(".usage-reset-pay")
        page.wait_for_selector("dialog.payment-window .payment-body[data-stage=waiting]", timeout=5000)
        check(mock.created == [{"intent": "topup", "percent": 50, "return_app": "koddi"}], f"ссылка пополнения: {mock.created}")
        mock.finish("paid")
        page.wait_for_selector("dialog.payment-window .payment-body[data-stage=paid]", timeout=9000)
        check("107%" in page.locator(".payment-body p").inner_text() or "%" in page.locator(".payment-body p").inner_text(), "итог пополнения без остатка")
        page.click(".payment-actions .primary")
        page.wait_for_timeout(500)
        check(page.locator("[data-testid=usage-reset]").count() == 0 and page.locator(".side-reset").count() == 0, "сброс остался после пополнения")
        check(not page.errors, f"ошибки страницы: {page.errors}")
    # разработка: окно 5 часов исчерпано - предложено улучшение; у Ultra только время
    for plan, expected in (("super", "Улучшить до Premium"), ("ultra", None)):
        api, mock = gated_api(plan, remaining=80, window_used=35)
        with browser_page(api, viewport=(1300, 860)) as page:
            page.wait_for_timeout(1500)
            text = page.locator("[data-testid=usage-notice]").inner_text()
            check("Лимит на 5 часов закончился" in text, f"{plan}: подсказка окна: {text!r}")
            if expected:
                check(expected in text, f"{plan}: нет предложения улучшить: {text!r}")
            else:
                check("Улучшить" not in text, f"{plan}: у Ultra не предлагается улучшение: {text!r}")
            page.click("[data-testid=usage-notice] .usage-notice-close")
            page.wait_for_timeout(300)
            check(page.locator("[data-testid=usage-notice]").count() == 0, f"{plan}: подсказка не скрылась")
    # бесплатное использование закончилось
    api = cloud_api()
    Billing("free", remaining=0).install(api)
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        check("Бесплатное использование закончилось" in page.locator("[data-testid=usage-notice]").inner_text(), "нет подсказки у Free")


def scenario_payment_states():
    """Оплата: отмена, истёкшая ссылка, отказы сервера и остановка опроса."""
    api = cloud_api()
    mock = Billing("super", remaining=80).install(api)
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        page.evaluate(OPEN_STUB)
        page.click("#connection-badge .account-name")
        page.click("[data-settings-section=billing]")
        page.click(".plan-tabs button[data-plan-tab=premium]")
        page.click(".plan-detail.active .plan-buy")
        page.wait_for_selector(".payment-body[data-stage=waiting]")
        page.click("text=Открыть страницу ещё раз")
        check(len(page.evaluate("window.__opened")) == 2, "повторное открытие ссылки")
        mock.finish("canceled")
        page.wait_for_selector(".payment-body[data-stage=canceled]", timeout=9000)
        check("Деньги не списаны" in page.locator(".payment-body p").inner_text(), "нет текста про деньги")
        page.click("text=Попробовать снова")
        page.wait_for_selector(".payment-body[data-stage=waiting]")
        check(len(mock.created) == 2, "после отмены нужна новая ссылка")
        mock.finish("expired")
        page.wait_for_selector(".payment-body[data-stage=expired]", timeout=9000)
        page.click(".payment-actions .pref-button:has-text('Закрыть')")
        page.wait_for_timeout(400)
        # «Отмена» останавливает опрос
        page.click(".plan-detail.active .plan-buy")
        page.wait_for_selector(".payment-body[data-stage=waiting]")
        page.click(".payment-actions .pref-button:has-text('Отмена')")
        page.wait_for_timeout(300)
        before = len([c for c in api.calls if "/billing/links/" in c[1]])
        page.wait_for_timeout(3500)
        check(len([c for c in api.calls if "/billing/links/" in c[1]]) == before, "опрос продолжился после отмены")
        # отказы сервера
        for status, reason, retry, expected in (
            (409, "already_included", None, "уже входит"),
            (409, "no_paid_plan", None, "платной подпиской"),
            (429, None, 7, "7 с"),
            (503, None, None, "временно недоступна"),
        ):
            code = "conflict" if status == 409 else "rate_limited" if status == 429 else "upstream_unavailable"
            detail = {"code": code, "message": "сервер"}
            if reason:
                detail["reason"] = reason
            if retry:
                detail["retry_after_seconds"] = retry
            mock.fail_link = (status, {"detail": detail})
            page.click(".plan-detail.active .plan-buy")
            page.wait_for_selector(".payment-body[data-stage=failed]", timeout=5000)
            check(expected in page.locator(".payment-body").inner_text(), f"{status}/{reason}: {page.locator('.payment-body').inner_text()!r}")
            page.click(".payment-actions .pref-button:has-text('Закрыть')")
            page.wait_for_timeout(400)
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_subscription_sync():
    """Оплата на сайте подхватывается приложением само; каталог, иконки и новые планы берутся с сервера; иконки очищаются."""
    api, mock = gated_api("free", remaining=100, evil_icon=True)
    with browser_page(api, viewport=(1300, 860), clock=True) as page:
        page.wait_for_timeout(1500)
        check(page.locator("[data-testid=dev-gate]").is_visible(), "нет приглашения у Free")
        # человек оплатил на сайте сам, администратор добавил план: без действий в приложении
        mock.plan = "super"
        mock.extra_plan = True
        mock.catalog_version = "v2"
        page.clock.fast_forward(61000)
        page.wait_for_selector("#prompt", timeout=8000)
        check(page.locator("[data-testid=dev-gate]").count() == 0, "доступ не открылся после оплаты на сайте")
        page.click("#connection-badge .account-name")
        page.click("[data-settings-section=billing]")
        page.wait_for_selector(".plan-tabs")
        check(page.locator(".plan-tabs button").count() == 5 and "Mega" in page.locator(".plan-tabs").inner_text(), "новый план сервера не показан")
        page.click(".plan-tabs button[data-plan-tab=mega]")
        check("×20 объём использования" in page.locator(".plan-detail.active").inner_text(), "функции нового плана")
        # иконка с вредным кодом очищена
        check(page.evaluate("window.__pwned") is None, "код из иконки выполнился")
        icon = page.evaluate("document.querySelector('.plan-detail.active .plan-icon svg') ? document.querySelector('.plan-detail.active .plan-icon').innerHTML : ''")
        page.click(".plan-tabs button[data-plan-tab=base]")
        icon = page.evaluate("document.querySelector('.plan-detail.active .plan-icon svg') ? document.querySelector('.plan-detail.active .plan-icon').innerHTML : ''")
        check("script" not in icon and "onload" not in icon and "onclick" not in icon and "<use" not in icon and "<style" not in icon, f"иконка не очищена: {icon!r}")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_skills_menu_and_project_menu():
    """Меню «+»: без «Создать проект» и «Сжать контекст», навыки подключаются чипом и уходят в запросе; у проекта есть контекстное меню."""
    api = development_api()
    with browser_page(api, viewport=(1300, 860)) as page:
        page.wait_for_timeout(1500)
        open_chat(page)
        page.click("#attach")
        page.wait_for_selector(".composer-actions .menu-item")
        items = [x.strip() for x in page.locator(".composer-actions .menu-item").all_inner_texts()]
        check(not any("Создать проект" in x or "Сжать контекст" in x for x in items), f"лишние пункты в меню: {items}")
        check(any("Навыки" in x for x in items) and any("Открыть папку" in x for x in items) and any("Планирование" in x for x in items), f"пункты меню: {items}")
        page.click(".composer-actions .menu-item:has-text('Навыки')")
        page.wait_for_selector(".skill-option")
        check(page.locator(".skill-option").count() == 15, "в подменю не все навыки")
        check(page.locator(".skill-category").count() >= 6, "навыки не разложены по категориям")
        page.fill(".skill-search input", "написание")
        page.wait_for_timeout(200)
        check(page.locator(".skill-option").count() == 1 and "Написание тестов" in page.locator(".skill-option").inner_text(), "поиск навыков")
        page.click(".skill-option")
        page.wait_for_selector(".skill-chip")
        check("Написание тестов" in page.locator(".skill-chip").inner_text(), "навык не подключился чипом")
        # запрос уходит с навыком
        api.extra["/api/chats/1/messages"] = lambda m, p, b, q: (500, {"detail": "остановлено проверкой"}) if m == "POST" else []
        page.fill("#prompt", "Покрой тестами оплату")
        page.press("#prompt", "Enter")
        page.wait_for_timeout(800)
        sent = [c for c in api.calls if c[0] == "POST" and c[1] == "/api/chats/1/messages"]
        check(sent and sent[0][2].get("skill_ids") == [next(x["id"] for x in api.skills if x["name"] == "Написание тестов")], f"навык не ушёл в запросе: {sent}")
        check(page.locator(".skill-chip").count() == 1, "после ошибки навык должен вернуться в поле")
        page.click(".skill-chip button")
        check(page.locator(".skill-chip").count() == 0, "чип не убирается")
        # контекстное меню проекта
        page.click(".project-head >> nth=0", button="right")
        page.wait_for_selector("[role=menuitem]:has-text('Новый чат в проекте')")
        names = [x.strip() for x in page.locator(".popover[aria-label='Действия с проектом'] [role=menuitem]").all_inner_texts()]
        check(names == ["Новый чат в проекте", "Копировать путь", "Убрать из списка"], f"меню проекта: {names}")
        page.keyboard.press("Escape")
        page.click(".project-head >> nth=0 >> button[aria-label^='Действия с проектом']")
        page.wait_for_selector("[role=menuitem]:has-text('Убрать из списка')")
        check(not page.errors, f"ошибки страницы: {page.errors}")


def scenario_tasks_automations_skills_pages():
    """Страницы «Задачи», «Автоматизации», «Навыки» объясняют себя и работают."""
    api = development_api()
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1500)
        page.click("[aria-label='Задачи']")
        page.wait_for_selector(".task-card")
        text = page.locator(".page-content").inner_text()
        check("Каждое ваше сообщение агенту" in text and "1 в работе, 1 в очереди, 1 готово, 1 с ошибкой" in text, f"пояснение или счётчики: {text[:300]!r}")
        check(page.locator(".task-card").count() == 4, "карточек задач")
        page.click(".task-tabs [data-tab=failed]")
        check(page.locator(".task-card").count() == 1 and "Нужен Plus" in page.locator(".task-card").inner_text() or "недоступен на вашем тарифе" in page.locator(".task-card").inner_text(), "вкладка ошибок")
        page.click(".task-tabs [data-tab=active]")
        check("Почини фризы" in page.locator(".task-card").inner_text(), "вкладка «В работе»")
        page.click(".task-card .task-main")
        page.wait_for_timeout(700)
        check(page.locator("#title").inner_text().strip() != "Задачи", "клик по задаче не открыл чат")
        # автоматизации
        page.click("#side-more")
        page.wait_for_timeout(400)
        page.click("[aria-label='Автоматизации']")
        page.wait_for_selector(".auto-card")
        check(page.locator(".auto-card").count() == 2 and page.locator(".template-card").count() == 4, "карточки и заготовки")
        check("По будням в 09:00" in page.locator(".auto-card").first.inner_text() and "Следующий запуск" in page.locator(".auto-card").first.inner_text(), "расписание словами")
        page.click(".template-card >> nth=0")
        page.wait_for_selector("dialog.form-dialog[open] input[required]")
        check(page.locator("dialog.form-dialog[open] textarea").input_value().startswith("Изучи последние изменения"), "заготовка не подставилась")
        page.fill("dialog.form-dialog[open] input[required]", "Мой обзор")
        page.click("dialog.form-dialog[open] button[type=submit]")
        page.wait_for_timeout(700)
        created = [c for c in api.calls if c[0] == "POST" and c[1] == "/api/automations"]
        check(created and created[0][2]["schedule"] == "weekdays@09:00" and created[0][2]["action"]["kind"] == "agent" and created[0][2]["action"]["project_id"] == 1, f"создание автоматизации: {created}")
        page.click("text=Создать с помощью ИИ")
        page.wait_for_selector("dialog.form-dialog[open] textarea")
        page.fill("dialog.form-dialog[open] textarea", "Каждый вечер собирать изменения за день")
        page.click("dialog.form-dialog[open] button[type=submit]")
        page.wait_for_timeout(900)
        assisted = [c for c in api.calls if c[1] == "/api/automations/assist"]
        check(assisted and assisted[0][2] == {"project_id": 1, "request": "Каждый вечер собирать изменения за день"}, f"запрос к ИИ: {assisted}")
        # навыки
        if not page.locator(".side-more[data-open]").count():
            page.click("#side-more")
            page.wait_for_timeout(300)
        page.click("[aria-label='Навыки']")
        page.wait_for_selector(".skill-card")
        check(page.locator(".skill-card").count() == 15 and page.locator(".skill-badge").count() == 15, "не все стандартные навыки")
        check(page.locator(".skill-card button[aria-label='Изменить']").count() == 0 and page.locator(".skill-card button[aria-label='Удалить']").count() == 0, "стандартные навыки нельзя менять")
        page.fill(".page-search input", "git")
        page.wait_for_timeout(200)
        check(page.locator(".skill-card").count() == 1 and "Коммиты" in page.locator(".skill-card").inner_text(), "поиск на странице навыков")
        page.click(".skill-card button[aria-label='Сделать свою копию']")
        page.wait_for_selector("dialog.form-dialog[open] textarea")
        check("(мой)" in page.locator("dialog.form-dialog[open] input").first.input_value() and len(page.locator("dialog.form-dialog[open] textarea").input_value()) > 800, "копия навыка")
        page.click("dialog.form-dialog[open] button[type=submit]")
        page.wait_for_timeout(700)
        check(any(c[0] == "POST" and c[1] == "/api/skills" for c in api.calls), "копия не сохранилась")
        page.click(".skill-card >> nth=0 >> text=Использовать")
        page.wait_for_selector(".skill-chip")
        check(not page.errors, f"ошибки страницы: {page.errors}")


SCENARIOS = [
    scenario_boot_and_open, scenario_send_stream, scenario_requests_queue_plan, scenario_permission_modes, scenario_inspector,
    scenario_settings_pages_library, scenario_cloud, scenario_cloud_send, scenario_search, scenario_attachments_and_mode,
    scenario_live_stream, scenario_shell_stability, scenario_audit_items,
    scenario_sidebar_search_reorder, scenario_pin_and_expand, scenario_shell_rules, scenario_motion_rules, scenario_long_chat_list, scenario_tiles_library_media_auth, scenario_par_login, scenario_context_menu_rail_title, scenario_media_files_viewer, scenario_voice_segments, scenario_settings_overhaul, scenario_math_and_map, scenario_developer_access, scenario_usage_hints, scenario_payment_states, scenario_subscription_sync, scenario_skills_menu_and_project_menu, scenario_tasks_automations_skills_pages,
]


def main() -> int:
    only = sys.argv[1:]
    failed = 0
    for scenario in SCENARIOS:
        if only and scenario.__name__ not in only:
            continue
        try:
            scenario()
            print("OK  ", scenario.__name__)
        except Exception as error:  # noqa: BLE001
            failed += 1
            print("FAIL", scenario.__name__, "-", " ".join(str(error).split())[:400])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
