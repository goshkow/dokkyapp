"""Скрытый запуск дочерних процессов на Windows.

Приложение собрано без консоли, поэтому любая консольная программа (git, PowerShell, node, драйвер Playwright,
MCP-сервер по stdio) получает от Windows своё окно, если не передать CREATE_NO_WINDOW. Флаг ставится всем
вызовам subprocess, в том числе из библиотек и asyncio; окно остаётся только у того, кто явно попросил
новую консоль (CREATE_NEW_CONSOLE) или отцепился от неё (DETACHED_PROCESS).
"""

from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010
_EXPLICIT = DETACHED_PROCESS | CREATE_NEW_CONSOLE


def hidden_flags(flags: int = 0) -> int:
    """Флаги запуска без окна; явный запрос новой консоли не трогаем."""
    if os.name != "nt" or flags & _EXPLICIT:
        return flags
    return flags | CREATE_NO_WINDOW


def install() -> None:
    if os.name != "nt" or getattr(subprocess.Popen, "_dokky_hidden", False):
        return
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        # creationflags - 14-й позиционный аргумент Popen, так его никто не передаёт
        if len(args) < 14:
            kwargs["creationflags"] = hidden_flags(int(kwargs.get("creationflags") or 0))
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._dokky_hidden = True
