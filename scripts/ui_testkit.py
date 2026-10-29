"""Общий набор для проверок интерфейса в браузере: раздача сборки и подставной API Докки без запросов к ИИ."""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from billing_mock import Billing

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from koddi.skills.catalog import BUILTIN  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WEB_NEXT = ROOT / "backend/koddi/desktop/web-next"
WEB_LEGACY = ROOT / "backend/koddi/desktop/web"


class _Handler(SimpleHTTPRequestHandler):
    base = WEB_NEXT

    def log_message(self, *_args):
        pass

    def translate_path(self, path):
        path = path.split("?")[0]
        if path.startswith("/ui/"):
            path = path[3:]
        return str(self.base / path.lstrip("/"))


class Api:
    """Подставной сервер: списки, настройки и сообщения хранятся в памяти, лишние запросы записываются."""

    def __init__(self, scope="development"):
        self.scope = scope
        self.projects = [
            {"id": 1, "name": "zaprethub.new", "path": "C:/Users/User/Desktop/zaprethub.new"},
            {"id": 2, "name": "addhead", "path": "C:/Users/User/Desktop/addhead"},
        ]
        self.local_chats = [
            {"id": 1, "title": "Фризы при возврате из фона", "mode": "development", "model": "dokky-code", "project_id": 1, "updated_at": "2026-10-16T09:00:00+00:00"},
            {"id": 2, "title": "Фризы приложения после сворачивания", "mode": "development", "model": "dokky-code", "project_id": 1, "updated_at": "2026-10-15T09:00:00+00:00"},
            {"id": 3, "title": "Поиск документации", "mode": "development", "model": "dokky-code", "project_id": 2, "updated_at": "2026-10-14T09:00:00+00:00"},
            {"id": 4, "title": "Идеи для улучшения", "mode": "development", "model": "dokky-code", "project_id": None, "updated_at": "2026-10-13T09:00:00+00:00"},
        ]
        self.cloud_chats = [
            {"id": "chat_1", "title": "Машины Атом", "mode": "chat", "model": "dokky-default", "version": 1, "updated_at": "2026-10-16T09:00:00+00:00"},
            {"id": "chat_2", "title": "Рецепт борща", "mode": "chat", "model": "dokky-default", "version": 1, "updated_at": "2026-10-10T09:00:00+00:00"},
        ]
        self.settings = {
            "theme": "dark", "ui_motion": "full", "chat_width": "standard", "text_size": 14, "ui_surface": "classic", "code_font": "Consolas",
            "enter_sends": True, "permission_profile": "ask", "private_network": False, "selected_model": "dokky-code",
            "left_panel_collapsed": False, "right_panel_collapsed": False, "coding_reasoning_mode": "light", "coding_keepalive": False,
            "ui_font": "inter", "dev_instructions": "", "launch_at_login": False,
        }
        self.models = [{
            "tag": "dokky-code", "name": "Докки", "source": "remote", "context_tokens": 200000,
            "reasoning_modes": [
                {"id": "light", "name": "Light", "compact_at_tokens": 90000},
                {"id": "pro", "name": "Pro", "compact_at_tokens": 120000},
                {"id": "max", "name": "Max", "compact_at_tokens": 150000},
            ],
        }]
        self.cloud_models = [{"id": "dokky-default", "name": "Докки (авто)", "roles": ["chat", "search"], "available": True}]
        self.messages: dict[str, list] = {}
        self.unexpected: list[str] = []
        self.calls: list[tuple[str, str, object]] = []
        self.extra = {}
        self.skills = [
            {"id": index + 1, "name": item["name"], "description": item["description"], "builtin": True, "category": item["category"],
             "workflow": {"builtin": True, "instructions": item["instructions"]}}
            for index, item in enumerate(BUILTIN)
        ]
        self.runs = [
            {"id": "q:1", "chat_id": 1, "chat_title": "Фризы при возврате из фона", "project_id": 1, "project_name": "zaprethub.new", "status": "queued", "request": "Добавь тест на таймер", "started_at": "2026-10-19 09:40:00", "finished_at": None, "error": ""},
            {"id": "t:5", "chat_id": 1, "chat_title": "Фризы при возврате из фона", "project_id": 1, "project_name": "zaprethub.new", "status": "running", "request": "Почини фризы при возврате из фона", "started_at": "2026-10-19 09:30:00", "finished_at": None, "error": ""},
            {"id": "t:4", "chat_id": 2, "chat_title": "Фризы приложения после сворачивания", "project_id": 1, "project_name": "zaprethub.new", "status": "completed", "request": "Найди причину задержки при сворачивании окна", "started_at": "2026-10-18 12:00:00", "finished_at": "2026-10-18 12:07:00", "error": ""},
            {"id": "t:3", "chat_id": 3, "chat_title": "Поиск документации", "project_id": 2, "project_name": "addhead", "status": "failed", "request": "Обнови документацию API", "started_at": "2026-10-17 10:00:00", "finished_at": "2026-10-17 10:01:00", "error": "plan_required", "summary": "Нужен Plus"},
        ]
        self.automations = [
            {"id": 1, "name": "Утренний обзор", "schedule": "weekdays@09:00", "schedule_text": "По будням в 09:00", "enabled": True, "status": "completed", "last_result": "Агент запущен: ход работы в чате",
             "last_run_at": "2026-10-19 06:00:00", "next_run_at": "2026-10-20T09:00", "last_chat_id": 1, "kind": "agent", "project_id": 1, "check_title": "",
             "action": {"kind": "agent", "prompt": "Изучи последние изменения проекта и кратко опиши, что нового.", "project_id": 1}},
            {"id": 2, "name": "Тесты каждый день", "schedule": "daily@12:00", "schedule_text": "Каждый день в 12:00", "enabled": False, "status": "failed", "last_result": "2 теста упали",
             "last_run_at": "2026-10-18 09:00:00", "next_run_at": None, "last_chat_id": None, "kind": "command", "project_id": 1, "check_title": "",
             "action": {"kind": "command", "command": "pytest -q", "project_id": 1}},
        ]
        # по умолчанию человек на Plus: каталог планов и подписка отвечают как настоящий сервер
        self.billing = Billing("super", remaining=80).install(self)

    def session(self):
        return {
            "authenticated": True, "internal_test": True, "user": {"id": "u1", "display_name": "Иван Гошков"},
            "bootstrap": {"models": self.cloud_models, "preferences": {}, "capabilities": {}},
        }

    def handle(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        method = request.method
        body = None
        if request.post_data:
            try:
                body = request.post_data_json
            except Exception:
                body = None
        self.calls.append((method, path, body))
        if path in {"/api/dokky/events", "/api/events"}:
            return route.fulfill(content_type="text/event-stream", body="")
        if path == "/api/dokky/cloud/me/avatar":
            return route.fulfill(status=404, body="")
        for key, handler in self.extra.items():
            if path == key or (key.endswith("*") and path.startswith(key[:-1])):
                value = handler(method, path, body, parse_qs(parsed.query))
                if isinstance(value, tuple):  # (HTTP-статус, тело): для проверки отказов сервера
                    return route.fulfill(status=value[0], content_type="application/json", body=json.dumps(value[1]))
                if value is not None:
                    return route.fulfill(content_type="application/json", body=json.dumps(value))
        value = self._value(method, path, body, parse_qs(parsed.query))
        if value is None:
            self.unexpected.append(f"{method} {path}")
            return route.fulfill(content_type="application/json", body="[]")
        route.fulfill(content_type="application/json", body=json.dumps(value))

    def _value(self, method, path, body, query):
        # открытие проекта запускает сборку карты на сервере; ответ окну не нужен
        if method == "GET" and path.startswith("/api/projects/") and path.endswith("/map/state"):
            return {"status": "ready", "version": 1, "files": 0, "truncated": False, "built_at": None, "watch": None}
        if path == "/api/settings":
            if method == "PUT" and isinstance(body, dict):
                self.settings.update({k: v for k, v in body.items() if k in self.settings})
            return self.settings
        if path == "/api/dokky/session":
            return self.session()
        if path == "/api/dokky/surface":
            # режим чата через страницу сайта проверяет отдельный сценарий, остальные идут на своём чате
            return {"available": False}
        if path == "/api/dokky/bootstrap":
            return self.session()["bootstrap"]
        if path == "/api/projects":
            return self.projects
        if path == "/api/chats":
            return self.local_chats
        if path == "/api/models/options":
            return self.models
        if path == "/api/modes":
            return [{"id": key, "name": key} for key in ("development", "chat", "plan")]
        if path == "/api/app/diagnostics":
            return {"build_number": "test"}
        if path == "/api/updates/status":
            return {"status": "disabled", "reason": "dev", "current": "0.2.1", "platform": None, "kind": None, "method": "dev", "auto_download": True, "error": "", "available": None, "progress": None}
        if path == "/api/remote-control" and method == "GET":
            return {"enabled": False, "state": "off", "message": "", "name": "Компьютер", "prevent_sleep": "working", "prevent_sleep_active": False,
                    "power": "ac", "pending": 0, "new_session_project_id": None, "shared": {}, "site": "https://dokky.goshkow.com"}
        if path == "/api/dokky/cloud/chats":
            return {"items": self.cloud_chats, "next_cursor": None}
        if path == "/api/dokky/cloud/subscription":
            return {"plan": "plus", "remaining_percent": 80, "developer": {"window_used_percent": 7, "window_percent": 35}}
        if path == "/api/dokky/cloud/preferences":
            return {}
        if path == "/api/skills":
            if method in {"POST", "PUT"} and isinstance(body, dict):
                self.skills.append({"id": 100 + len(self.skills), "name": body["name"], "description": body.get("description", ""), "builtin": False, "category": "Мои навыки", "workflow": body.get("workflow", {})})
            return self.skills
        if path.startswith("/api/skills/") and method in {"PUT", "DELETE"}:
            return {"ok": True}
        if path == "/api/runs":
            return self.runs
        if path == "/api/automations":
            if method == "POST" and isinstance(body, dict):
                self.automations.append({"id": 50 + len(self.automations), "name": body["name"], "schedule": body["schedule"], "schedule_text": body["schedule"], "enabled": True, "status": "idle", "last_result": "", "last_run_at": None,
                                         "next_run_at": None, "last_chat_id": None, "kind": body["action"].get("kind", ""), "project_id": body["action"].get("project_id"), "check_title": "", "action": body["action"]})
            return self.automations
        if path == "/api/automations/assist":
            return {"chat": {"id": 99, "title": "Новая автоматизация", "mode": "development", "model": "dokky-code", "project_id": 1, "updated_at": "2026-10-19T10:00:00+00:00"}, "queue": {}}
        if path.startswith("/api/automations/") and method in {"PATCH", "POST", "DELETE"}:
            return {"ok": True}
        if path.endswith("/queue"):
            return {"items": [], "paused": False}
        if path.endswith("/turn"):
            return {"active": False}
        if path.endswith("/plan"):
            return {"steps": [], "document": "", "turn_id": None}
        if path.endswith(("/messages", "/changes", "/requests")):
            return []
        if "coding-session" in path:
            return {"id": "s", "reasoning_mode": "light", "version": 1, "history": {"length": 0}, "last_usage": None}
        return None


@contextmanager
def browser_page(api: Api, *, viewport=(1280, 860), legacy=False, init_script: str | None = None, args: list[str] | None = None, clock: bool = False, setup=None):
    """Открывает собранный интерфейс на подставном API и возвращает страницу."""
    handler = type("Handler", (_Handler,), {"base": WEB_LEGACY if legacy else WEB_NEXT})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=args or [])
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, bypass_csp=True)
            page.errors = errors  # type: ignore[attr-defined]
            page.on("pageerror", lambda error: errors.append(str(error)))
            if clock:  # время страницы можно перематывать: периодические опросы проверяются без ожидания
                page.clock.install()
            page.route("**/api/**", api.handle)
            page.add_init_script(f"localStorage.setItem('koddi-product-scope','{api.scope}')")
            if init_script:
                page.add_init_script(init_script)
            if setup:  # свои маршруты до загрузки: например, подменная страница сайта во фрейме
                setup(page)
            page.goto(f"http://127.0.0.1:{server.server_port}/ui/", wait_until="load")
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
