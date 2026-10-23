"""Разбивка окна контекста по категориям: куда именно уходят токены сессии."""

from __future__ import annotations

import json
import re
from typing import Any

from koddi.agents.compaction import context_size
from koddi.agents.prompts import CONTEXT_PREFIX

# порядок строк в карточке «Контекст»
CATEGORIES = (
    ("system", "Системные инструкции"),
    ("tools", "Инструменты"),
    ("mcp", "Инструменты MCP"),
    ("memory", "Файлы памяти"),
    ("environment", "Окружение и режим"),
    ("messages", "Сообщения"),
    ("results", "Результаты инструментов"),
)
_RULE_HEADING = re.compile(r"^## (.+?) \(scope: ", re.M)


def _tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(text) // 3


def _context_parts(content: str) -> tuple[list[dict], int]:
    """Снимок окружения разделяется на файлы памяти и остальное окружение."""
    try:
        context = json.loads(content[len(CONTEXT_PREFIX):])
    except ValueError:
        return [], _tokens(content)
    if not isinstance(context, dict):
        return [], _tokens(content)
    memory: list[dict] = []
    rules = str(context.pop("project_instructions", "") or "")
    if rules:
        parts = _RULE_HEADING.split(rules)
        # split с группой даёт [до, путь1, текст1, путь2, текст2, ...]
        for path, text in zip(parts[1::2], parts[2::2]):
            memory.append({"name": path, "tokens": _tokens(text)})
        if len(parts) == 1:
            memory.append({"name": "Инструкции проекта", "tokens": _tokens(rules)})
    saved = context.pop("memory", None) or []
    if saved:
        memory.append({"name": f"Память проекта ({len(saved)})", "tokens": _tokens(saved)})
    own = str(context.pop("user_instructions", "") or "")
    if own:
        memory.append({"name": "Личные инструкции", "tokens": _tokens(own)})
    return memory, _tokens(context)


def context_breakdown(
    messages: list[dict], tools: list[dict], *, window: int, compact_at: int, exact: int | None = None,
) -> dict:
    """Оценка по длине текста (как при сжатии). Точное число токенов последнего ответа модели идёт отдельным полем."""
    totals = {key: 0 for key, _ in CATEGORIES}
    items: dict[str, list[dict]] = {key: [] for key, _ in CATEGORIES}
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        size = _tokens(message)
        if role in {"system", "developer"}:
            totals["system"] += size
        elif role == "tool":
            totals["results"] += size
        elif role == "user" and isinstance(content, str) and content.startswith(CONTEXT_PREFIX):
            memory, environment = _context_parts(content)
            items["memory"].extend(memory)
            totals["memory"] += sum(item["tokens"] for item in memory)
            totals["environment"] += environment
        else:
            totals["messages"] += size
    for tool in tools:
        function = tool.get("function") or {}
        name = str(function.get("name", ""))
        size = _tokens(tool)
        if name.startswith("mcp_"):
            totals["mcp"] += size
            description = str(function.get("description", ""))
            label = description.split(":", 1)[0].removeprefix("MCP ").strip() or name
            items["mcp"].append({"name": label, "tokens": size})
        else:
            totals["tools"] += size
    used = sum(totals.values())
    window = max(int(window), used, 1)
    reserve = max(0, window - compact_at) if compact_at else 0
    free = max(0, window - used - reserve)
    rows = [
        {"key": key, "name": name, "tokens": totals[key], "items": sorted(items[key], key=lambda item: -item["tokens"])}
        for key, name in CATEGORIES
        if totals[key]
    ]
    rows.append({"key": "reserve", "name": "Резерв до сжатия", "tokens": reserve, "items": []})
    rows.append({"key": "free", "name": "Свободно", "tokens": free, "items": []})
    return {
        "window": window,
        "used": used,
        "compact_at": compact_at or None,
        "exact": exact,
        "rows": rows,
        "estimate": context_size(messages, tools, dokky=True),
    }
