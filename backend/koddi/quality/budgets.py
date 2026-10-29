"""Бюджеты объективных гейтов по глубине хода (light / pro / max) и настройке пользователя.

light - только дешёвое и детерминированное (сложность, сигналы, покрытие из готовых отчётов), без мутаций;
pro - плюс дубли, правило слоёв и точечные мутации; max - сбор покрытия и больше мутантов.
Настройка `quality_budget`: auto (по глубине), economy (всегда light), strict (всегда max), off (гейты сами
не запускаются, инструмент quality.run остаётся доступен агенту и человеку).
"""

from __future__ import annotations

from typing import Any

from koddi.storage.db import get_setting, set_setting

SETTING = "quality_budget"
MODES = ("auto", "economy", "strict", "off")

BUDGETS: dict[str, dict[str, Any]] = {
    "light": {"auto_gates": ["crap", "signals"], "coverage": "existing", "max_mutants": 0, "wall_seconds": 0,
              "mutant_timeout": 60, "max_files": 0, "dry_universe": 1500, "blocking": ["crap"]},
    "pro": {"auto_gates": ["crap", "signals", "dry", "architecture", "mutation"], "coverage": "existing",
            "max_mutants": 15, "wall_seconds": 180, "mutant_timeout": 90, "max_files": 3, "dry_universe": 4000,
            "blocking": ["crap", "architecture", "mutation"]},
    "max": {"auto_gates": ["crap", "signals", "dry", "architecture", "mutation"], "coverage": "collect",
            "max_mutants": 40, "wall_seconds": 600, "mutant_timeout": 120, "max_files": 6, "dry_universe": 4000,
            "blocking": ["crap", "architecture", "mutation", "signals"]},
}
# ручной вызов quality.run не должен становиться дороже max даже по просьбе модели
MANUAL_LIMITS = {"max_mutants": 60, "wall_seconds": 900}


def mode() -> str:
    value = get_setting(SETTING, "auto")
    return value if value in MODES else "auto"


def set_mode(value: str) -> str:
    if value not in MODES:
        raise ValueError(f"режим бюджета: {', '.join(MODES)}")
    set_setting(SETTING, value)
    return value


def level_for(depth: str | None) -> str:
    current = mode()
    if current == "economy":
        return "light"
    if current == "strict":
        return "max"
    return depth if depth in BUDGETS else "pro"


def budget_for(depth: str | None) -> dict[str, Any]:
    level = level_for(depth)
    return {"level": level, "mode": mode(), **BUDGETS[level]}


def automatic(depth: str | None) -> dict[str, Any] | None:
    """Бюджет автоматического прогона перед завершением задачи или None, если пользователь выключил."""
    if mode() == "off":
        return None
    return budget_for(depth)
