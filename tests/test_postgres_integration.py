import importlib
import os
import sys

import pytest


POSTGRES_URL = os.environ.get("VOX_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set VOX_TEST_POSTGRES_URL to run live PostgreSQL integration tests.",
)


VOX_TABLES = (
    "schema_migrations",
    "answer_cache",
    "handoff_tickets",
    "admin_tokens",
    "audit_events",
    "sessions",
    "jobs",
    "organizations",
)


def reset_postgres_database(database_url: str) -> None:
    import psycopg

    with psycopg.connect(database_url) as conn:
        for table in VOX_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


@pytest.fixture(autouse=True)
def clean_postgres_database():
    reset_postgres_database(POSTGRES_URL)
    yield
    reset_postgres_database(POSTGRES_URL)


def reload_persistence(monkeypatch):
    monkeypatch.setenv("VOX_AUTOLOAD_MODELS", "0")
    monkeypatch.setenv("VOX_DB_BACKEND", "postgres")
    monkeypatch.setenv("VOX_DATABASE_URL", POSTGRES_URL)
    for name in list(sys.modules):
        if name in {"src.config", "src.dataset_manager", "src.persistence"}:
            sys.modules.pop(name, None)
    return importlib.import_module("src.persistence")


def test_postgres_persistence_round_trip(monkeypatch):
    persistence = reload_persistence(monkeypatch)

    before = persistence.migration_status()
    persistence.init_db()
    after = persistence.migration_status()

    assert before["pending"] == ["001_initial_schema", "002_admin_token_expires"]
    assert after["pending"] == []

    persistence.upsert_organization(
        {
            "org_id": "pg-demo",
            "organization_name": "Postgres Demo",
            "assistant_name": "VOX",
            "domain": "support",
        }
    )
    assert persistence.list_persisted_organizations()[0]["org_id"] == "pg-demo"

    token = persistence.create_admin_token("pg-token", org_id="pg-demo", scopes=["admin"])
    verified = persistence.verify_admin_token(token["token"])
    assert verified is not None
    assert verified["org_id"] == "pg-demo"

    session = {
        "session_id": "call-1",
        "query_count": 2,
        "layer_counts": {"1": 1, "2": 1},
        "history": [{"query": "hello"}],
        "created_at": 100.0,
        "last_seen": 200.0,
    }
    persistence.persist_session("pg-demo", session)
    assert persistence.count_persisted_sessions("pg-demo") == 1

    job = persistence.create_queued_job("dataset_processing", org_id="pg-demo")
    claimed = persistence.claim_next_queued_job("pg-worker", kinds={"dataset_processing"})
    assert claimed["job_id"] == job["job_id"]
    assert claimed["metadata"]["worker_id"] == "pg-worker"

    ticket = persistence.create_handoff_ticket(
        org_id="pg-demo",
        query="Need a person",
        response=None,
        intent="handoff",
        confidence=0.2,
        layer=4,
    )
    updated_ticket = persistence.update_handoff_ticket(ticket["ticket_id"], "resolved", "done")
    assert updated_ticket["status"] == "resolved"

    response = {"response": "Hello", "intent": "greeting", "confidence": 0.91, "layer": 1, "language": "en"}
    persistence.store_cached_answer("pg-demo", "en", "Hello?", response, ttl_seconds=60)
    cached = persistence.get_cached_answer("pg-demo", "en", "hello")
    assert cached["response"] == "Hello"
    assert cached["cached"] is True

    persistence.record_audit_event("pg_test", org_id="pg-demo", status=200)
    assert persistence.latest_audit_events(limit=1)[0]["event_type"] == "pg_test"
