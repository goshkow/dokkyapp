from __future__ import annotations

import re

from koddi.storage.db import get_setting, set_setting
from koddi.tools.schemas import AGENT_UNAVAILABLE_TOOLS, MODE_TOOL_NAMES, TOOL_SCHEMAS_BY_NAME


BUILTIN_MODES = {
    "chat": "Чат",
    "development": "Разработка",
    "plan": "Планирование",
    "debug": "Отладка",
    "review": "Проверка",
}


def list_modes() -> list[dict]:
    return [
        {
            "id": mode,
            "name": name,
            "builtin": True,
            "tools": sorted(set(MODE_TOOL_NAMES.get(mode, [])) - AGENT_UNAVAILABLE_TOOLS),
        }
        for mode, name in BUILTIN_MODES.items()
    ] + get_setting("custom_modes", [])


def save_mode(mode: dict) -> dict:
    if (
        not re.fullmatch(r"[a-z][a-z0-9_-]{1,40}", mode.get("id", ""))
        or mode["id"] in BUILTIN_MODES
    ):
        raise ValueError("Укажите уникальный идентификатор режима")
    if not isinstance(mode.get("name"), str) or not mode["name"].strip():
        raise ValueError("Укажите название режима")
    if not isinstance(mode.get("instructions"), str):
        raise ValueError("Укажите инструкции режима")
    if not isinstance(mode.get("tools"), list) or any(
        name not in TOOL_SCHEMAS_BY_NAME or name in AGENT_UNAVAILABLE_TOOLS
        for name in mode["tools"]
    ):
        raise ValueError("В режиме указаны неизвестные инструменты")
    value = {key: mode[key] for key in ("id", "name", "instructions", "tools")}
    modes = [m for m in get_setting("custom_modes", []) if m["id"] != mode["id"]]
    set_setting("custom_modes", [*modes, value])
    return value
