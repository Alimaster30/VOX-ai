import importlib
import io
import os
import shutil
import sys
import time
from pathlib import Path

import pytest


def load_app(monkeypatch, admin_token=None):
    monkeypatch.setenv("VOX_AUTOLOAD_MODELS", "0")
    monkeypatch.delenv("VOX_PRODUCTION", raising=False)
    if admin_token is None:
        monkeypatch.delenv("VOX_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("VOX_ADMIN_TOKEN", admin_token)

    for name in list(sys.modules):
        if name == "app" or name.startswith("src.config") or name in {"src.persistence", "src.dataset_manager"}:
            sys.modules.pop(name, None)

    module = importlib.import_module("app")
    return module, module.app.test_client()


def load_app_with_env(monkeypatch, **env):
    monkeypatch.setenv("VOX_AUTOLOAD_MODELS", "0")
    monkeypatch.delenv("VOX_PRODUCTION", raising=False)
    monkeypatch.delenv("VOX_ADMIN_TOKEN", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))

    for name in list(sys.modules):
        if name == "app" or name.startswith("src.config") or name in {"src.persistence", "src.dataset_manager"}:
            sys.modules.pop(name, None)

    module = importlib.import_module("app")
    return module, module.app.test_client()


def remove_org(org_id):
    path = Path("organizations") / org_id
    if path.exists():
        shutil.rmtree(path)


def test_admin_token_protects_setup_routes(monkeypatch):
    _, client = load_app(monkeypatch, admin_token="secret-token")

    assert client.get("/api/admin/check").status_code == 401
    assert client.get("/api/admin/check", headers={"X-VOX-Admin-Token": "secret-token"}).status_code == 200
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/datasets", headers={"X-VOX-Admin-Token": "bad"}).status_code == 401
    assert client.get("/api/datasets", headers={"X-VOX-Admin-Token": "secret-token"}).status_code == 200


def test_create_and_switch_organization(monkeypatch):
    org_id = "pytest-switch-org"
    remove_org(org_id)
    app_module, client = load_app(monkeypatch)

    created = client.post(
        "/api/organizations",
        json={
            "org_id": org_id,
            "organization_name": "Pytest Switch Org",
            "domain": "testing",
        },
    )
    assert created.status_code == 201
    assert (Path("organizations") / org_id / "profile.json").exists()
    assert (Path("organizations") / org_id / "intents.json").exists()

    switched = client.post("/api/organizations/switch", json={"org_id": org_id})
    assert switched.status_code == 200
    assert switched.json["active_org_id"] == org_id
    assert app_module.ORG_PROFILE["org_id"] == org_id
    assert app_module.USE_LEGACY_HANDLER is False

    status = client.get("/api/status").json
    assert status["org_id"] == org_id
    assert status["active_intents"]["intent_count"] == 0

    back = client.post("/api/organizations/switch", json={"org_id": "default"})
    assert back.status_code == 200
    remove_org(org_id)


def test_switch_refuses_when_calls_are_active(monkeypatch):
    org_id = "pytest-busy-org"
    remove_org(org_id)
    app_module, client = load_app(monkeypatch)
    client.post("/api/organizations", json={"org_id": org_id, "organization_name": "Busy Org"})

    app_module.runtime_counter("calls", 1)
    try:
        response = client.post("/api/organizations/switch", json={"org_id": org_id})
        assert response.status_code == 409
        assert "calls are active" in response.json["error"]
    finally:
        app_module.runtime_counter("calls", -1)
        remove_org(org_id)


def test_intent_merge_publish_helper(tmp_path):
    from src.intent_generator import merge_intent_lists

    active = [{
        "tag": "support",
        "patterns": ["contact support"],
        "responses_urdu": [],
        "responses_english": ["Old answer"],
    }]
    draft = [{
        "tag": "support",
        "patterns": ["contact support", "help desk"],
        "responses_urdu": [],
        "responses_english": ["New answer"],
    }]

    merged = merge_intent_lists(active, draft)
    assert merged[0]["patterns"] == ["contact support", "help desk"]
    assert merged[0]["responses_english"] == ["Old answer", "New answer"]


def test_job_manager_lifecycle():
    from src.jobs import JobManager

    manager = JobManager()
    job = manager.start("unit", lambda current: {"ok": True})

    deadline = time.time() + 2
    while time.time() < deadline:
        current = manager.get(job.job_id)
        if current and current.status == "completed":
            break
        time.sleep(0.05)

    current = manager.get(job.job_id)
    assert current is not None
    assert current.status == "completed"
    assert current.progress == 100
    assert current.result == {"ok": True}


def test_call_id_isolates_sessions(monkeypatch):
    _, client = load_app(monkeypatch)

    a = client.get("/api/session?call_id=caller-a").json
    b = client.get("/api/session?call_id=caller-b").json
    status = client.get("/api/status?call_id=caller-a").json

    assert a["session_id"] == "caller-a"
    assert b["session_id"] == "caller-b"
    assert status["active_sessions"] >= 2


def test_request_org_header_reads_org_without_switching(monkeypatch):
    org_id = "pytest-request-org"
    remove_org(org_id)
    app_module, client = load_app(monkeypatch)
    client.post("/api/organizations", json={"org_id": org_id, "organization_name": "Request Org"})

    try:
        response = client.get("/api/status", headers={"X-VOX-Org-ID": org_id})

        assert response.status_code == 200
        assert response.json["org_id"] == org_id
        assert response.json["organization_name"] == "Request Org"
        assert app_module.ORG_PROFILE["org_id"] == "default"
    finally:
        remove_org(org_id)


def test_same_call_id_isolated_by_request_org(monkeypatch):
    org_id = "pytest-session-org"
    remove_org(org_id)
    _, client = load_app(monkeypatch)
    client.post("/api/organizations", json={"org_id": org_id, "organization_name": "Session Org"})

    try:
        default_session = client.get("/api/session?call_id=same-caller").json
        org_session = client.get(
            "/api/session?call_id=same-caller",
            headers={"X-VOX-Org-ID": org_id},
        ).json
        default_status = client.get("/api/status?call_id=same-caller").json
        org_status = client.get(
            "/api/status?call_id=same-caller",
            headers={"X-VOX-Org-ID": org_id},
        ).json

        assert default_session["session_id"] == org_session["session_id"] == "same-caller"
        assert default_status["org_id"] == "default"
        assert org_status["org_id"] == org_id
        assert default_status["persisted_sessions"] >= 1
        assert org_status["persisted_sessions"] >= 1
    finally:
        remove_org(org_id)


def test_upload_rejects_invalid_pdf_signature(monkeypatch):
    _, client = load_app(monkeypatch)

    data = {
        "files": (io.BytesIO(b"not actually a pdf"), "fake.pdf"),
    }
    response = client.post("/api/datasets/upload", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "Invalid PDF file signature" in response.json["errors"][0]["error"]


def test_upload_rejects_malformed_pdf(monkeypatch):
    _, client = load_app(monkeypatch)

    data = {
        "files": (io.BytesIO(b"%PDF-1.7\nnot a valid pdf body"), "broken.pdf"),
    }
    response = client.post("/api/datasets/upload", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "PDF could not be parsed safely" in response.json["errors"][0]["error"]


def test_upload_rejects_csv_formula_cells(monkeypatch):
    _, client = load_app(monkeypatch)

    data = {
        "files": (io.BytesIO(b"name,value\nsafe,=HYPERLINK(\"http://bad\")\n"), "formula.csv"),
    }
    response = client.post("/api/datasets/upload", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "Spreadsheet formula-like cell rejected" in response.json["errors"][0]["error"]


def test_upload_scan_hook_can_reject_file(monkeypatch):
    scan_command = f'"{sys.executable}" -c "import sys; sys.exit(1)"'
    _, client = load_app_with_env(monkeypatch, VOX_UPLOAD_SCAN_COMMAND=scan_command)

    data = {
        "files": (io.BytesIO(b"plain text"), "scan-me.txt"),
    }
    response = client.post("/api/datasets/upload", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "Upload scan failed" in response.json["errors"][0]["error"]


def test_duplicate_upload_is_rejected(monkeypatch):
    org_id = "pytest-upload-org"
    remove_org(org_id)
    app_module, client = load_app(monkeypatch)
    client.post("/api/organizations", json={"org_id": org_id, "organization_name": "Upload Org"})
    client.post("/api/organizations/switch", json={"org_id": org_id})

    try:
        first = client.post(
            "/api/datasets/upload",
            data={"files": (io.BytesIO(b"hello,world\n1,2\n"), "data.csv")},
            content_type="multipart/form-data",
        )
        assert first.status_code == 200

        second = client.post(
            "/api/datasets/upload",
            data={"files": (io.BytesIO(b"hello,world\n1,2\n"), "data.csv")},
            content_type="multipart/form-data",
        )
        assert second.status_code == 400
        assert "already been uploaded" in second.json["errors"][0]["error"]
    finally:
        client.post("/api/organizations/switch", json={"org_id": "default"})
        remove_org(org_id)


def test_voice_rate_limit(monkeypatch):
    app_module, client = load_app_with_env(monkeypatch, VOX_RATE_LIMIT_VOICE=1)

    first = client.post("/api/voice?call_id=limited", data=b"audio")
    second = client.post("/api/voice?call_id=limited", data=b"audio")

    assert first.status_code == 503
    assert second.status_code == 429
    assert second.json["error"] == "Rate limit exceeded"


def test_generate_audio_returns_tts_metadata(monkeypatch):
    app_module, _ = load_app(monkeypatch)

    def fake_synthesize(**kwargs):
        return {
            "audio_base64": "abc",
            "audio_mime": "audio/wav",
            "tts_engine": "kokoro",
            "tts_error": None,
        }

    monkeypatch.setattr("src.local_tts.synthesize_speech", fake_synthesize)

    result = app_module.generate_audio("Hello!", "en")

    assert result["audio_base64"] == "abc"
    assert result["audio_mime"] == "audio/wav"
    assert result["tts_engine"] == "kokoro"
    assert result["tts_error"] is None


def test_ollama_health_route_reports_missing_models(monkeypatch):
    app_module, client = load_app(monkeypatch)

    health = {
        "reachable": True,
        "required_models": ["qwen3.2:3b", "nomic-embed-text"],
        "available_models": ["qwen3.2:3b"],
        "missing_models": ["nomic-embed-text"],
        "error": None,
    }
    monkeypatch.setattr(app_module, "check_ollama_health", lambda llm, embed: health)
    app_module.ollama_health_cache["data"] = None

    response = client.get("/api/ollama/health?force=1")
    assert response.status_code == 200
    assert response.json["reachable"] is True
    assert response.json["missing_models"] == ["nomic-embed-text"]


def test_intent_generation_requires_ollama(monkeypatch):
    app_module, client = load_app(monkeypatch)
    app_module.ollama_health_cache["data"] = None
    monkeypatch.setattr(
        app_module,
        "check_ollama_health",
        lambda llm, embed: {
            "reachable": False,
            "required_models": [llm, embed],
            "available_models": [],
            "missing_models": [llm, embed],
            "error": "connection refused",
        },
    )
    monkeypatch.setattr(app_module, "load_manifest", lambda profile: {"chunks": [{"text": "hello"}]})

    response = client.post("/api/intents/generate", json={"max_intents": 1})
    assert response.status_code == 503
    assert "Ollama is not reachable" in response.json["error"]


def test_health_endpoint_and_request_id_header(monkeypatch):
    _, client = load_app(monkeypatch)

    response = client.get("/api/health", headers={"X-Request-ID": "req-test-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test-123"
    assert response.json["request_id"] == "req-test-123"
    assert response.json["status"] == "ok"
    assert response.json["org_id"]


def test_admin_error_includes_request_id(monkeypatch):
    _, client = load_app(monkeypatch, admin_token="secret-token")

    response = client.get("/api/datasets", headers={"X-Request-ID": "req-denied"})
    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "req-denied"
    assert response.json["request_id"] == "req-denied"


def test_bearer_admin_token_is_accepted(monkeypatch):
    _, client = load_app(monkeypatch, admin_token="secret-token")

    response = client.get("/api/metrics", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert "vox_app_info" in response.get_data(as_text=True)


def test_call_id_is_returned_in_response_headers(monkeypatch):
    _, client = load_app(monkeypatch)

    response = client.get("/api/session", headers={"X-VOX-Call-ID": "call-header-1"})
    assert response.status_code == 200
    assert response.headers["X-VOX-Call-ID"] == "call-header-1"
    assert response.json["session_id"] == "call-header-1"


def test_session_is_persisted(monkeypatch):
    app_module, client = load_app(monkeypatch)

    response = client.get("/api/session", headers={"X-VOX-Call-ID": "persisted-call-1"})
    status = client.get("/api/status", headers={"X-VOX-Call-ID": "persisted-call-1"})

    assert response.status_code == 200
    assert status.status_code == 200
    assert status.json["persisted_sessions"] >= 1

    from src.persistence import count_persisted_sessions

    assert count_persisted_sessions(app_module.ORG_PROFILE["org_id"]) >= 1


def test_admin_audit_records_requests(monkeypatch):
    _, client = load_app(monkeypatch, admin_token="secret-token")

    client.get("/api/status", headers={"X-Request-ID": "audit-req-1"})
    response = client.get(
        "/api/admin/audit?limit=10",
        headers={"X-VOX-Admin-Token": "secret-token"},
    )

    assert response.status_code == 200
    assert any(event["request_id"] == "audit-req-1" for event in response.json["events"])


def test_metrics_endpoint_reports_runtime_and_request_counts(monkeypatch):
    app_module, client = load_app(monkeypatch)

    app_module.runtime_counter("calls", 1)
    try:
        client.get("/api/session?call_id=metrics-call")
        response = client.get("/api/metrics")
    finally:
        app_module.runtime_counter("calls", -1)

    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    assert 'vox_app_info{org_id="default",assistant="VOX"} 1' in text
    assert 'vox_http_requests_total{method="GET",path="/api/session",status="200"}' in text
    assert "vox_active_calls 1" in text
    assert "vox_active_sessions" in text
    assert "vox_answer_cache_entries" in text
    assert "vox_models_ready" in text


def test_metrics_endpoint_uses_read_auth(monkeypatch, tmp_path):
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    created = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Metrics reader", "scopes": ["read"], "expires_in_days": 30},
    )
    denied = client.get("/api/metrics")
    allowed = client.get("/api/metrics", headers={"X-VOX-Admin-Token": created.json["token"]})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert "vox_http_requests_total" in allowed.get_data(as_text=True)


def test_model_state_is_isolated_by_request_org(monkeypatch):
    org_id = "pytest-model-state-org"
    remove_org(org_id)
    app_module, client = load_app(monkeypatch)
    client.post("/api/organizations", json={"org_id": org_id, "organization_name": "Model State Org"})

    try:
        app_module.update_model_state("default", ready=False, loading=False, error=None)
        app_module.update_model_state(org_id, ready=False, loading=True, error="missing model")

        default_health = client.get("/api/health")
        org_health = client.get("/api/health", headers={"X-VOX-Org-ID": org_id})
        default_ready = client.get("/api/ready")
        org_ready = client.get("/api/ready", headers={"X-VOX-Org-ID": org_id})
        default_metrics = client.get("/api/metrics").get_data(as_text=True)
        org_metrics = client.get("/api/metrics", headers={"X-VOX-Org-ID": org_id}).get_data(as_text=True)

        assert default_health.status_code == 200
        assert default_health.json["models"]["error"] is None
        assert org_health.status_code == 503
        assert org_health.json["models"]["error"] == "missing model"
        assert default_ready.json["models_loading"] is False
        assert default_ready.json["models_error"] is None
        assert org_ready.json["models_loading"] is True
        assert org_ready.json["models_error"] == "missing model"
        assert "vox_models_error 0" in default_metrics
        assert "vox_models_error 1" in org_metrics
        assert "vox_models_loading 1" in org_metrics
    finally:
        remove_org(org_id)


def test_admin_maintenance_endpoint_requires_root(monkeypatch, tmp_path):
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    created = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Read only", "scopes": ["read"], "expires_in_days": 30},
    )
    denied = client.post(
        "/api/admin/maintenance",
        headers={"X-VOX-Admin-Token": created.json["token"]},
        json={"dry_run": True},
    )
    allowed = client.post(
        "/api/admin/maintenance",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"dry_run": True},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json["dry_run"] is True


def test_job_endpoint_reads_persisted_job(monkeypatch):
    app_module, client = load_app(monkeypatch)
    from src.persistence import persist_job

    persisted = {
        "job_id": "persisted-job-1",
        "kind": "dataset_processing",
        "status": "completed",
        "progress": 100,
        "message": "Completed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": None,
        "finished_at": "2026-01-01T00:00:02+00:00",
        "result": {"ok": True},
        "error": None,
        "metadata": {"org_id": app_module.ORG_PROFILE["org_id"]},
    }
    persist_job(persisted)

    response = client.get("/api/jobs/persisted-job-1")

    assert response.status_code == 200
    assert response.json["job_id"] == "persisted-job-1"
    assert response.json["result"] == {"ok": True}


def test_dataset_process_queues_job_in_external_mode(monkeypatch, tmp_path):
    db_path = tmp_path / "vox.sqlite3"
    app_module, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(db_path),
        VOX_JOB_MODE="external",
    )

    response = client.post("/api/datasets/process")

    assert response.status_code == 202
    assert response.json["status"] == "queued"
    assert response.json["metadata"]["org_id"] == app_module.ORG_PROFILE["org_id"]

    from src.persistence import claim_next_queued_job

    claimed = claim_next_queued_job("pytest-worker", kinds={"dataset_processing"})
    assert claimed["job_id"] == response.json["job_id"]
    assert claimed["status"] == "running"
    assert claimed["metadata"]["worker_id"] == "pytest-worker"


def test_database_admin_token_can_be_created_and_revoked(monkeypatch, tmp_path):
    app_module, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    created = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Ops token", "org_id": app_module.ORG_PROFILE["org_id"]},
    )
    assert created.status_code == 201
    raw_token = created.json["token"]
    assert raw_token.startswith("vox_")
    assert created.json["expires_at"]

    listed = client.get("/api/admin/tokens", headers={"X-VOX-Admin-Token": "root-token"})
    assert listed.status_code == 200
    assert listed.json["tokens"][0]["token_id"] == created.json["token_id"]
    assert "token" not in listed.json["tokens"][0]

    allowed = client.get("/api/datasets", headers={"X-VOX-Admin-Token": raw_token})
    assert allowed.status_code == 200

    revoked = client.delete(
        f"/api/admin/tokens/{created.json['token_id']}",
        headers={"X-VOX-Admin-Token": "root-token"},
    )
    assert revoked.status_code == 200

    rejected = client.get("/api/datasets", headers={"X-VOX-Admin-Token": raw_token})
    assert rejected.status_code == 401


def test_expired_database_admin_token_is_rejected(monkeypatch, tmp_path):
    app_module, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    created = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Short token", "org_id": app_module.ORG_PROFILE["org_id"], "expires_in_days": 1},
    )
    assert created.status_code == 201

    from src.persistence import connect

    with connect() as conn:
        conn.execute(
            "UPDATE admin_tokens SET active = 1, expires_at = ? WHERE token_id = ?",
            ("2000-01-01T00:00:00+00:00", created.json["token_id"]),
        )

    rejected = client.get("/api/datasets", headers={"X-VOX-Admin-Token": created.json["token"]})
    assert rejected.status_code == 401

    listed = client.get("/api/admin/tokens", headers={"X-VOX-Admin-Token": "root-token"})
    expired = next(token for token in listed.json["tokens"] if token["token_id"] == created.json["token_id"])
    assert expired["expired"] is True
    assert expired["active"] is False


def test_admin_token_expiry_validation(monkeypatch, tmp_path):
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    response = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Bad expiry", "expires_in_days": 0},
    )

    assert response.status_code == 400
    assert "expires_in_days" in response.json["error"]


def test_read_only_admin_token_cannot_mutate(monkeypatch, tmp_path):
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    created = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Read only", "scopes": ["read"], "expires_in_days": 30},
    )
    assert created.status_code == 201
    token = created.json["token"]

    read_response = client.get("/api/datasets", headers={"X-VOX-Admin-Token": token})
    assert read_response.status_code == 200

    write_response = client.post(
        "/api/organizations",
        headers={"X-VOX-Admin-Token": token},
        json={"org_id": "read-only-denied", "organization_name": "Denied"},
    )
    assert write_response.status_code == 403
    assert "required scope" in write_response.json["error"]


def test_invalid_admin_scope_is_rejected(monkeypatch, tmp_path):
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    response = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Bad scope", "scopes": ["galaxy_admin"]},
    )

    assert response.status_code == 400
    assert "Invalid admin scopes" in response.json["error"]


def test_org_scoped_admin_token_rejects_wrong_active_org(monkeypatch, tmp_path):
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )

    created = client.post(
        "/api/admin/tokens",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"name": "Other org token", "org_id": "some-other-org"},
    )
    assert created.status_code == 201

    response = client.get("/api/datasets", headers={"X-VOX-Admin-Token": created.json["token"]})

    assert response.status_code == 403
    assert "not authorized" in response.json["error"]


def test_org_scoped_admin_token_allows_matching_request_org(monkeypatch, tmp_path):
    org_id = "pytest-token-org"
    remove_org(org_id)
    _, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )
    client.post(
        "/api/organizations",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"org_id": org_id, "organization_name": "Token Org"},
    )

    try:
        created = client.post(
            "/api/admin/tokens",
            headers={"X-VOX-Admin-Token": "root-token"},
            json={"name": "Org token", "org_id": org_id, "scopes": ["read"]},
        )
        response = client.get(
            "/api/datasets",
            headers={"X-VOX-Admin-Token": created.json["token"], "X-VOX-Org-ID": org_id},
        )

        assert response.status_code == 200
        assert response.json["org_id"] == org_id
    finally:
        remove_org(org_id)


def test_handoff_ticket_list_and_update(monkeypatch, tmp_path):
    app_module, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )
    from src.persistence import create_handoff_ticket

    ticket = create_handoff_ticket(
        org_id=app_module.ORG_PROFILE["org_id"],
        session_id="call-1",
        call_id="call-1",
        request_id="req-handoff",
        query="Can I talk to someone?",
        response="Please contact support.",
        intent="unknown",
        confidence=0.0,
        layer=3,
        language="en",
        department="Support",
        contact={"phone": "+1"},
    )

    listed = client.get("/api/handoffs", headers={"X-VOX-Admin-Token": "root-token"})
    assert listed.status_code == 200
    assert listed.json["open_count"] == 1
    assert listed.json["tickets"][0]["ticket_id"] == ticket["ticket_id"]

    updated = client.patch(
        f"/api/handoffs/{ticket['ticket_id']}",
        headers={"X-VOX-Admin-Token": "root-token"},
        json={"status": "resolved", "notes": "Called back."},
    )

    assert updated.status_code == 200
    assert updated.json["status"] == "resolved"
    assert updated.json["notes"] == "Called back."

    open_list = client.get("/api/handoffs", headers={"X-VOX-Admin-Token": "root-token"})
    assert open_list.json["open_count"] == 0


def test_low_confidence_query_creates_handoff(monkeypatch, tmp_path):
    app_module, _ = load_app_with_env(monkeypatch, VOX_DB_PATH=str(tmp_path / "vox.sqlite3"))

    result = {
        "response": "I do not know.",
        "intent": "unknown",
        "confidence": 0.0,
        "layer": 3,
        "language": "en",
    }
    with app_module.app.test_request_context("/api/voice", headers={"X-Request-ID": "req-low", "X-VOX-Call-ID": "call-low"}):
        app_module.attach_request_context()
        ticket = app_module.create_handoff_for_query("call-low", "unknown question", result)

    assert ticket["query"] == "unknown question"
    assert ticket["intent"] == "unknown"
    assert ticket["call_id"] == "call-low"

    from src.persistence import count_handoff_tickets

    assert count_handoff_tickets(app_module.ORG_PROFILE["org_id"]) == 1


def test_dataset_versions_api_lists_versions(monkeypatch, tmp_path):
    app_module, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ADMIN_TOKEN="root-token",
    )
    from src.dataset_versions import create_dataset_version, version_dir
    from src.dataset_manager import load_manifest

    version = create_dataset_version(app_module.ORG_PROFILE, load_manifest(app_module.ORG_PROFILE))
    try:
        response = client.get("/api/datasets/versions", headers={"X-VOX-Admin-Token": "root-token"})

        assert response.status_code == 200
        assert any(item["version_id"] == version["version_id"] for item in response.json["versions"])
    finally:
        path = version_dir(app_module.ORG_PROFILE, version["version_id"])
        if path.exists():
            shutil.rmtree(path)


def test_process_query_uses_answer_cache(monkeypatch, tmp_path):
    app_module, _ = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ANSWER_CACHE_MIN_CONFIDENCE="0.5",
    )

    class DummyClassifier:
        calls = 0

        def get_response(self, query, language="en"):
            self.calls += 1
            return "Cached answer", "faq", 0.9

    dummy = DummyClassifier()
    app_module.classifier = dummy
    app_module.handler = None
    app_module.USE_LEGACY_HANDLER = False

    first = app_module.process_query("What are your hours?", [], detected_lang="en")
    second = app_module.process_query("what are your hours", [], detected_lang="en")

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["response"] == "Cached answer"
    assert dummy.calls == 1


def test_process_query_uses_request_org_runtime(monkeypatch, tmp_path):
    org_id = "pytest-runtime-org"
    remove_org(org_id)
    app_module, client = load_app_with_env(
        monkeypatch,
        VOX_DB_PATH=str(tmp_path / "vox.sqlite3"),
        VOX_ANSWER_CACHE_ENABLED="0",
    )
    client.post("/api/organizations", json={"org_id": org_id, "organization_name": "Runtime Org"})

    class DummyClassifier:
        def __init__(self, response):
            self.response = response
            self.index = object()

        def get_response(self, query, language="en"):
            return self.response, "faq", 0.9

    def fake_runtime(profile, response):
        return {
            **app_module.org_runtime_config(profile),
            "handler": None,
            "classifier": DummyClassifier(response),
            "db": None,
            "retriever": None,
            "llm": None,
            "rag_prompt": None,
            "loaded_at": 1,
            "error": None,
        }

    try:
        default_profile = app_module.ORG_PROFILE
        org_profile = app_module.load_org_profile(org_id)
        app_module.org_runtimes["default"] = fake_runtime(default_profile, "Default answer")
        app_module.org_runtimes[org_id] = fake_runtime(org_profile, "Org answer")

        with app_module.app.test_request_context("/api/session"):
            app_module.attach_request_context()
            default_result = app_module.process_query("hours", [], detected_lang="en")

        with app_module.app.test_request_context("/api/session", headers={"X-VOX-Org-ID": org_id}):
            app_module.attach_request_context()
            org_result = app_module.process_query("hours", [], detected_lang="en")

        assert default_result["response"] == "Default answer"
        assert org_result["response"] == "Org answer"
    finally:
        remove_org(org_id)
