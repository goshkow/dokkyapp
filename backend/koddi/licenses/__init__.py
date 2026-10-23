from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

from koddi.config import app_dir, is_packaged_build


def root() -> Path:
    return app_dir() / "koddi/licenses" if is_packaged_build() else Path(__file__).parent


def catalog() -> dict:
    path = root() / "catalog.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
        raise HTTPException(503, "Каталог лицензий не включён в сборку")
    return json.loads(path.read_text(encoding="utf-8"))


def notice(component_id: str, notice_id: str) -> dict:
    component = next((item for item in catalog()["components"] if item["id"] == component_id), None)
    item = (
        next((item for item in component["notices"] if item["id"] == notice_id), None)
        if component
        else None
    )
    if item is None:
        raise HTTPException(404, "Уведомление не найдено")
    base = root().resolve()
    path = base / item["path"]
    if not path.resolve().is_relative_to(base) or any(
        parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(base)
    ):
        raise HTTPException(404, "Уведомление недоступно")
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise HTTPException(404, "Уведомление недоступно")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise HTTPException(409, "Текст уведомления изменился")
    return {"name": item["name"], "text": data.decode("utf-8", errors="replace")}
