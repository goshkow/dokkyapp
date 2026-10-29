"""Списки выбора страницы системным меню: плитки стекла лежат над страницей, а меню AppKit - над всем окном."""

from __future__ import annotations

import json

MAX_ITEMS = 32
MAX_LABEL = 200


def parse(payload: str) -> dict | None:
    """Запрос страницы: id ответа, точка якоря и размер страницы, пункты и текущее значение; лишнее отбрасывается."""
    try:
        data = json.loads(payload)
        request_id = data["id"]
        x, y = float(data["x"]), float(data["y"])
        vw, vh = float(data["vw"]), float(data["vh"])
        raw = data["items"]
    except (ValueError, KeyError, TypeError):
        return None
    if type(request_id) is not int or not 0 < request_id < 2**31:
        return None
    if not (1 <= vw <= 20000 and 1 <= vh <= 20000 and 0 <= x <= vw and 0 <= y <= vh):
        return None
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_ITEMS:
        return None
    items = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        value, label = item.get("value"), item.get("label")
        if not isinstance(value, str) or not isinstance(label, str) or not label.strip():
            return None
        if len(value) > MAX_LABEL or len(label) > MAX_LABEL:
            return None
        items.append({"value": value, "label": label, "disabled": bool(item.get("disabled"))})
    selected = data.get("selected")
    return {
        "id": request_id,
        "x": x,
        "y": y,
        "vw": vw,
        "vh": vh,
        "items": items,
        "selected": selected if isinstance(selected, str) else None,
    }


def result_script(request_id: int, value: str | None) -> str:
    """Ответ странице: выбранное значение или null, если меню закрыли без выбора."""
    return f"window.koddiPopupResult?.({int(request_id)}, {json.dumps(value)})"
