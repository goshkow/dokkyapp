from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from koddi.config import data_dir
from koddi.storage.db import db


OUTPUT_ID = re.compile(r"out_([1-9][0-9]{0,18})_([1-9][0-9]{0,18})_([1-9][0-9]{0,18})")


def upgrade_output_reference(payload: dict) -> dict:
    if (
        payload.get("truncated") is not True
        or payload.get("output_id")
        or not isinstance(payload.get("output_file"), str)
    ):
        return payload
    legacy = Path(payload["output_file"])
    match = re.fullmatch(
        r"([1-9][0-9]{0,18})-([1-9][0-9]{0,18})-([1-9][0-9]{0,18})\.json", legacy.name
    )
    if not match:
        return payload
    output_id = "out_" + "_".join(match.groups())
    try:
        expected, _, _ = output_path(output_id)
        if legacy.resolve() != expected.resolve() or not expected.is_file():
            return payload
    except (ValueError, OSError):
        return payload
    return {**payload, "output_id": output_id, "read_tool": "tool.output_read"}


def output_path(output_id: str) -> tuple[Path, int, int]:
    match = OUTPUT_ID.fullmatch(output_id)
    if not match:
        raise ValueError("Некорректный идентификатор сохранённого результата")
    turn, message, sequence = (int(value) for value in match.groups())
    profile = data_dir().resolve()
    root = profile / "tool-output"
    path = root / f"{turn}-{message}-{sequence}.json"
    for candidate in (root, path):
        if candidate.is_symlink() or (
            hasattr(candidate, "is_junction") and candidate.is_junction()
        ):
            raise ValueError("Ссылки в хранилище результатов запрещены")
    if root.resolve().parent != profile:
        raise ValueError("Хранилище результатов находится за пределами профиля")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Недопустимый путь сохранённого результата")
    return path, turn, message


def save_tool_output(turn_id: int, message_id: int, sequence: int, content: str) -> dict:
    output_id = f"out_{turn_id}_{message_id}_{sequence}"
    path, _, _ = output_path(output_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"output_id": output_id, "output_file": str(path), "size_bytes": path.stat().st_size}


def read_tool_output(
    output_id: str,
    turn_id: int | None,
    *,
    offset: int = 0,
    max_chars: int = 6000,
    cancel_event=None,
) -> dict:
    if turn_id is None:
        raise ValueError("Чтение сохранённого результата требует контекст чата")
    path, source_turn, source_message = output_path(output_id)
    with db() as conn:
        current = conn.execute("SELECT chat_id FROM turns WHERE id=?", (turn_id,)).fetchone()
        source = conn.execute("SELECT chat_id FROM turns WHERE id=?", (source_turn,)).fetchone()
        message = conn.execute(
            "SELECT chat_id FROM messages WHERE id=?", (source_message,)
        ).fetchone()
    if (
        not current
        or not source
        or not message
        or current["chat_id"] != source["chat_id"]
        or source["chat_id"] != message["chat_id"]
    ):
        raise ValueError("Результат недоступен в этом чате")
    if offset < 0 or not 1 <= max_chars <= 8000:
        raise ValueError("Некорректный диапазон результата")
    if not path.is_file():
        raise ValueError("Сохранённый результат не найден")
    skipped = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        while skipped < offset:
            if cancel_event is not None and cancel_event.is_set():
                return {"ok": False, "cancelled": True}
            part = handle.read(min(65536, offset - skipped))
            if not part:
                break
            skipped += len(part)
        if cancel_event is not None and cancel_event.is_set():
            return {"ok": False, "cancelled": True}
        text = handle.read(max_chars + 1) if skipped == offset else ""
    content = text[:max_chars]
    return {
        "ok": True,
        "output_id": output_id,
        "source_turn_id": source_turn,
        "offset": offset,
        "content": content,
        "next_offset": offset + len(content) if len(text) > max_chars else None,
        "eof": len(text) <= max_chars,
        "size_bytes": path.stat().st_size,
    }
