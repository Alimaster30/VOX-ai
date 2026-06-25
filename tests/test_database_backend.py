import importlib
import sqlite3
import sys

import pytest


def reload_persistence(monkeypatch, **env):
    monkeypatch.setenv("VOX_AUTOLOAD_MODELS", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for name in list(sys.modules):
        if name in {"src.config", "src.dataset_manager", "src.persistence"}:
            sys.modules.pop(name, None)
    return importlib.import_module("src.persistence")


def test_sqlite_backend_is_default(monkeypatch, tmp_path):
    persistence = reload_persistence(monkeypatch, VOX_DB_PATH=str(tmp_path / "vox.sqlite3"))

    assert persistence.db_backend() == "sqlite"
    assert persistence.validate_db_backend() == "sqlite"


def test_postgresql_alias_normalizes_to_postgres(monkeypatch):
    persistence = reload_persistence(monkeypatch, VOX_DB_BACKEND="postgresql")

    assert persistence.db_backend() == "postgres"
    assert persistence.validate_db_backend() == "postgres"


def test_postgres_requires_database_url(monkeypatch):
    persistence = reload_persistence(monkeypatch, VOX_DB_BACKEND="postgres", VOX_DATABASE_URL="")

    with pytest.raises(persistence.DatabaseConfigurationError) as exc:
        persistence.connect()
    assert "VOX_DATABASE_URL is required" in str(exc.value)


def test_postgres_sql_placeholders(monkeypatch):
    persistence = reload_persistence(
        monkeypatch,
        VOX_DB_BACKEND="postgres",
        VOX_DATABASE_URL="postgresql://vox:secret@localhost:5432/vox",
    )

    assert persistence.sql("SELECT * FROM jobs WHERE kind = ? AND org_id = ?") == (
        "SELECT * FROM jobs WHERE kind = %s AND org_id = %s"
    )
    assert "BIGSERIAL PRIMARY KEY" in persistence.postgres_schema_sql()


def test_sqlite_init_records_migrations(monkeypatch, tmp_path):
    persistence = reload_persistence(monkeypatch, VOX_DB_PATH=str(tmp_path / "vox.sqlite3"))

    before = persistence.migration_status()
    persistence.init_db()
    after = persistence.migration_status()

    assert before["pending"] == ["001_initial_schema", "002_admin_token_expires"]
    assert after["pending"] == []
    assert after["applied"] == ["001_initial_schema", "002_admin_token_expires"]

    with persistence.connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM schema_migrations").fetchone()
    assert row["count"] == 2


def test_legacy_admin_tokens_table_gets_expires_column(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE admin_tokens (
                token_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                org_id TEXT,
                scopes_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT
            )
            """
        )

    persistence = reload_persistence(monkeypatch, VOX_DB_PATH=str(db_path))
    persistence.init_db()

    with persistence.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(admin_tokens)").fetchall()}
        migrations = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]

    assert "expires_at" in columns
    assert migrations == ["001_initial_schema", "002_admin_token_expires"]


def test_unknown_backend_is_rejected(monkeypatch):
    persistence = reload_persistence(monkeypatch, VOX_DB_BACKEND="mysql")

    with pytest.raises(persistence.UnsupportedDatabaseBackend) as exc:
        persistence.validate_db_backend()
    assert "Unsupported VOX_DB_BACKEND" in str(exc.value)
