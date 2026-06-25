import json
import hashlib
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from src.config import SETTINGS, resolve_project_path
from src.dataset_manager import now_iso


DB_PATH = Path(resolve_project_path(SETTINGS.db_path))
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SUPPORTED_DB_BACKENDS = {"sqlite", "postgres"}
_INIT_LOCK = threading.Lock()
_INITIALIZED = False


class UnsupportedDatabaseBackend(RuntimeError):
    pass


class DatabaseConfigurationError(RuntimeError):
    pass


def db_backend() -> str:
    backend = (SETTINGS.db_backend or "sqlite").strip().lower()
    if backend == "postgresql":
        backend = "postgres"
    return backend


def validate_db_backend() -> str:
    backend = db_backend()
    if backend not in SUPPORTED_DB_BACKENDS:
        raise UnsupportedDatabaseBackend(
            f"Unsupported VOX_DB_BACKEND={SETTINGS.db_backend!r}. Use one of: {', '.join(sorted(SUPPORTED_DB_BACKENDS))}."
        )
    return backend


def is_postgres() -> bool:
    return db_backend() == "postgres"


def sql(query: str) -> str:
    if not is_postgres():
        return query
    return query.replace("?", "%s")


class DatabaseConnection(AbstractContextManager):
    def __init__(self, conn: Any, backend: str):
        self.conn = conn
        self.backend = backend

    def __enter__(self) -> "DatabaseConnection":
        self.conn.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        return self.conn.__exit__(exc_type, exc, traceback)

    def execute(self, query: str, params: Iterable[Any] | None = None) -> Any:
        if params is None:
            return self.conn.execute(sql(query))
        return self.conn.execute(sql(query), list(params))

    def executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self.conn.executescript(script)
            return
        for statement in script.split(";"):
            statement = statement.strip()
            executable = "\n".join(
                line for line in statement.splitlines() if not line.strip().startswith("--")
            ).strip()
            if executable:
                self.conn.execute(statement)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()


def connect() -> DatabaseConnection:
    backend = validate_db_backend()
    if backend == "postgres":
        return connect_postgres()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return DatabaseConnection(conn, backend)


def connect_postgres() -> DatabaseConnection:
    if not SETTINGS.database_url:
        raise DatabaseConfigurationError("VOX_DATABASE_URL is required when VOX_DB_BACKEND=postgres.")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise DatabaseConfigurationError(
            "PostgreSQL support requires psycopg. Install requirements.txt, then set VOX_DB_BACKEND=postgres."
        ) from exc
    conn = psycopg.connect(SETTINGS.database_url, row_factory=dict_row, connect_timeout=10)
    return DatabaseConnection(conn, "postgres")


def init_db() -> None:
    global _INITIALIZED
    validate_db_backend()
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        with connect() as conn:
            run_migrations(conn)
        _INITIALIZED = True


def migration_files(backend: str | None = None) -> list[Path]:
    backend = backend or validate_db_backend()
    path = MIGRATIONS_DIR / backend
    if not path.exists():
        raise DatabaseConfigurationError(f"No migrations found for database backend: {backend}")
    return sorted(path.glob("*.sql"))


def load_migration_sql(backend: str, filename: str) -> str:
    path = MIGRATIONS_DIR / backend / filename
    if not path.exists():
        raise DatabaseConfigurationError(f"Missing migration file: {path}")
    return path.read_text(encoding="utf-8")


def sqlite_schema_sql() -> str:
    return load_migration_sql("sqlite", "001_initial_schema.sql")


def postgres_schema_sql() -> str:
    return load_migration_sql("postgres", "001_initial_schema.sql")


def migration_table_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """


def ensure_migration_table(conn: DatabaseConnection) -> None:
    conn.execute(migration_table_sql())


def applied_migration_versions(conn: DatabaseConnection) -> set[str]:
    ensure_migration_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row["version"]) for row in rows}


def apply_migration_hook(conn: DatabaseConnection, version: str) -> None:
    if version == "002_admin_token_expires":
        ensure_column(conn, "admin_tokens", "expires_at", "TEXT")


def run_migrations(conn: DatabaseConnection) -> list[str]:
    backend = conn.backend
    applied = applied_migration_versions(conn)
    applied_now: list[str] = []
    for migration_path in migration_files(backend):
        version = migration_path.stem
        if version in applied:
            continue
        conn.executescript(migration_path.read_text(encoding="utf-8"))
        apply_migration_hook(conn, version)
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (version, migration_path.name, now_iso()),
        )
        applied_now.append(version)
    return applied_now


def migration_status() -> Dict[str, Any]:
    backend = validate_db_backend()
    available = [path.stem for path in migration_files(backend)]
    with connect() as conn:
        applied = sorted(applied_migration_versions(conn))
    return {
        "backend": backend,
        "available": available,
        "applied": applied,
        "pending": [version for version in available if version not in set(applied)],
    }


def ensure_column(conn: DatabaseConnection, table: str, column: str, declaration: str) -> None:
    if conn.backend == "postgres":
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            LIMIT 1
            """,
            (table, column),
        ).fetchone()
        if row is None:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        return
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def hash_admin_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_cache_query(query: str) -> str:
    text = re.sub(r"\s+", " ", (query or "").strip().lower())
    text = re.sub(r"[?!.,;:]+$", "", text)
    return text[:500]


def answer_cache_key(org_id: str, language: str, query: str) -> str:
    normalized = normalize_cache_query(query)
    raw = f"{org_id}|{language}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_answer(org_id: str, language: str, query: str) -> Dict[str, Any] | None:
    init_db()
    key = answer_cache_key(org_id, language, query)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT cache_key, response_json, expires_at, hit_count
            FROM answer_cache
            WHERE cache_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        expires_at = parse_iso(row["expires_at"])
        if expires_at and expires_at <= now:
            conn.execute("DELETE FROM answer_cache WHERE cache_key = ?", (key,))
            return None
        conn.execute(
            "UPDATE answer_cache SET hit_count = hit_count + 1, updated_at = ? WHERE cache_key = ?",
            (now_iso(), key),
        )
    cached = json_loads(row["response_json"], None)
    if not isinstance(cached, dict):
        return None
    cached["cached"] = True
    cached["cache_hit_count"] = int(row["hit_count"]) + 1
    return cached


def store_cached_answer(
    org_id: str,
    language: str,
    query: str,
    response: Dict[str, Any],
    ttl_seconds: int,
) -> None:
    init_db()
    if ttl_seconds <= 0:
        return
    normalized = normalize_cache_query(query)
    if not normalized:
        return
    key = answer_cache_key(org_id, language, query)
    now = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat()
    payload = {
        key_name: response.get(key_name)
        for key_name in (
            "response",
            "intent",
            "confidence",
            "layer",
            "language",
            "layer_ms",
            "total_ms",
            "handoff_recommended",
        )
    }
    payload["cached"] = False
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO answer_cache (
                cache_key, org_id, language, normalized_query, response_json,
                hit_count, created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_json = excluded.response_json,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (key, org_id, language, normalized, json_dumps(payload), now, now, expires_at),
        )


def count_answer_cache(org_id: str | None = None) -> int:
    init_db()
    if org_id:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM answer_cache WHERE org_id = ?", (org_id,)).fetchone()
    else:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM answer_cache").fetchone()
    return int(row["count"])


def cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def count_where(conn: DatabaseConnection, table: str, where: str, params: Iterable[Any]) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", list(params)).fetchone()
    return int(row["count"])


def cleanup_expired_answer_cache(conn: DatabaseConnection, dry_run: bool = False) -> int:
    now = now_iso()
    where = "expires_at IS NOT NULL AND expires_at <= ?"
    params = [now]
    count = count_where(conn, "answer_cache", where, params)
    if not dry_run:
        conn.execute(f"DELETE FROM answer_cache WHERE {where}", params)
    return count


def cleanup_expired_admin_tokens(conn: DatabaseConnection, dry_run: bool = False) -> int:
    now = now_iso()
    where = "active = 1 AND expires_at IS NOT NULL AND expires_at <= ?"
    params = [now]
    count = count_where(conn, "admin_tokens", where, params)
    if not dry_run:
        conn.execute(f"UPDATE admin_tokens SET active = 0 WHERE {where}", params)
    return count


def cleanup_expired_persisted_sessions(conn: DatabaseConnection, dry_run: bool = False) -> int:
    if SETTINGS.session_ttl_seconds <= 0:
        return 0
    cutoff = time.time() - SETTINGS.session_ttl_seconds
    where = "last_seen < ?"
    params = [cutoff]
    count = count_where(conn, "sessions", where, params)
    if not dry_run:
        conn.execute(f"DELETE FROM sessions WHERE {where}", params)
    return count


def cleanup_old_audit_events(conn: DatabaseConnection, dry_run: bool = False) -> int:
    if SETTINGS.maintenance_audit_retention_days <= 0:
        return 0
    cutoff = cutoff_iso(SETTINGS.maintenance_audit_retention_days)
    where = "created_at < ?"
    params = [cutoff]
    count = count_where(conn, "audit_events", where, params)
    if not dry_run:
        conn.execute(f"DELETE FROM audit_events WHERE {where}", params)
    return count


def cleanup_old_finished_jobs(conn: DatabaseConnection, dry_run: bool = False) -> int:
    if SETTINGS.maintenance_job_retention_days <= 0:
        return 0
    cutoff = cutoff_iso(SETTINGS.maintenance_job_retention_days)
    where = "status IN ('completed', 'failed') AND finished_at IS NOT NULL AND finished_at < ?"
    params = [cutoff]
    count = count_where(conn, "jobs", where, params)
    if not dry_run:
        conn.execute(f"DELETE FROM jobs WHERE {where}", params)
    return count


def cleanup_old_resolved_handoffs(conn: DatabaseConnection, dry_run: bool = False) -> int:
    if SETTINGS.maintenance_handoff_retention_days <= 0:
        return 0
    cutoff = cutoff_iso(SETTINGS.maintenance_handoff_retention_days)
    where = "status IN ('resolved', 'closed') AND resolved_at IS NOT NULL AND resolved_at < ?"
    params = [cutoff]
    count = count_where(conn, "handoff_tickets", where, params)
    if not dry_run:
        conn.execute(f"DELETE FROM handoff_tickets WHERE {where}", params)
    return count


def run_maintenance(dry_run: bool = False) -> Dict[str, Any]:
    init_db()
    with connect() as conn:
        results = {
            "expired_answer_cache": cleanup_expired_answer_cache(conn, dry_run=dry_run),
            "expired_admin_tokens": cleanup_expired_admin_tokens(conn, dry_run=dry_run),
            "expired_sessions": cleanup_expired_persisted_sessions(conn, dry_run=dry_run),
            "old_audit_events": cleanup_old_audit_events(conn, dry_run=dry_run),
            "old_finished_jobs": cleanup_old_finished_jobs(conn, dry_run=dry_run),
            "old_resolved_handoffs": cleanup_old_resolved_handoffs(conn, dry_run=dry_run),
        }
        if dry_run:
            conn.rollback()
    return {
        "dry_run": dry_run,
        "cleaned": results,
        "total": sum(results.values()),
    }


def iso_from_now(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_expired(expires_at: str | None) -> bool:
    parsed = parse_iso(expires_at)
    return bool(parsed and parsed <= datetime.now(timezone.utc))


def create_admin_token(
    name: str,
    org_id: str | None = None,
    scopes: list[str] | None = None,
    expires_in_days: int | None = 90,
) -> Dict[str, Any]:
    init_db()
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Token name is required")
    if len(clean_name) > 80:
        raise ValueError("Token name must be 80 characters or less")

    token = f"vox_{secrets.token_urlsafe(32)}"
    token_id = uuid.uuid4().hex
    now = now_iso()
    scopes = scopes or ["admin"]
    if expires_in_days is not None:
        try:
            expires_in_days = int(expires_in_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("expires_in_days must be a number") from exc
        if expires_in_days < 1 or expires_in_days > 3650:
            raise ValueError("expires_in_days must be between 1 and 3650")
    expires_at = iso_from_now(expires_in_days) if expires_in_days is not None else None
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO admin_tokens (
                token_id, name, token_hash, org_id, scopes_json, active, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (token_id, clean_name, hash_admin_token(token), org_id or None, json_dumps(scopes), now, expires_at),
        )
    return {
        "token_id": token_id,
        "name": clean_name,
        "token": token,
        "org_id": org_id or None,
        "scopes": scopes,
        "active": True,
        "created_at": now,
        "expires_at": expires_at,
        "last_used_at": None,
    }


def verify_admin_token(token: str) -> Dict[str, Any] | None:
    init_db()
    if not token:
        return None
    token_hash = hash_admin_token(token)
    now = now_iso()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT token_id, name, org_id, scopes_json, active, created_at, expires_at, last_used_at, revoked_at
            FROM admin_tokens
            WHERE token_hash = ? AND active = 1
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if is_expired(row["expires_at"]):
            conn.execute(
                "UPDATE admin_tokens SET active = 0 WHERE token_id = ?",
                (row["token_id"],),
            )
            return None
        conn.execute(
            "UPDATE admin_tokens SET last_used_at = ? WHERE token_id = ?",
            (now, row["token_id"]),
        )
    return {
        "token_id": row["token_id"],
        "name": row["name"],
        "org_id": row["org_id"],
        "scopes": json_loads(row["scopes_json"], []),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_used_at": now,
        "revoked_at": row["revoked_at"],
        "source": "database",
    }


def list_admin_tokens() -> list[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT token_id, name, org_id, scopes_json, active, created_at, expires_at, last_used_at, revoked_at
            FROM admin_tokens
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        {
            "token_id": row["token_id"],
            "name": row["name"],
            "org_id": row["org_id"],
            "scopes": json_loads(row["scopes_json"], []),
            "active": bool(row["active"]) and not is_expired(row["expires_at"]),
            "expired": is_expired(row["expires_at"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "last_used_at": row["last_used_at"],
            "revoked_at": row["revoked_at"],
        }
        for row in rows
    ]


def revoke_admin_token(token_id: str) -> bool:
    init_db()
    now = now_iso()
    with connect() as conn:
        result = conn.execute(
            """
            UPDATE admin_tokens
            SET active = 0, revoked_at = ?
            WHERE token_id = ? AND active = 1
            """,
            (now, token_id),
        )
    return result.rowcount > 0


def create_handoff_ticket(
    org_id: str,
    query: str,
    response: str | None,
    intent: str | None,
    confidence: float,
    layer: int,
    language: str | None = None,
    session_id: str | None = None,
    call_id: str | None = None,
    request_id: str | None = None,
    department: str | None = None,
    contact: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    init_db()
    ticket_id = uuid.uuid4().hex
    now = now_iso()
    ticket = {
        "ticket_id": ticket_id,
        "org_id": org_id,
        "session_id": session_id,
        "call_id": call_id,
        "request_id": request_id,
        "query": query,
        "response": response,
        "intent": intent,
        "confidence": float(confidence),
        "layer": int(layer),
        "language": language,
        "status": "open",
        "department": department,
        "contact": contact or {},
        "notes": None,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO handoff_tickets (
                ticket_id, org_id, session_id, call_id, request_id, query, response,
                intent, confidence, layer, language, status, department, contact_json,
                notes, created_at, updated_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                org_id,
                session_id,
                call_id,
                request_id,
                query,
                response,
                intent,
                float(confidence),
                int(layer),
                language,
                "open",
                department,
                json_dumps(contact or {}),
                None,
                now,
                now,
                None,
            ),
        )
    return ticket


def list_handoff_tickets(
    org_id: str | None = None,
    status: str | None = "open",
    limit: int = 50,
) -> list[Dict[str, Any]]:
    init_db()
    where = []
    params: list[Any] = []
    if org_id:
        where.append("org_id = ?")
        params.append(org_id)
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(int(limit), 100))
    params.append(limit)
    order_by = "ORDER BY created_at DESC, ticket_id DESC" if is_postgres() else "ORDER BY created_at DESC, rowid DESC"
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM handoff_tickets
            {clause}
            {order_by}
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [handoff_row_to_dict(row) for row in rows]


def update_handoff_ticket(ticket_id: str, status: str, notes: str | None = None) -> Dict[str, Any] | None:
    init_db()
    status = status.strip().lower()
    if status not in {"open", "in_progress", "resolved", "closed"}:
        raise ValueError("Invalid handoff status")
    now = now_iso()
    resolved_at = now if status in {"resolved", "closed"} else None
    with connect() as conn:
        row = conn.execute("SELECT * FROM handoff_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        if row is None:
            return None
        existing_notes = row["notes"]
        conn.execute(
            """
            UPDATE handoff_tickets
            SET status = ?, notes = ?, updated_at = ?, resolved_at = ?
            WHERE ticket_id = ?
            """,
            (status, notes if notes is not None else existing_notes, now, resolved_at, ticket_id),
        )
    with connect() as conn:
        updated = conn.execute("SELECT * FROM handoff_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return handoff_row_to_dict(updated) if updated else None


def count_handoff_tickets(org_id: str | None = None, status: str | None = "open") -> int:
    init_db()
    where = []
    params: list[Any] = []
    if org_id:
        where.append("org_id = ?")
        params.append(org_id)
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM handoff_tickets {clause}", params).fetchone()
    return int(row["count"])


def handoff_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "ticket_id": row["ticket_id"],
        "org_id": row["org_id"],
        "session_id": row["session_id"],
        "call_id": row["call_id"],
        "request_id": row["request_id"],
        "query": row["query"],
        "response": row["response"],
        "intent": row["intent"],
        "confidence": row["confidence"],
        "layer": row["layer"],
        "language": row["language"],
        "status": row["status"],
        "department": row["department"],
        "contact": json_loads(row["contact_json"], {}),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
    }


def upsert_organization(profile: Dict[str, Any]) -> None:
    init_db()
    org_id = profile.get("org_id", "default")
    path = Path("organizations") / org_id / "profile.json"
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO organizations (
                org_id, organization_name, assistant_name, domain, profile_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(org_id) DO UPDATE SET
                organization_name = excluded.organization_name,
                assistant_name = excluded.assistant_name,
                domain = excluded.domain,
                profile_path = excluded.profile_path,
                updated_at = excluded.updated_at
            """,
            (
                org_id,
                profile.get("organization_name", org_id),
                profile.get("assistant_name", "VOX"),
                profile.get("domain", "general"),
                str(path),
                now,
                now,
            ),
        )


def sync_organizations(profiles: Iterable[Dict[str, Any]]) -> None:
    for profile in profiles:
        upsert_organization(profile)


def list_persisted_organizations() -> list[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT org_id, organization_name, assistant_name, domain, profile_path, created_at, updated_at
            FROM organizations
            ORDER BY organization_name COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]


def persist_job(job: Dict[str, Any]) -> None:
    init_db()
    metadata = job.get("metadata") or {}
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, org_id, kind, status, progress, message, created_at, started_at,
                finished_at, result_json, error, metadata_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                org_id = excluded.org_id,
                kind = excluded.kind,
                status = excluded.status,
                progress = excluded.progress,
                message = excluded.message,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                result_json = excluded.result_json,
                error = excluded.error,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                job["job_id"],
                metadata.get("org_id"),
                job["kind"],
                job["status"],
                int(job["progress"]),
                job["message"],
                job["created_at"],
                job.get("started_at"),
                job.get("finished_at"),
                json_dumps(job.get("result")) if job.get("result") is not None else None,
                job.get("error"),
                json_dumps(metadata),
                now,
            ),
        )


def create_queued_job(
    kind: str,
    org_id: str | None = None,
    message: str = "Queued",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    job = {
        "job_id": uuid.uuid4().hex,
        "kind": kind,
        "status": "queued",
        "progress": 0,
        "message": message,
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
        "metadata": {**(metadata or {}), "org_id": org_id},
    }
    persist_job(job)
    return job


def has_active_persisted_job(kind: str, org_id: str | None = None) -> bool:
    init_db()
    params: list[Any] = [kind]
    clause = "kind = ? AND status IN ('queued', 'running')"
    if org_id:
        clause += " AND org_id = ?"
        params.append(org_id)
    with connect() as conn:
        row = conn.execute(f"SELECT 1 FROM jobs WHERE {clause} LIMIT 1", params).fetchone()
    return row is not None


def update_persisted_job(job_id: str, **updates: Any) -> Dict[str, Any] | None:
    current = get_persisted_job(job_id)
    if current is None:
        return None
    current.update(updates)
    persist_job(current)
    return current


def claim_next_queued_job(worker_id: str, kinds: Iterable[str] | None = None) -> Dict[str, Any] | None:
    init_db()
    kind_list = list(kinds or [])
    placeholders = ",".join("?" for _ in kind_list)
    kind_clause = f"AND kind IN ({placeholders})" if kind_list else ""
    params: list[Any] = [*kind_list]
    now = now_iso()

    with connect() as conn:
        if conn.backend == "sqlite":
            conn.execute("BEGIN IMMEDIATE")
            order_by = "ORDER BY created_at ASC, rowid ASC"
            lock_clause = ""
        else:
            order_by = "ORDER BY created_at ASC, job_id ASC"
            lock_clause = "FOR UPDATE SKIP LOCKED"
        row = conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE status = 'queued' {kind_clause}
            {order_by}
            LIMIT 1
            {lock_clause}
            """,
            params,
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        metadata = json_loads(row["metadata_json"], {})
        metadata["worker_id"] = worker_id
        conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                progress = CASE WHEN progress < 5 THEN 5 ELSE progress END,
                message = ?,
                started_at = COALESCE(started_at, ?),
                metadata_json = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            ("Claimed by worker", now, json_dumps(metadata), now, row["job_id"]),
        )
        conn.commit()

    return get_persisted_job(row["job_id"])


def get_persisted_job(job_id: str) -> Dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return job_row_to_dict(row) if row else None


def latest_persisted_job(kind: str | None = None, org_id: str | None = None) -> Dict[str, Any] | None:
    init_db()
    where = []
    params: list[Any] = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if org_id:
        where.append("org_id = ?")
        params.append(org_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order_by = "ORDER BY created_at DESC, job_id DESC" if is_postgres() else "ORDER BY created_at DESC, rowid DESC"
    with connect() as conn:
        row = conn.execute(
            f"SELECT * FROM jobs {clause} {order_by} LIMIT 1",
            params,
        ).fetchone()
    return job_row_to_dict(row) if row else None


def job_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "kind": row["kind"],
        "status": row["status"],
        "progress": row["progress"],
        "message": row["message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "result": json_loads(row["result_json"], None),
        "error": row["error"],
        "metadata": json_loads(row["metadata_json"], {}),
    }


def persist_session(org_id: str, session_data: Dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, org_id, query_count, layer_counts_json, history_json,
                created_at, last_seen, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, org_id) DO UPDATE SET
                query_count = excluded.query_count,
                layer_counts_json = excluded.layer_counts_json,
                history_json = excluded.history_json,
                last_seen = excluded.last_seen,
                updated_at = excluded.updated_at
            """,
            (
                session_data["session_id"],
                org_id,
                int(session_data.get("query_count", 0)),
                json_dumps(session_data.get("layer_counts", {})),
                json_dumps(session_data.get("history", [])),
                float(session_data.get("created_at", 0)),
                float(session_data.get("last_seen", 0)),
                now_iso(),
            ),
        )


def count_persisted_sessions(org_id: str | None = None) -> int:
    init_db()
    if org_id:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM sessions WHERE org_id = ?", (org_id,)).fetchone()
    else:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()
    return int(row["count"])


def record_audit_event(
    event_type: str,
    request_id: str | None = None,
    call_id: str | None = None,
    org_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status: int | None = None,
    elapsed_ms: int | None = None,
    remote_addr: str | None = None,
    details: Dict[str, Any] | None = None,
) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_events (
                created_at, request_id, call_id, org_id, event_type, method, path,
                status, elapsed_ms, remote_addr, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                request_id,
                call_id,
                org_id,
                event_type,
                method,
                path,
                status,
                elapsed_ms,
                remote_addr,
                json_dumps(details or {}),
            ),
        )


def latest_audit_events(limit: int = 25) -> list[Dict[str, Any]]:
    init_db()
    limit = max(1, min(int(limit), 100))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, request_id, call_id, org_id, event_type, method, path,
                   status, elapsed_ms, remote_addr, details_json
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            **{key: row[key] for key in row.keys() if key != "details_json"},
            "details": json_loads(row["details_json"], {}),
        }
        for row in rows
    ]
