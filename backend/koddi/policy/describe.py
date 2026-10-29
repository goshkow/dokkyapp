"""Понятное человеку описание действия агента: что сделать, с чем, и короткие подробности.

Его видит человек в запросе разрешения и проверка Докки в режиме «Автоматически»; сырые аргументы в JSON не показываются.
"""

from __future__ import annotations

from typing import Any

MAX_DETAIL = 1200

TITLES = {
    "file.read": "Прочитать файл",
    "file.read_many": "Прочитать файлы",
    "file.read_range": "Прочитать часть файла",
    "file.read_chunk": "Прочитать часть файла",
    "file.metadata": "Посмотреть сведения о файле",
    "file.create": "Создать файл",
    "file.write_patch": "Записать файл",
    "file.edit": "Изменить файл",
    "file.search_replace": "Изменить файл",
    "file.delete": "Удалить файл",
    "file.move": "Переместить файл",
    "file.copy": "Скопировать файл",
    "file.restore": "Откатить изменения",
    "changes.rollback": "Откатить изменения",
    "patch.apply": "Применить правки к файлам",
    "shell.run": "Выполнить команду",
    "test.run": "Запустить тесты",
    "qa.run": "Запустить проверки проекта",
    "quality.run": "Проверить качество кода",
    "ui.verify": "Проверить страницу в браузере",
    "build.run": "Запустить сборку",
    "lint.run": "Запустить проверку кода",
    "format.run": "Отформатировать код",
    "process.kill": "Завершить процесс",
    "git.add": "Добавить файлы в индекс Git",
    "git.commit": "Сделать коммит",
    "git.checkout": "Переключить ветку Git",
    "git.create_branch": "Создать ветку Git",
    "git.restore_file": "Отменить изменения файла в Git",
    "git.worktree_create": "Создать рабочую копию Git",
    "git.worktree_remove": "Удалить рабочую копию Git",
    "browser.open": "Открыть страницу в браузере агента",
    "browser.click": "Нажать на элемент страницы",
    "browser.type": "Ввести текст на странице",
    "browser.screenshot": "Сделать снимок страницы",
    "browser.dom": "Прочитать содержимое страницы",
    "browser.console": "Прочитать консоль страницы",
    "browser.network": "Прочитать сетевые запросы страницы",
    "devserver.start": "Запустить сервер разработки",
    "devserver.stop": "Остановить сервер разработки",
    "terminal.open": "Открыть терминал в папке проекта",
    "ide.open_file": "Открыть файл в редакторе",
    "ide.open_diff": "Показать изменения в редакторе",
    "memory.set": "Запомнить заметку для следующих задач",
    "project.map_update": "Обновить карту проекта",
    "skill.apply": "Применить навык",
    "skill.create": "Создать навык",
    "automation.create": "Создать автоматизацию",
    "automation.run": "Запустить автоматизацию",
    "workspace.snapshot": "Сделать резервную копию проекта",
    "workspace.open": "Открыть проект",
    "workspace.create": "Создать проект",
    "computer.use": "Управлять приложением на компьютере",
    "mcp.tool_call": "Вызвать внешний инструмент (MCP)",
}

# у этих инструментов главное - путь, остальное идёт в подробности
PATH_KEYS = ("path", "source", "destination", "url", "branch", "pid", "server", "name", "app")


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split()) if "\n" not in text else text
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def describe_action(name: str, args: dict[str, Any] | None, *, external: list[str] | None = None,
                    dangerous: bool = False) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    title = TITLES.get(name) or f"Выполнить действие «{name}»"
    target = ""
    detail = ""
    kind = "other"
    if name.startswith(("file.", "patch.")) or name in {"changes.rollback", "workspace.snapshot"}:
        kind = "file"
    if name in {"shell.run", "test.run", "build.run", "lint.run", "format.run", "devserver.start"}:
        kind = "command"
        detail = str(args.get("command", ""))
        target = str(args.get("cwd", "")) or ""
    elif name == "qa.run":
        kind = "command"
        detail = "lint, типы и тесты проекта" + (" (весь проект)" if args.get("scope") == "full" else " для изменённых файлов")
    elif name == "quality.run":
        kind = "command"
        gates = [str(item) for item in args.get("gates") or []]
        detail = ("мутационное тестирование в изолированной копии проекта" if "mutation" in gates or not gates
                  else "сбор покрытия тестами проекта" if args.get("coverage") == "collect" else "разбор кода")
        detail += " (весь проект)" if args.get("scope") == "full" else " для изменённых файлов"
    elif name in {"file.move", "file.copy"}:
        target = f"{args.get('source', '')} → {args.get('destination', '')}"
    elif name == "file.read_many":
        target = ", ".join(str(item) for item in args.get("paths", [])[:8])
    elif name in {"file.create", "file.write_patch"}:
        target = str(args.get("path", ""))
        content = str(args.get("content", ""))
        detail = f"{_lines(content)} строк, {len(content.encode('utf-8'))} байт"
    elif name in {"file.edit", "file.search_replace"}:
        target = str(args.get("path", ""))
        edits = args.get("edits") or [args]
        parts = []
        for edit in edits[:3]:
            if isinstance(edit, dict) and "old_text" in edit:
                parts.append(f"заменить «{_short(edit.get('old_text', ''), 120)}» на «{_short(edit.get('new_text', ''), 120)}»")
        detail = "\n".join(parts) + (f"\nи ещё правок: {len(edits) - 3}" if len(edits) > 3 else "")
    elif name == "patch.apply":
        from koddi.tools.patches import parse_patch

        try:
            files = sorted({p.destination or p.source for p in parse_patch(str(args.get("patch", ""))) if p.destination or p.source})
        except Exception:  # описание не должно падать на кривом патче, его отклонит сам инструмент
            files = []
        target = ", ".join(files[:8])
    elif name == "git.commit":
        detail = str(args.get("message", ""))
    elif name == "git.add":
        target = ", ".join(str(item) for item in args.get("paths", ["."]))
    elif name == "browser.type":
        target = str(args.get("selector", ""))
        detail = f"текст: «{_short(args.get('text', ''), 120)}»"
    elif name == "mcp.tool_call":
        target = f"{args.get('server', '')} / {args.get('name', '')}"
    elif name == "memory.set":
        detail = _short(args.get("content") or args.get("value") or "", 300)
    else:
        target = next((str(args[key]) for key in PATH_KEYS if args.get(key) not in (None, "")), "")
    if not detail and name not in TITLES:
        # неизвестный инструмент: аргументы строками «имя: значение», без JSON
        detail = "\n".join(f"{key}: {_short(value, 160)}" for key, value in list(args.items())[:8])
    return {
        "title": title,
        "target": _short(target, 400),
        # команда целиком: одобряют то, что выполнится; размер ограничен проверкой аргументов
        "detail": detail if kind == "command" else detail[:MAX_DETAIL],
        "kind": kind,
        "outside_project": list(external or []),
        "dangerous": dangerous,
    }
