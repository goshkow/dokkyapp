"""Подставной сервер подписок Докки для проверок интерфейса: каталог планов, `/subscription`, ссылки на оплату.

Формат ответов повторяет описание API v1 из репозитория dokky (раздел «Подписки, лимиты и оплата через сайт»): планы, функции с готовыми
SVG-иконками, цены, `action`, `usage_reset`, `developer.allowed`. Состояние меняется методами, как будто человек
оплатил на сайте.
"""

from __future__ import annotations

import json

ORDER = ["base", "super", "premium", "ultra"]
NAMES = {"base": "Base", "super": "Plus", "premium": "Premium", "ultra": "Ultra"}
PRICES = {"base": 100.0, "super": 300.0, "premium": 600.0, "ultra": 1000.0}
MULTIPLIER = {"base": 1.0, "super": 3.0, "premium": 6.0, "ultra": 10.0}
TAGLINES = {
    "base": "Расширенные возможности Докки.",
    "super": "Все возможности Base и ×3 объём использования.",
    "premium": "Все возможности Plus и ×6 объём использования.",
    "ultra": "Все возможности Premium и ×10 объём использования.",
}


def icon(*shapes: str) -> str:
    return '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + "".join(shapes) + "</svg>"


ICONS = {
    "usage": icon('<path d="M3 3v16a2 2 0 0 0 2 2h16"/>', '<path d="M7 11l4-4 4 4 5-5"/>'),
    "chat": icon('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
    "reasoning": icon('<circle cx="12" cy="12" r="9"/>', '<path d="M9 9a3 3 0 1 1 4 3v2"/>', '<path d="M12 18h.01"/>'),
    "research": icon('<circle cx="11" cy="11" r="7"/>', '<path d="m21 21-4.3-4.3"/>'),
    "files": icon('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>', '<path d="M14 3v6h6"/>'),
    "images": icon('<rect x="3" y="3" width="18" height="18" rx="2"/>', '<circle cx="9" cy="9" r="2"/>', '<path d="m21 15-5-5L5 21"/>'),
    "voice": icon('<rect x="9" y="3" width="6" height="12" rx="3"/>', '<path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
    "youtube": icon('<rect x="3" y="5" width="18" height="14" rx="3"/>', '<path d="m10 9 5 3-5 3z"/>'),
    "browser": icon('<circle cx="12" cy="12" r="9"/>', '<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
    "improved_search": icon('<circle cx="11" cy="11" r="7"/>', '<path d="m21 21-4.3-4.3M8 11h6"/>'),
    "slides": icon('<rect x="3" y="4" width="18" height="12" rx="1"/>', '<path d="M12 16v4M8 20h8"/>'),
    "priority": icon('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>'),
    "early": icon('<path d="M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z"/>'),
}
LABELS = {
    "chat": ("AI-чат", "Ответы на вопросы и обсуждение идей."),
    "reasoning": ("Режимы размышления", "Быстрый, усиленный и автоматический режимы для задач разной сложности."),
    "research": ("Поиск в интернете", "Поиск и изучение страниц с проверяемыми ссылками на источники."),
    "files": ("Создание Word, Excel, PDF, TXT", "Создание и редактирование текстов, документов, PDF и таблиц."),
    "images": ("Генерация и редактирование изображений", "Создание изображений по описанию и изменение загруженных картинок."),
    "voice": ("Голосовые запросы", "Диктуйте запросы вместо ввода текста с клавиатуры."),
    "youtube": ("Анализ YouTube роликов", "Изучение содержания YouTube-видео и ответы на вопросы по ролику."),
    "browser": ("Работа с браузером", "Докки открывает виртуальный браузер и читает публичные страницы."),
    "improved_search": ("Улучшенный поиск в интернете", "Более подробные ответы и карточки страниц под ответом."),
    "slides": ("Умное создание презентаций PPTX", "Презентации PPTX со структурой, оформлением и иллюстрациями."),
    "priority": ("Приоритет запросов", "Приоритет обработки при загрузке сервиса."),
    "early": ("Ранний доступ к новым функциям", "Новые функции до общего выпуска."),
}
OWN = {
    "base": ["chat", "reasoning", "research", "files", "images", "voice"],
    "super": ["youtube", "browser", "improved_search", "slides", "priority"],
    "premium": ["early"],
    "ultra": [],
}


def feature(fid: str, label: str, description: str, inherited: bool) -> dict:
    return {"id": fid, "label": label, "description": description, "from_previous_plan": inherited,
            "icon": {"name": fid, "url": f"https://dokky.goshkow.com/assets/icons/{fid}.svg", "svg": ICONS[fid]}}


def plan_features(plan: str) -> list[dict]:
    index = ORDER.index(plan)
    multiplier = MULTIPLIER[plan]
    usage_label = "Базовый объём использования" if multiplier == 1 else f"×{multiplier:g} объём использования"
    own = [feature("usage", usage_label, "Больше запросов и сложных задач в пределах объёма использования плана.", False)]
    own += [feature(fid, *LABELS[fid], False) for fid in OWN[plan]]
    inherited = [feature(fid, *LABELS[fid], True) for earlier in ORDER[:index] for fid in OWN[earlier]]
    if plan == "base":
        return own
    return own + inherited


class Billing:
    """Состояние подписки одного человека и ответы сервера по нему."""

    def __init__(self, plan: str = "super", *, remaining: int = 80, window_used: float = 7, payment: bool = True, catalog_version: str = "v1",
                 tester_credits: float | None = None, discount: int = 0, expires: str = "2026-11-18T12:00:00+00:00", renewal: bool = True,
                 window_resets: str = "2026-10-18T22:30:00+00:00", extra_plan: bool = False, evil_icon: bool = False):
        self.plan = plan  # free, trial, base, super, premium, ultra, admin
        self.remaining = remaining
        self.window_used = window_used
        self.payment = payment
        self.catalog_version = catalog_version
        self.tester_credits = tester_credits
        self.discount = discount
        self.expires = expires
        self.renewal = renewal
        self.window_resets = window_resets
        self.extra_plan = extra_plan
        self.evil_icon = evil_icon
        self.links: dict[str, dict] = {}
        self.created: list[dict] = []
        self.fail_link: tuple[int, dict] | None = None
        self.catalog_error: tuple[int, dict] | None = None
        self.calls = {"subscription": 0, "plans": 0}

    # состояние
    @property
    def paid(self) -> bool:
        return self.plan in {"base", "super", "premium", "ultra"}

    @property
    def unlimited(self) -> bool:
        return self.plan == "admin"

    @property
    def tester_active(self) -> bool:
        return self.tester_credits is not None and self.tester_credits > 0

    def developer_allowed(self) -> bool:
        return self.unlimited or self.tester_active or self.plan in {"super", "premium", "ultra"}

    def current_plan_id(self) -> str:
        return "ultra" if self.unlimited else self.plan

    def current_index(self) -> int:
        return ORDER.index(self.plan) if self.paid else -1

    # ответы
    def usage_reset(self) -> dict:
        if self.unlimited:
            return {"available": False, "reason": "unlimited", "threshold_percent": 10, "remaining_percent": self.remaining, "options": []}
        if not self.paid:
            return {"available": False, "reason": "no_paid_plan", "threshold_percent": 10, "remaining_percent": self.remaining, "options": []}
        if self.remaining >= 10:
            return {"available": False, "reason": "usage_above_threshold", "threshold_percent": 10, "remaining_percent": self.remaining, "options": []}
        price = PRICES[self.plan]
        return {"available": True, "reason": None, "threshold_percent": 10, "remaining_percent": self.remaining, "tier": self.plan, "tier_name": NAMES[self.plan],
                "options": [{"percent": p, "price": round(price * p / 100 * 1.2, 2), "currency": "RUB", "remaining_after_percent": self.remaining + p} for p in (25, 50, 100)]}

    def subscription(self) -> dict:
        self.calls["subscription"] += 1
        allowed = self.developer_allowed()
        reason_code = None if allowed else "plus_required"
        reason = None if allowed else "Режим разработки доступен с оплаченным Plus и выше"
        if allowed and not self.unlimited and self.window_used >= 35:
            allowed, reason_code, reason = False, "window_exhausted", "Окно в 5 часов исчерпано"
        if allowed and not self.unlimited and self.remaining <= 0:
            allowed, reason_code, reason = False, "overall_exhausted", "Использование закончилось"
        return {
            "plan": "ultra" if self.unlimited else self.plan,
            "trial": self.plan == "trial",
            "paid_active": self.paid or self.unlimited,
            "tester_credits": self.tester_credits,
            "tester_credit_active": self.tester_active,
            "remaining_percent": None if self.unlimited else self.remaining,
            "usage_exhausted": not self.unlimited and self.remaining <= 0,
            "expires_at": self.expires if self.paid else None,
            "catalog_version": self.catalog_version,
            "usage_reset": self.usage_reset(),
            "free_quotas": {},
            "developer": {"allowed": allowed, "reason_code": reason_code, "reason": reason, "window_percent": 35,
                          "window_used_percent": self.window_used, "window_resets_at": self.window_resets, "window_hours": 5},
        }

    def action(self, plan: str) -> str:
        if self.unlimited or not self.payment:
            return "unavailable"
        index = ORDER.index(plan)
        if self.current_index() == index:
            return "current"
        if index < self.current_index():
            return "included"
        return "subscribe" if self.current_index() < 0 else "upgrade"

    def plans(self) -> dict:
        self.calls["plans"] += 1
        items = []
        for plan in ORDER:
            price = PRICES[plan]
            full = round(price * (100 - self.discount) / 100, 2)
            action = self.action(plan)
            checkout = None
            if action == "subscribe":
                checkout = {"amount": full, "original_amount": full, "currency": "RUB"}
            elif action == "upgrade":
                checkout = {"amount": max(0.0, round(full - PRICES[ORDER[self.current_index()]], 2)), "original_amount": max(0.0, round(price - PRICES[ORDER[self.current_index()]], 2)), "currency": "RUB"}
            features = plan_features(plan)
            if self.evil_icon:
                features[0]["icon"]["svg"] = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" onload="window.__pwned=1"><script>window.__pwned=2</script><style>*{display:none}</style><use href="https://evil.example/x.svg#a"/><path d="M3 3h18" stroke="currentColor" onclick="window.__pwned=3"/></svg>'
            items.append({
                "id": plan, "name": NAMES[plan], "tagline": TAGLINES[plan], "action": action, "is_current": action == "current",
                "price": {"amount": full, "original_amount": price, "discount_percent": self.discount, "currency": "RUB", "period_months": 1},
                "checkout_price": checkout, "usage_multiplier": MULTIPLIER[plan], "features": features,
            })
        if self.extra_plan:
            items.append({
                "id": "mega", "name": "Mega", "tagline": "Новый план из каталога сервера.", "action": "subscribe", "is_current": False,
                "price": {"amount": 2000.0, "original_amount": 2000.0, "discount_percent": 0, "currency": "RUB", "period_months": 1},
                "checkout_price": {"amount": 2000.0, "original_amount": 2000.0, "currency": "RUB"}, "usage_multiplier": 20.0,
                "features": [feature("usage", "×20 объём использования", "Максимальный объём.", False), feature("early", "Закрытые функции", "Только для Mega.", False)],
            })
        return {
            "catalog_version": self.catalog_version, "currency": "RUB", "period_months": 1, "payment_available": self.payment,
            "included_heading": "Также включено:", "gates": {"developer": {"min_plan": "super", "min_plan_name": "Plus"}},
            "current": {"plan": self.current_plan_id(), "plan_name": NAMES.get(self.current_plan_id(), "Trial" if self.plan == "trial" else "Free"),
                        "paid_active": self.paid, "trial": self.plan == "trial", "unlimited": self.unlimited,
                        "expires_at": self.expires if self.paid else None, "remaining_percent": None if self.unlimited else self.remaining,
                        "renewal": {"available": self.paid and self.renewal, "enabled": self.renewal, "next_charge_at": self.expires if self.paid else None}},
            "usage_reset": self.usage_reset(), "plans": items,
        }

    # ссылки на оплату
    def create_link(self, body: dict) -> tuple[int, dict]:
        if self.fail_link:
            return self.fail_link
        link_id = f"bl_{len(self.links) + 1:04d}"
        self.links[link_id] = {"id": link_id, "intent": body.get("intent"), "plan": body.get("plan"), "percent": body.get("percent"), "status": "created"}
        self.created.append(body)
        return 201, {"id": link_id, "url": f"https://dokky.goshkow.com/pay/{'A' * 43}", "intent": body.get("intent"), "action": "upgrade",
                     "plan": body.get("plan"), "percent": body.get("percent"), "status": "created", "expires_at": "2026-10-18T15:30:00Z"}

    def link(self, link_id: str) -> tuple[int, dict]:
        item = self.links.get(link_id)
        if not item:
            return 404, {"detail": {"code": "not_found", "message": "Ссылка не найдена"}}
        return 200, {k: item[k] for k in ("id", "intent", "status", "plan", "percent")} | {"expires_at": "2026-10-18T15:30:00Z"}

    def finish(self, status: str = "paid") -> None:
        """Человек закончил оплату на сайте: для `paid` подписка меняется так, как это сделал бы сервер."""
        for item in self.links.values():
            if item["status"] in {"created", "opened", "awaiting_payment"}:
                item["status"] = status
                if status == "paid":
                    if item["intent"] == "plan":
                        self.plan = "super" if item["plan"] in {"plus", "super"} else item["plan"]
                        self.remaining = max(self.remaining, 100)
                    else:
                        self.remaining += item.get("percent") or 100

    # подключение к подставному API
    def install(self, api) -> "Billing":
        api.extra["/api/dokky/cloud/subscription"] = lambda m, p, b, q: self.subscription()
        api.extra["/api/dokky/cloud/billing/plans"] = lambda m, p, b, q: (self.catalog_error or self.plans())
        api.extra["/api/dokky/cloud/billing/links"] = lambda m, p, b, q: self.create_link(b or {})
        api.extra["/api/dokky/cloud/billing/links/*"] = lambda m, p, b, q: self.link(p.rsplit("/", 1)[1])
        previous = api.session

        def session():
            value = previous()
            models = [item for item in value["bootstrap"]["models"] if item.get("id") != "dokky-code"]
            code = {"id": "dokky-code", "name": "Докки Code", "roles": ["development"], "available": self.developer_allowed()}
            if not code["available"]:
                code["unavailable_reason"] = "plus_required"
            value["bootstrap"] = {**value["bootstrap"], "models": models + [code]}
            return value

        api.session = session
        return self


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
