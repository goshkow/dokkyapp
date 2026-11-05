"""Понятное человеку описание действия агента: что сделать, с чем, и короткие подробности.

Его видит человек в запросе разрешения и проверка Докки в режиме «Автоматически»; сырые аргументы в JSON не показываются.
"""

from __future__ import annotations

from typing import Any

MAX_DETAIL = 1200

TITLES = {
    "skill.load": "Подключить навык из плагина",
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
    "git.push": "Отправить коммиты на сервер (git push)",
    "git.pull": "Подтянуть изменения с сервера (git pull)",
    "git.merge": "Слить ветку (git merge)",
    "git.stash": "Отложить изменения (git stash)",
    "github.pr_create": "Создать pull request на GitHub",
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
    "devserver.output": "Посмотреть вывод фоновой команды",
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
    elif name == "web.fetch":
        kind = "network"
        title = "Открыть страницу в интернете"
        target = str(args.get("url", ""))
        detail = "Адрес уйдёт этому сайту. Если разрешить, сайт больше не будет спрашиваться в этом чате."
    elif name == "cloud.video":
        kind = "network"
        title = "Разобрать видео в облаке Докки"
        target = str(args.get("url", ""))
        detail = str(args.get("question") or "")[:300] + "\nРасход - из лимита режима разработки."
    elif name in {"cloud.image", "cloud.document"}:
        kind = "network"
        title = "Создать картинку в облаке Докки" if name == "cloud.image" else "Сделать документ в облаке Докки"
        sent = args.get("references") if name == "cloud.image" else args.get("inputs")
        target = ", ".join(str(item) for item in sent[:5]) if isinstance(sent, list) else ""
        what = str(args.get("prompt") if name == "cloud.image" else args.get("instruction") or "")
        detail = (what[:300] + ("…" if len(what) > 300 else "")
                  + ("\nФайлы проекта уйдут на сервер Докки." if target else "")
                  + "\nРасход - из лимита режима разработки.")
    elif name == "skill.load":
        target = str(args.get("name", ""))
        detail = "Агент предлагает навык из установленного вами плагина. Навык добавляет способ работы, но не права."
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
    elif name in {"git.push", "git.pull"}:
        target = " ".join(str(args[key]) for key in ("remote", "branch") if args.get(key))
    elif name == "git.merge":
        target = str(args.get("branch", "")) or ("отменить слияние" if args.get("abort") else "")
    elif name == "github.pr_create":
        target = str(args.get("title", ""))
        detail = _short(args.get("body") or "", 300)
    elif name == "git.add":
        target = ", ".join(str(item) for item in args.get("paths", ["."]))
    elif name == "browser.type":
        target = str(args.get("selector", ""))
        detail = f"текст: «{_short(args.get('text', ''), 120)}»"
    elif name == "computer.use":
        command = str(args.get("command", ""))
        app = str(args.get("app") or "")
        title = f"Работать с приложением {app}" if app else title
        target = app or str(args.get("ref") or args.get("window_id") or "")
        parts = [f"действие: {command}"] if command else []
        if args.get("text"):
            parts.append(f"текст: «{_short(args['text'], 120)}»")
        if args.get("combo"):
            parts.append(f"клавиши: {args['combo']}")
        if command == "batch":
            parts.append(f"шагов: {len(args.get('items') or [])}")
        detail = "\n".join(parts)
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
