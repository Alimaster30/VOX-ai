import importlib
import sys
import time


def reload_persistence(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("VOX_DB_PATH", str(tmp_path / "vox.sqlite3"))
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    for name in list(sys.modules):
        if name in {"src.config", "src.persistence"}:
            sys.modules.pop(name, None)
    return importlib.import_module("src.persistence")


def test_maintenance_dry_run_does_not_delete(monkeypatch, tmp_path):
    persistence = reload_persistence(monkeypatch, tmp_path, VOX_SESSION_TTL_SECONDS=1)
    old = time.time() - 100

    persistence.persist_session("demo", {
        "session_id": "old-session",
        "query_count": 0,
        "layer_counts": {"1": 0, "2": 0, "3": 0},
        "history": [],
        "created_at": old,
        "last_seen": old,
    })

    result = persistence.run_maintenance(dry_run=True)

    assert result["cleaned"]["expired_sessions"] == 1
    assert persistence.count_persisted_sessions("demo") == 1


def test_maintenance_removes_expired_runtime_records(monkeypatch, tmp_path):
    persistence = reload_persistence(
        monkeypatch,
        tmp_path,
        VOX_SESSION_TTL_SECONDS=1,
        VOX_MAINTENANCE_AUDIT_RETENTION_DAYS=1,
        VOX_MAINTENANCE_JOB_RETENTION_DAYS=1,
        VOX_MAINTENANCE_HANDOFF_RETENTION_DAYS=1,
    )
    old_epoch = time.time() - 100
    old_iso = "2000-01-01T00:00:00+00:00"

    persistence.persist_session("demo", {
        "session_id": "old-session",
        "query_count": 0,
        "layer_counts": {"1": 0, "2": 0, "3": 0},
        "history": [],
        "created_at": old_epoch,
        "last_seen": old_epoch,
    })
    persistence.store_cached_answer(
        "demo",
        "en",
        "hello",
        {"response": "hi", "intent": "greeting", "confidence": 0.9, "layer": 1, "language": "en"},
        ttl_seconds=1,
    )
    cache_key = persistence.answer_cache_key("demo", "en", "hello")
    token = persistence.create_admin_token("expired", expires_in_days=1)
    persistence.persist_job({
        "job_id": "old-job",
        "kind": "dataset_processing",
        "status": "completed",
        "progress": 100,
        "message": "done",
        "created_at": old_iso,
        "started_at": old_iso,
        "finished_at": old_iso,
        "result": {},
        "error": None,
        "metadata": {"org_id": "demo"},
    })
    ticket = persistence.create_handoff_ticket(
        org_id="demo",
        query="q",
        response="r",
        intent="unknown",
        confidence=0,
        layer=3,
    )
    persistence.update_handoff_ticket(ticket["ticket_id"], "resolved", "done")
    persistence.record_audit_event("old_event")

    with persistence.connect() as conn:
        conn.execute("UPDATE answer_cache SET expires_at = ? WHERE cache_key = ?", (old_iso, cache_key))
        conn.execute("UPDATE admin_tokens SET expires_at = ? WHERE token_id = ?", (old_iso, token["token_id"]))
        conn.execute("UPDATE handoff_tickets SET resolved_at = ? WHERE ticket_id = ?", (old_iso, ticket["ticket_id"]))
        conn.execute("UPDATE audit_events SET created_at = ?", (old_iso,))

    result = persistence.run_maintenance()

    assert result["cleaned"]["expired_sessions"] == 1
    assert result["cleaned"]["expired_answer_cache"] == 1
    assert result["cleaned"]["expired_admin_tokens"] == 1
    assert result["cleaned"]["old_finished_jobs"] == 1
    assert result["cleaned"]["old_resolved_handoffs"] == 1
    assert result["cleaned"]["old_audit_events"] == 1
    assert persistence.count_persisted_sessions("demo") == 0
    assert persistence.count_answer_cache("demo") == 0
