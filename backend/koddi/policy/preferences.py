from __future__ import annotations

import os

from koddi.policy.grants import current_owner
from koddi.storage.db import get_setting, set_setting


MODES = {"light", "pro", "max"}


def reasoning_mode() -> str:
    owner = current_owner()
    value = get_setting("coding_reasoning_mode:" + owner)
    # прежний общий выбор наследует только аккаунт, которому он принадлежал при обновлении
    if value is None and (
        get_setting("coding_preferences_legacy_owner") == owner
        or os.getenv("KODDI_INTERNAL_TEST") == "1"
    ):
        value = get_setting("coding_reasoning_mode")
    return value if isinstance(value, str) and value in MODES else "pro"


def save_reasoning_mode(mode: str, *, owner: str | None = None) -> None:
    if mode not in MODES:
        raise ValueError("Неизвестный режим")
    set_setting("coding_reasoning_mode:" + (owner if owner is not None else current_owner()), mode)
