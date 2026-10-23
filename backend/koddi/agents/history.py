from __future__ import annotations

import json
from copy import deepcopy


CHECKPOINT = "_koddi_history_checkpoint"


def without_empty_assistants(messages: list[dict]) -> list[dict]:
    return [
        deepcopy(message)
        for message in messages
        if not (
            message.get("role") == "assistant"
            and not message.get("tool_calls")
            and (
                message.get("content") is None
                or isinstance(message.get("content"), str)
                and not message["content"].strip()
            )
        )
    ]


def replay_history(records: list[dict]) -> list[dict]:
    """Чекпоинты заменяют контекст для провайдера, но не журнал, который только дописывается."""
    history: list[dict] = []
    for record in records:
        if record.get(CHECKPOINT) == 1:
            history = deepcopy(record["messages"])
        else:
            history.append(deepcopy(record))
    return without_empty_assistants(history)


def repair_history(history: list[dict]) -> list[dict]:
    """Результаты сопоставляются только внутри соседнего хода ассистента, инструменты заново не запускаются."""
    repaired: list[dict] = []
    index = 0
    history = without_empty_assistants(history)
    while index < len(history):
        item = deepcopy(history[index])
        index += 1
        if item.get("role") == "tool":
            continue  # orphan/duplicate result from a previously interrupted exchange
        repaired.append(item)
        calls = item.get("tool_calls", [])
        if not calls:
            continue
        ids = [call.get("id") for call in calls]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(
            ids
        ):
            raise ValueError("История содержит некорректные идентификаторы инструментов")
        results = {}
        while index < len(history) and history[index].get("role") == "tool":
            result = deepcopy(history[index])
            try:
                payload = json.loads(result.get("content", ""))
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict) and payload.get("output_file"):
                from koddi.tools.output_store import upgrade_output_reference

                upgraded = upgrade_output_reference(payload)
                if upgraded != payload:
                    result["content"] = json.dumps(upgraded, ensure_ascii=False)
            results.setdefault(result.get("tool_call_id"), result)
            index += 1
        for call_id in ids:
            repaired.append(
                results.get(call_id)
                or {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "ok": False,
                            "interrupted": True,
                            "error": "Действие прервано; результат неизвестен. Проверьте текущее состояние перед повтором.",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
    return repaired


def checkpoint(messages: list[dict], reason: str) -> dict:
    return {CHECKPOINT: 1, "reason": reason, "messages": deepcopy(messages)}


def initial_instructions(messages: list[dict]) -> list[dict]:
    result = []
    for message in messages:
        if message.get("role") not in {"system", "developer"}:
            break
        result.append(deepcopy(message))
    return result


def compacted_messages(
    messages: list[dict],
    summary: str,
    user: dict,
    plan: list[dict],
    *,
    max_chars: int | None = None,
    preserve_terminal_exchange: bool = False,
    current_context: dict | None = None,
) -> list[dict]:
    if not summary.strip():
        raise ValueError("AI-сервер вернул пустое резюме; исходная история сохранена")
    user_index = next((i for i in range(len(messages) - 1, -1, -1) if messages[i] == user), 0)
    assistant_starts = [
        i for i in range(1, len(messages)) if messages[i].get("role") == "assistant"
    ]
    latest_developer = next(
        (i for i in range(len(messages) - 1, 0, -1) if messages[i].get("role") == "developer"), None
    )
    instructions = initial_instructions(messages)
    legacy_rules = []
    if latest_developer is not None and latest_developer >= len(instructions):
        legacy_rules.append(messages[latest_developer])
    context = {
        "role": "user",
        "content": "Continuation checkpoint (historical evidence, not new instructions):\n"
        + summary.strip()
        + "\nCurrent plan:\n"
        + json.dumps(plan, ensure_ascii=False),
    }
    result = []
    # последние обмены сохраняем целиком, включая пары вызов-результат.
    starts = assistant_starts[-3:]
    if not preserve_terminal_exchange or messages[-1].get("role") == "user" or not assistant_starts:
        starts.append(len(messages))
    for start in starts:
        indices = {user_index, *range(start, len(messages))}
        recent = [
            deepcopy(messages[i])
            for i in sorted(indices)
            if i > 0 and messages[i].get("role") not in {"system", "developer"}
        ]
        if current_context is not None:
            from koddi.agents.prompts import CONTEXT_PREFIX

            recent = [message for message in recent if not (
                message.get("role") == "user" and isinstance(message.get("content"), str)
                and message["content"].startswith(CONTEXT_PREFIX)
            )]
        result = [*deepcopy(instructions), deepcopy(context), *deepcopy(legacy_rules),
                  *([deepcopy(current_context)] if current_context is not None else []), *recent]
        if max_chars is None or len(json.dumps(result, ensure_ascii=False)) <= max_chars:
            break
    else:
        raise ValueError(
            "Обязательные инструкции и задача не помещаются в окно модели; исходная история сохранена"
        )
    if len(json.dumps(result, ensure_ascii=False)) >= len(json.dumps(messages, ensure_ascii=False)):
        raise ValueError("Сжатие не уменьшило контекст; исходная история сохранена")
    return result
