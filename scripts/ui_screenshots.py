"""Каталог скриншотов интерфейса на подставном API: планы, оплата, блокировка Докки Code, подсказки, темы, настройки.

Запуск: python scripts/ui_screenshots.py [папка]  (по умолчанию _screens). Рядом кладутся index.md и листы-обзоры sheet-*.png.
Живой сервер не нужен: ответы подписки и каталога строит scripts/billing_mock.py по описанию API v1 из репозитория dokky.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import smoke_next as sm  # noqa: E402
from billing_mock import Billing  # noqa: E402
from ui_testkit import browser_page  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "_screens"
OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()
shots: list[tuple[str, str]] = []
WIDE = (1300, 860)
PHONE = (420, 860)


def shot(page, title: str) -> None:
    name = f"{len(shots) + 1:03d}.png"
    page.wait_for_timeout(350)
    page.screenshot(path=str(OUT / name))
    shots.append((name, title))
    print(name, title, flush=True)


def configure(api, theme="dark", **settings):
    api.settings["theme"] = theme
    api.settings.update(settings)
    return api


def dev(plan, theme="dark", settings=None, **options):
    api = configure(sm.development_api(), theme, **(settings or {}))
    return api, Billing(plan, **options).install(api)


def chat(plan, theme="dark", settings=None, **options):
    api = configure(sm.cloud_api(), theme, **(settings or {}))
    return api, Billing(plan, **options).install(api)


def settings(page, section):
    if not page.locator("dialog.settings-window[open]").count():
        page.click("#connection-badge .account-name")
        page.wait_for_selector("dialog.settings-window[open]")
    page.click(f"[data-settings-section={section}]")
    page.wait_for_timeout(700)


def pay(page, mock, finish=None):
    page.evaluate(sm.OPEN_STUB)
    page.click(".plan-buy")
    page.wait_for_selector(".payment-body[data-stage=waiting]", timeout=6000)
    if finish:
        mock.finish(finish)
        page.wait_for_selector(f".payment-body[data-stage={'paid' if finish == 'paid' else finish}]", timeout=9000)


def run() -> None:
    plans = [("free", "Free", {"remaining": 100}), ("trial", "Пробный период", {"remaining": 100}), ("base", "Base", {"remaining": 70}),
             ("super", "Plus", {"remaining": 64}), ("premium", "Premium", {"remaining": 80}), ("ultra", "Ultra", {"remaining": 90}), ("admin", "Админ", {})]

    # 1. Докки Code на разных тарифах: приглашение и чтение старого чата
    for plan, label, options in plans[:3]:
        for theme in ("dark", "light"):
            api, _ = dev(plan, theme, **options)
            with browser_page(api, viewport=WIDE) as page:
                page.wait_for_timeout(1500)
                shot(page, f"Докки Code, {label}, {theme}: приглашение вместо поля ввода")
                if theme == "dark":
                    sm.open_chat(page)
                    shot(page, f"Докки Code, {label}: старый чат читается, писать нельзя")
    for plan, label, options in (plans[3], plans[4], plans[5], plans[6]):
        api, _ = dev(plan, **options)
        with browser_page(api, viewport=WIDE) as page:
            page.wait_for_timeout(1500)
            shot(page, f"Докки Code, {label}: полный доступ")
    api, _ = dev("base", tester_credits=45, remaining=60)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Докки Code, Base с кредитами тестера: доступ открыт")
    for plan, label in (("free", "Free"), ("base", "Base")):
        api, _ = dev(plan, remaining=100)
        with browser_page(api, viewport=PHONE) as page:
            page.wait_for_timeout(1500)
            shot(page, f"Телефонная ширина, {label}: приглашение Докки Code")

    # 2. Подписка и оплата: страница на каждом тарифе
    for plan, label, options in plans:
        api, _ = chat(plan, **options)
        with browser_page(api, viewport=(1300, 900)) as page:
            page.wait_for_timeout(1500)
            settings(page, "billing")
            shot(page, f"Подписка и оплата, {label}")
            if plan in {"free", "base", "super", "premium"}:
                page.click(".plan-tabs button >> nth=-1")
                page.wait_for_timeout(400)
                shot(page, f"Подписка и оплата, {label}: вкладка старшего плана (улучшение)")
            if plan in {"free", "super"}:
                page.evaluate("document.querySelector('#settings-body').scrollTo(0, 99999)")
                shot(page, f"Подписка и оплата, {label}: нижняя часть со списком функций")
    for plan, label in (("free", "Free"), ("super", "Plus"), ("ultra", "Ultra")):
        api, _ = chat(plan, "light", remaining=64)
        with browser_page(api, viewport=(1300, 900)) as page:
            page.wait_for_timeout(1500)
            settings(page, "billing")
            shot(page, f"Подписка и оплата, {label}, светлая тема")
    api, _ = chat("super", discount=20, remaining=64)
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        page.click(".plan-tabs button[data-plan-tab=ultra]")
        shot(page, "Подписка и оплата: скидка -20% и зачёркнутая цена")
    api, _ = chat("free", payment=False, remaining=100)
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        shot(page, "Подписка и оплата: оплата временно недоступна")
    api, _ = chat("super", extra_plan=True, remaining=64)
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        page.click(".plan-tabs button[data-plan-tab=mega]")
        shot(page, "Подписка и оплата: новый план из каталога сервера (Mega)")
    api, mock = chat("free", remaining=100)
    mock.catalog_error = (503, {"detail": {"code": "upstream_unavailable", "message": "Сервер подписок недоступен"}})
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        shot(page, "Подписка и оплата: каталог не загрузился")

    # 3. Сброс использования
    for plan, label, remaining in (("super", "Plus", 7), ("base", "Base", 5), ("premium", "Premium", 9), ("ultra", "Ultra", 3)):
        api, _ = chat(plan, remaining=remaining)
        with browser_page(api, viewport=(1300, 900)) as page:
            page.wait_for_timeout(1500)
            settings(page, "billing")
            shot(page, f"Сброс использования, {label}, осталось {remaining}%: +100% станет {remaining + 100}%")
            page.click("[data-testid=usage-reset] button[data-percent='25']")
            shot(page, f"Сброс использования, {label}: выбрано +25%")
    api, _ = chat("super", "light", remaining=7)
    with browser_page(api, viewport=(1300, 900)) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        shot(page, "Сброс использования, Plus, светлая тема")

    # 4. Экран оплаты
    api, mock = chat("super", remaining=64)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        page.click(".plan-tabs button[data-plan-tab=ultra]")
        pay(page, mock)
        shot(page, "Оплата: ждём оплату на сайте")
        mock.finish("paid")
        page.wait_for_selector(".payment-body[data-stage=paid]", timeout=9000)
        shot(page, "Оплата: план включён")
    api, mock = chat("super", "light", remaining=64)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        page.click(".plan-tabs button[data-plan-tab=premium]")
        pay(page, mock)
        shot(page, "Оплата, светлая тема: ждём оплату")
        mock.finish("canceled")
        page.wait_for_selector(".payment-body[data-stage=canceled]", timeout=9000)
        shot(page, "Оплата: не прошла, деньги не списаны")
        page.click("text=Попробовать снова")
        page.wait_for_selector(".payment-body[data-stage=waiting]")
        mock.finish("expired")
        page.wait_for_selector(".payment-body[data-stage=expired]", timeout=9000)
        shot(page, "Оплата: ссылка устарела")
    api, mock = chat("super", remaining=7)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        settings(page, "billing")
        page.evaluate(sm.OPEN_STUB)
        page.click(".usage-reset-pay")
        page.wait_for_selector(".payment-body[data-stage=waiting]", timeout=6000)
        shot(page, "Оплата: ждём пополнение использования")
        mock.finish("paid")
        page.wait_for_selector(".payment-body[data-stage=paid]", timeout=9000)
        shot(page, "Оплата: использование пополнено")
    for status, code, reason, retry, title in (
        (409, "conflict", "already_included", None, "план уже включён"),
        (409, "conflict", "no_paid_plan", None, "нужна платная подписка"),
        (429, "rate_limited", None, 12, "слишком часто"),
        (503, "upstream_unavailable", None, None, "оплата временно недоступна"),
    ):
        api, mock = chat("free", remaining=100)
        detail = {"code": code, "message": "сервер", **({"reason": reason} if reason else {}), **({"retry_after_seconds": retry} if retry else {})}
        mock.fail_link = (status, {"detail": detail})
        with browser_page(api, viewport=WIDE) as page:
            page.wait_for_timeout(1500)
            settings(page, "billing")
            page.click(".plan-buy")
            page.wait_for_selector(".payment-body[data-stage=failed]", timeout=6000)
            shot(page, f"Оплата: отказ сервера, {title}")

    # 5. Подсказки про использование
    for plan, label, scope, options, title in (
        ("super", "Plus", "chat", {"remaining": 7}, "осталось 7%, чат"),
        ("base", "Base", "chat", {"remaining": 4}, "осталось 4%, Base"),
        ("super", "Plus", "chat", {"remaining": 0}, "использование закончилось"),
        ("free", "Free", "chat", {"remaining": 0}, "бесплатное использование закончилось"),
        ("super", "Plus", "dev", {"remaining": 80, "window_used": 31}, "окно 5 часов заканчивается"),
        ("super", "Plus", "dev", {"remaining": 80, "window_used": 35}, "окно 5 часов исчерпано"),
        ("premium", "Premium", "dev", {"remaining": 80, "window_used": 35}, "окно исчерпано, можно улучшить"),
        ("ultra", "Ultra", "dev", {"remaining": 80, "window_used": 35}, "окно исчерпано, улучшать некуда"),
    ):
        api, _ = (dev if scope == "dev" else chat)(plan, **options)
        with browser_page(api, viewport=WIDE) as page:
            page.wait_for_timeout(1500)
            shot(page, f"Подсказка, {label}, {title}")
    api, _ = chat("super", "light", remaining=7)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Подсказка, светлая тема: осталось 7%")
    api, _ = chat("super", remaining=7)
    with browser_page(api, viewport=PHONE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Подсказка, телефонная ширина")

    # 6. Меню аккаунта и кружок лимитов на каждом тарифе
    for plan, label, options in plans:
        api, _ = chat(plan, **options)
        with browser_page(api, viewport=WIDE) as page:
            page.wait_for_timeout(1500)
            page.click("#account-menu-toggle")
            page.wait_for_timeout(500)
            shot(page, f"Меню аккаунта и кружок лимитов, {label}")
    api, _ = chat("super", remaining=7)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        page.click("#account-menu-toggle")
        shot(page, "Меню аккаунта: есть «Сбросить использование»")

    # 7. Настройки на Plus и разное оформление
    sections = [("general", "Общие"), ("account", "Аккаунт"), ("usage", "Использование"), ("conversation", "Общение"), ("development", "Разработка"), ("system", "Система"), ("about", "О приложении")]
    for theme in ("dark", "light"):
        api, _ = chat("super", theme, remaining=64)
        with browser_page(api, viewport=(1300, 900)) as page:
            page.wait_for_timeout(1500)
            for section, label in sections:
                settings(page, section)
                shot(page, f"Настройки, {label}, {theme}")
    for name, values in (("тёмное меню", {"ui_surface": "side-dark"}), ("тёмный чат", {"ui_surface": "chat-dark"}), ("системный шрифт", {"ui_font": "system"}),
                         ("крупный текст", {"text_size": 17}), ("узкий чат", {"chat_width": "compact"}), ("широкий чат", {"chat_width": "wide"})):
        api, _ = chat("super", settings=values, remaining=64)
        with browser_page(api, viewport=WIDE) as page:
            page.wait_for_timeout(1500)
            sm_open = page.locator("text=Машины Атом")
            sm_open.click()
            page.wait_for_timeout(1200)
            shot(page, f"Чат с ответом, оформление: {name}")

    # 8. Разные типы чатов
    api, _ = chat("super", remaining=64)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Чат: новый чат Докки")
        page.click("#product-search")
        page.wait_for_timeout(900)
        shot(page, "Поиск: новый запрос")
    api = configure(sm.cloud_api(), "dark")
    Billing("super", remaining=64).install(api)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        page.click("text=Машины Атом")
        page.wait_for_timeout(1200)
        page.click(".work-progress-toggle")
        page.wait_for_timeout(500)
        shot(page, "Чат: ответ с источниками, файлом и ходом работы")
    api = configure(sm.cloud_api(), "dark")
    Billing("super", remaining=64).install(api)
    content = "Формула: $$E = mc^2$$\n\nВнутри строки \\(x^2 + 1\\)."
    api.extra["/api/dokky/cloud/chats/chat_1/messages"] = lambda m, p, b, q: {"items": [
        {"id": "msg_u", "role": "user", "content": "формулу и места", "created_at": "2026-10-17T10:00:00+00:00", "attachments": [], "artifacts": [], "activity": [], "citations": [], "media": [], "blocks": []},
        {"id": "msg_a", "role": "assistant", "content": content, "content_format": "dokky-md/1", "has_math": True, "created_at": "2026-10-17T10:00:09+00:00", "status": "completed", "attachments": [], "artifacts": [], "activity": [], "citations": [], "media": [],
         "blocks": [{"type": "place_map", "title": "Места", "places": [{"name": "Красная площадь", "lat": 55.7539, "lon": 37.6208}]}]}], "next_cursor": None}
    with browser_page(api, viewport=WIDE) as page:
        page.route("**/api/dokky/tile/**", lambda r: r.fulfill(status=200, content_type="image/png", body=b"\x89PNG\r\n\x1a\n"))
        page.wait_for_timeout(1500)
        page.click("text=Машины Атом")
        page.wait_for_timeout(2200)
        shot(page, "Чат: формулы и карта мест")
    api, _ = dev("super", remaining=80)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Докки Code, Plus: новый чат с полем ввода")
        sm.open_chat(page)
        shot(page, "Докки Code, Plus: чат с ходом работы и изменениями")
    api, _ = dev("super", "light", remaining=80)
    with browser_page(api, viewport=WIDE) as page:
        page.wait_for_timeout(1500)
        sm.open_chat(page)
        shot(page, "Докки Code, Plus, светлая тема: чат")
    api, _ = chat("super", remaining=64)
    with browser_page(api, viewport=PHONE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Телефонная ширина: чат Докки")
    api, _ = dev("super", remaining=80)
    with browser_page(api, viewport=PHONE) as page:
        page.wait_for_timeout(1500)
        shot(page, "Телефонная ширина: Докки Code, Plus")
    for theme in ("dark", "light"):
        api, _ = chat("super", theme, remaining=64)
        with browser_page(api, viewport=(900, 700)) as page:
            page.wait_for_timeout(1500)
            settings(page, "billing")
            shot(page, f"Подписка и оплата в узком окне, {theme}")


def sheets() -> None:
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 15)
    except OSError:
        font = ImageFont.load_default()

    per, cols = 12, 3
    thumb = (560, 380)
    for index in range(0, len(shots), per):
        chunk = shots[index:index + per]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (thumb[0] + 16) + 16, rows * (thumb[1] + 46) + 16), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for position, (name, title) in enumerate(chunk):
            image = Image.open(OUT / name).convert("RGB")
            image.thumbnail(thumb)
            x = 16 + (position % cols) * (thumb[0] + 16)
            y = 16 + (position // cols) * (thumb[1] + 46)
            sheet.paste(image, (x, y + 24))
            draw.text((x, y + 2), f"{name[:3]}  {title}"[:70], fill=(235, 235, 235), font=font)
        sheet.save(OUT / f"sheet-{index // per + 1:02d}.png")
    (OUT / "index.md").write_text("# Скриншоты интерфейса\n\n" + "\n".join(f"- `{name}` - {title}" for name, title in shots) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
    sheets()
    print(f"готово: {len(shots)} скриншотов в {OUT}")
