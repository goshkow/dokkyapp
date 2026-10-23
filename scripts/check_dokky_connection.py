"""Проверка авторизации и доступности API без вызова модели и списаний."""

import asyncio

from koddi.dokky.client import DokkyError, dokky
from koddi.storage import init_db


async def main():
    init_db()
    if not dokky.store.load():
        print("Вход в Докки ещё не выполнен. Платные запросы не отправлялись.")
        return 1
    try:
        models = (await dokky.request("GET", "/models")).json().get("data", [])
        coding = next((item for item in models if item.get("id") == "dokky-code"), None)
        print("Авторизация и список моделей: доступны.")
        if not coding or coding.get("available") is False:
            print("Модель разработки dokky-code не выдана сервером.")
            return 1
        print("Контекст разработки:", coding.get("context_tokens", "не указан"))
        print("Режимы:", ", ".join(item["id"] for item in coding.get("reasoning_modes", [])))
        await dokky.request("GET", "/coding/sessions", params={"limit": 1})
        print("Сессии разработки: доступны. История и идентификаторы не выводились.")
        print("Генерация не запускалась, платные запросы не отправлялись.")
        return 0
    except DokkyError as exc:
        print(f"Проверка не пройдена: HTTP {exc.status}, код {exc.code}.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
