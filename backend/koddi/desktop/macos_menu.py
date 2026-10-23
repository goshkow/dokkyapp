"""Строка меню macOS: только то, что поддерживает приложение. Команды выполняет страница (`window.koddiMenu`)."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

APP_NAME = "Докки"
SITE = "https://dokky.goshkow.com"
_translators: list[QTranslator] = []


def set_process_name() -> None:
    """Название в строке меню и Dock берётся из Info.plist; при запуске из исходников там «Python»."""
    from Foundation import NSBundle

    from Foundation import NSProcessInfo

    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = APP_NAME
    NSProcessInfo.processInfo().setProcessName_(APP_NAME)


def install_qt_translations(extra_dirs: list[Path] = ()) -> None:
    """Системные пункты меню («Скрыть», «Завершить», «Службы») Qt переводит из своих словарей."""
    directories = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath), *map(str, extra_dirs)]
    for name in ("qtbase_ru",):
        translator = QTranslator()
        if any(translator.load(name, directory) for directory in directories):
            QCoreApplication.installTranslator(translator)
            _translators.append(translator)


def install(host) -> None:
    bar = host.menuBar()
    page = host.view.page()

    def command(action: str):
        return lambda *_: host.menu_action(action)

    def add(menu, title, handler, shortcut=None, role=None):
        item = QAction(title, host)
        if shortcut:
            item.setShortcut(QKeySequence(shortcut))
        if role is not None:
            item.setMenuRole(role)
        item.triggered.connect(handler)
        menu.addAction(item)
        return item

    def page_action(menu, title, web_action, key):
        item = QAction(title, host)
        item.setShortcut(QKeySequence(key))
        item.triggered.connect(lambda *_: page.triggerPageAction(web_action))
        menu.addAction(item)

    Role = QAction.MenuRole
    file = bar.addMenu("Файл")
    add(file, "Новый чат", command("new-chat"), "Ctrl+N")
    add(file, "Открыть проект…", lambda *_: host.open_project_folder(), "Ctrl+O")
    file.addSeparator()
    add(file, "Закрыть окно", host.close, "Ctrl+W")
    # пункты с ролями Qt переносит в меню приложения «Докки»
    add(file, f"О программе {APP_NAME}", command("about"), role=Role.AboutRole)
    add(file, "Настройки…", command("settings"), "Ctrl+,", Role.PreferencesRole)
    add(file, "Тариф и оплата…", command("billing"), role=Role.ApplicationSpecificRole)
    add(file, "Выход из аккаунта…", command("sign-out"), role=Role.ApplicationSpecificRole)
    add(file, f"Завершить {APP_NAME}", QApplication.quit, "Ctrl+Q", Role.QuitRole)
    # без своего пункта Qt оставляет в меню приложения стандартный «About Qt»
    add(file, "О Qt", lambda *_: None, role=Role.AboutQtRole).setVisible(False)

    edit = bar.addMenu("Правка")
    for title, action, key in (
        ("Отменить", QWebEnginePage.WebAction.Undo, QKeySequence.StandardKey.Undo),
        ("Повторить", QWebEnginePage.WebAction.Redo, QKeySequence.StandardKey.Redo),
        (None, None, None),
        ("Вырезать", QWebEnginePage.WebAction.Cut, QKeySequence.StandardKey.Cut),
        ("Копировать", QWebEnginePage.WebAction.Copy, QKeySequence.StandardKey.Copy),
        ("Вставить", QWebEnginePage.WebAction.Paste, QKeySequence.StandardKey.Paste),
        ("Выделить всё", QWebEnginePage.WebAction.SelectAll, QKeySequence.StandardKey.SelectAll),
    ):
        if title is None:
            edit.addSeparator()
        else:
            page_action(edit, title, action, key)

    view = bar.addMenu("Вид")
    add(view, "Главное меню", command("toggle-left"), "Ctrl+Alt+S")
    add(view, "Изменения и файлы", command("toggle-right"), "Ctrl+Alt+I")
    view.addSeparator()
    add(view, "Крупнее текст", command("text-bigger"), "Ctrl+=")
    add(view, "Мельче текст", command("text-smaller"), "Ctrl+-")
    add(view, "Обычный размер текста", command("text-reset"), "Ctrl+0")
    view.addSeparator()
    add(view, "Во весь экран", host.toggle_fullscreen, "Ctrl+Meta+F")
    if host.glass is not None:
        view.addSeparator()
        add(view, "Настройка стекла…", lambda *_: host.open_glass_tuner(), "Ctrl+Alt+G")

    go = bar.addMenu("Переход")
    add(go, "Назад", command("back"), "Ctrl+[")
    add(go, "Вперёд", command("forward"), "Ctrl+]")
    go.addSeparator()
    add(go, "Чат", command("scope-chat"), "Ctrl+1")
    add(go, "Поиск", command("scope-search"), "Ctrl+2")
    add(go, "Докки Code", command("scope-development"), "Ctrl+3")
    go.addSeparator()
    add(go, "Библиотека", command("page-library"))
    for title, action in (
        ("Проекты", "page-projects"), ("Задачи", "page-mission"), ("Автоматизации", "page-automations"),
        ("Проверка изменений", "page-review"), ("Навыки", "page-skills"), ("Источники", "page-sources"),
        ("Журнал событий", "page-logs"),
    ):
        add(go, title, command(action))

    window = bar.addMenu("Окно")
    add(window, "Свернуть", host.showMinimized, "Ctrl+M")
    add(window, "Увеличить", host.toggle_maximized)

    help_menu = bar.addMenu("Справка")
    add(help_menu, f"Сайт {APP_NAME}", lambda *_: QDesktopServices.openUrl(QUrl(SITE)))
    add(help_menu, "Лицензии и сведения о программе", command("about"))


def dispatch_script(action: str) -> str:
    return f"window.koddiMenu?.({json.dumps(action)})"
