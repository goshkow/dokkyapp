from __future__ import annotations

from dataclasses import dataclass


BUDGETS = {
    "small": {"max_chars": 16000, "compact_at": 12000},
    "medium": {"max_chars": 48000, "compact_at": 36000},
    "large": {"max_chars": 120000, "compact_at": 90000},
    "maximum": {"max_chars": 480000, "compact_at": 400000},
}


@dataclass
class ContextState:
    budget: str
    used_chars: int
    should_compact: bool
    summary: str = ""


def estimate(messages: list[dict], budget: str = "medium") -> ContextState:
    cfg = BUDGETS.get(budget, BUDGETS["medium"])
    used = sum(len(str(message.get("content", ""))) for message in messages)
    return ContextState(budget=budget, used_chars=used, should_compact=used >= cfg["compact_at"])


def compact(messages: list[dict], keep_last: int = 8) -> tuple[str, list[dict]]:
    if len(messages) <= keep_last:
        return "", messages
    older = messages[:-keep_last]
    summary = "\n".join(
        f"{item.get('role', 'unknown')}: {str(item.get('content', ''))[:500]}"
        for item in older[-12:]
    )
    compacted = [{"role": "system", "content": "Краткая память предыдущего контекста:\n" + summary}]
    compacted.extend(messages[-keep_last:])
    return summary, compacted
