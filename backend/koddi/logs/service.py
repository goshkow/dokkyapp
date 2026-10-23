from __future__ import annotations

import json
import os
import logging
import re
from typing import Any

from koddi.storage.db import db, get_setting


LOGGER = logging.getLogger("koddi")
SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)")
RAW_SECRETS = (
    # любой приватный ключ, включая PGP; оборванный блок прячется до конца текста
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_.~+/-]+=*"),
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if re.search(r"(?i)(token|secret|password|api[_-]?key)", str(key))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = SECRET_PATTERN.sub(r"\1=[redacted]", value)
        for pattern in RAW_SECRETS:
            value = pattern.sub('[redacted]', value)
        return value
    return value


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def log_owner() -> str:
    user = get_setting("dokky_user", {})
    user = user if isinstance(user, dict) else {}
    value = user.get("id") or user.get("user_id")
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        return str(value)
    return "__test__" if os.getenv("KODDI_INTERNAL_TEST") == "1" else ""


def log_scope() -> tuple[str, str]:
    owner = log_owner()
    return owner, "" if os.getenv("KODDI_INTERNAL_TEST") == "1" else owner


def log_event(level: str, event: str, message: str, data: dict[str, Any] | None = None) -> None:
    data = redact(data or {})
    message = str(redact(message))
    getattr(LOGGER, level.lower(), LOGGER.info)("%s: %s %s", event, message, data)
    with db() as conn:
        conn.execute(
            "INSERT INTO logs(level, event, message, data_json, owner_id) VALUES (?, ?, ?, ?, ?)",
            (level.upper(), event, message, json.dumps(data, ensure_ascii=False, default=str), log_owner()),
        )


def list_logs(limit: int = 200) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE owner_id IN (?,?) ORDER BY id DESC LIMIT ?", (*log_scope(), min(max(limit, 1), 1000))
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("owner_id", None)
        item["data"] = json.loads(item.pop("data_json") or "{}")
        items.append(item)
    return items


def search_logs(query: str, limit: int = 200) -> list[dict[str, Any]]:
    needle = f"%{query.strip()}%"
    if not query.strip():
        return list_logs(limit)
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM logs WHERE (event LIKE ? OR message LIKE ? OR data_json LIKE ?) AND owner_id IN (?,?)
               ORDER BY id DESC LIMIT ?""",
            (needle, needle, needle, *log_scope(), min(max(limit, 1), 1000)),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("owner_id", None)
        item["data"] = redact(json.loads(item.pop("data_json") or "{}"))
        items.append(item)
    return items
