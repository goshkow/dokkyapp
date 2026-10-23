from __future__ import annotations

import sqlite3
import pytest

import koddi.storage.db as storage_db


def test_sql_migrations_create_versioned_schema(tmp_path, monkeypatch) -> None:
    database = tmp_path / "migrated.sqlite3"
    monkeypatch.setattr(storage_db, "DB_PATH", database)
    storage_db.init_db()

    connection = sqlite3.connect(database)
    versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    connection.close()

    assert {"0001_initial.sql", "0003_remote_agent.sql"}.issubset(versions)
    assert {"projects", "messages", "agent_history", "agent_plans", "agent_requests"}.issubset(
        tables
    )
    assert "model_downloads" not in tables
    assert "model_registry" not in tables


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    database = tmp_path / "atomic.sqlite3"
    monkeypatch.setattr(storage_db, "DB_PATH", database)
    storage_db.init_db()
    directory = tmp_path / "migrations"
    directory.mkdir()
    script = directory / "9999_failure.sql"
    script.write_text(
        "ALTER TABLE chats ADD COLUMN probe TEXT; INSERT INTO missing_table VALUES (1);",
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_db, "MIGRATIONS_DIR", directory)
    with pytest.raises(sqlite3.OperationalError):
        storage_db.init_db()
    with storage_db.db() as conn:
        assert "probe" not in {row["name"] for row in conn.execute("PRAGMA table_info(chats)")}
        assert not conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version='9999_failure.sql'"
        ).fetchone()
    script.write_text("ALTER TABLE chats ADD COLUMN probe TEXT;", encoding="utf-8")
    storage_db.init_db()
    storage_db.init_db()
    with storage_db.db() as conn:
        assert "probe" in {row["name"] for row in conn.execute("PRAGMA table_info(chats)")}
