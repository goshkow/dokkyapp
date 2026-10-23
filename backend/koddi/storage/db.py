from __future__ import annotations

import json
import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from koddi.config import data_dir


DB_PATH = data_dir() / "koddi.sqlite3"
MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if migration.name in applied:
                continue
            try:
                script=migration.read_text(encoding='utf-8')
                if migration.name=='0001_initial.sql':
                    # исторический PRAGMA выполняется выше, до атомарной миграции
                    script=script.removeprefix('PRAGMA journal_mode=WAL;')
                conn.executescript('BEGIN IMMEDIATE;\n'+script)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (migration.name,))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        defaults = {
            "theme": "dark",
            "locale": "ru",
            "permission_profile": "ask",
            "selected_model": "",
            "context_budget": "medium",
            "left_panel_collapsed": False,
            "right_panel_collapsed": False,
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value_json) VALUES ('defaults_version', '1')"
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_setting(key: str, default: Any = None) -> Any:
    with db() as conn:
        row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )


def runtime_path(*parts: str) -> Path:
    path = data_dir().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def durable_write(function, *args, **kwargs):
    """Короткая запись завершается до отмены, чтобы не оставлять поздних изменений журнала."""
    pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise
