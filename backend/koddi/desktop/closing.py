"""Что делает закрытие окна: спрятать в трей или завершить программу."""

from __future__ import annotations

HIDE = "hide"
QUIT = "quit"

HINT_SETTING = "tray_hint_shown"


def close_action(*, user_close: bool, quitting: bool, close_to_tray: bool, has_tray: bool) -> str:
    """Прячем только закрытие, которое попросил человек (крестик, Cmd+W): выход из программы, выход из системы
    и обновление тоже шлют окну closeEvent, но окно при них должно закрыться."""
    if user_close and not quitting and close_to_tray and has_tray:
        return HIDE
    return QUIT
