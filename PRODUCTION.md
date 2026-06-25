# VOX Production Run Notes

## 1. Configure environment

Copy `.env.example` to `.env`, then set real values:

```bash
VOX_PRODUCTION=1
VOX_SECRET_KEY=<long-random-secret>
VOX_ADMIN_TOKEN=<long-random-admin-token>
VOX_HOST=0.0.0.0
VOX_PORT=5000
VOX_WAITRESS_THREADS=8
```

Tune these for the server hardware:

```bash
VOX_MAX_CONCURRENT_STT=2
VOX_MAX_CONCURRENT_QUERIES=4
VOX_AUTOLOAD_MODELS=1
```

Upload hardening controls:

```bash
VOX_MAX_UPLOAD_MB=50
VOX_MAX_UPLOAD_PDF_PAGES=500
VOX_MAX_UPLOAD_SPREADSHEET_CELLS=250000
VOX_MAX_UPLOAD_ZIP_RATIO=20
VOX_UPLOAD_SCAN_COMMAND=
```

`VOX_UPLOAD_SCAN_COMMAND` is optional. If set, VOX appends the uploaded file path as the final argument and rejects the file when the command exits non-zero. Example:

```bash
VOX_UPLOAD_SCAN_COMMAND=clamscan --no-summary
```

Answer cache controls:

```bash
VOX_ANSWER_CACHE_ENABLED=1
VOX_ANSWER_CACHE_TTL_SECONDS=86400
VOX_ANSWER_CACHE_MIN_CONFIDENCE=0.65
```

The cache is per organization and language. VOX only caches non-handoff answers above the confidence threshold.

Text-to-speech controls:

```bash
VOX_TTS_ENGINE=kokoro
VOX_TTS_FALLBACK_ENGINE=none
VOX_TTS_KOKORO_VOICE_EN=af_heart
VOX_TTS_KOKORO_VOICE_UR=
```

`kokoro` is local-first and returns WAV audio to the browser. Set `VOX_TTS_FALLBACK_ENGINE` to `gtts` or `edge` only when network fallback is acceptable. If local TTS is unavailable and fallback is `none`, VOX still returns the text answer with no audio instead of blocking the call on an internet service.

Configure rotating logs:

```bash
VOX_LOG_DIR=./logs
VOX_LOG_LEVEL=INFO
VOX_LOG_MAX_BYTES=5242880
VOX_LOG_BACKUP_COUNT=5
VOX_MAINTENANCE_AUDIT_RETENTION_DAYS=30
VOX_MAINTENANCE_JOB_RETENTION_DAYS=14
VOX_MAINTENANCE_HANDOFF_RETENTION_DAYS=90
```

Database controls:

```bash
VOX_DB_BACKEND=sqlite
VOX_DB_PATH=./runtime_cache/vox.sqlite3
VOX_DATABASE_URL=
```

Use SQLite for local/small deployments. For production database operations and stronger worker concurrency, set `VOX_DB_BACKEND=postgres` and provide `VOX_DATABASE_URL`; see `DATABASE.md` before switching a live deployment.

Use the environment admin token as the bootstrap/root token. From it, create revocable database-backed admin tokens for operators:

```bash
curl -X POST http://localhost:5000/api/admin/tokens \
  -H "X-VOX-Admin-Token: <root-admin-token>" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Ops token\",\"org_id\":\"default\",\"expires_in_days\":90}"
```

VOX returns the new token once. Store it securely, then use it in `X-VOX-Admin-Token`.
Database-backed admin tokens expire after 90 days by default. Set `expires_in_days` between `1` and `3650` when creating a token.

Recommended scopes:

```json
["admin"]
```

Use this for operators who can upload/process datasets, publish intents, resolve handoffs, and manage organization setup.

```json
["read"]
```

Use this for monitoring/reporting access. Read-only tokens can inspect protected data but cannot mutate datasets, organizations, handoffs, intents, or model state.

## 2. Start VOX

Run database migrations before starting or restarting services:

```bat
.\.venv\Scripts\python.exe migrate_db.py
```

VOX also checks migrations on startup, but running this command separately gives a clearer deployment checkpoint.

Windows:

```bat
run_production.bat
```

Linux:

```bash
chmod +x run_production.sh
./run_production.sh
```

For service-based deployment, use `DEPLOYMENT_CHECKLIST.md`.
It includes Windows service scripts, Linux `systemd` templates, and reverse proxy examples.

After startup, run a smoke test:

```bat
.\.venv\Scripts\python.exe smoke_test.py --base-url http://127.0.0.1:5000
```

After models and monitoring are ready, run the stricter check:

```bat
.\.venv\Scripts\python.exe smoke_test.py --base-url http://127.0.0.1:5000 --admin-token <read-or-admin-token> --require-models --require-metrics --require-ollama
```

## 3. Caller/session isolation

For multiple callers, clients should send a stable call id:

```http
X-VOX-Call-ID: call-123
```

or:

```http
/api/voice?call_id=call-123
```

Each call id gets its own conversation history.
For multi-organization deployments, clients can target an organization per request:

```http
X-VOX-Org-ID: acme-health
```

or:

```http
/api/voice?org_id=acme-health&call_id=call-123
```

Sessions are isolated by both organization id and call id, so two organizations can safely use the same call id without sharing history.

Each organization has its own classifier, vector retriever, RAG prompt, and LLM runtime state. Load a non-default organization's runtime before sending live voice traffic:

```bash
curl -X POST http://localhost:5000/api/models/load \
  -H "X-VOX-Admin-Token: <admin-token>" \
  -H "X-VOX-Org-ID: acme-health"
```

Check readiness for that organization:

```bash
curl http://localhost:5000/api/ready -H "X-VOX-Org-ID: acme-health"
```

Model loading state is tracked per organization. If one organization's runtime fails to load, `/api/ready`, `/api/health`, and `/api/metrics` for other organizations can still report healthy state.

## 4. Capacity behavior

When VOX is at capacity, `/api/voice` returns `429` instead of blocking indefinitely.
Increase concurrency only when the CPU/GPU and Ollama server can handle it.

Before raising production limits, run the staging load tests in `LOAD_TESTING.md`.
Start with `/api/session` to measure concurrent caller/session behavior, then use `/api/voice` with a WAV file to test the full STT, Qwen, RAG, cache, and TTS path.

## 5. Monitoring

VOX exposes a Prometheus-style metrics endpoint:

```bash
curl http://localhost:5000/api/metrics -H "X-VOX-Admin-Token: <read-or-admin-token>"
```

Prometheus can also scrape it with:

```bash
Authorization: Bearer <read-or-admin-token>
```

Track these first:

- `vox_http_requests_total`
- `vox_http_request_latency_ms_avg`
- `vox_http_request_latency_ms_max`
- `vox_active_calls`
- `vox_active_stt_jobs`
- `vox_active_query_jobs`
- `vox_open_handoffs`
- `vox_answer_cache_entries`
- `vox_models_ready`

Use a read-only admin token for monitoring.
See `MONITORING.md` for Prometheus, Grafana, dashboard, and alert-rule setup.

## 6. Background Worker

By default, VOX runs dataset processing inside the Flask process:

```bash
VOX_JOB_MODE=inline
```

For production, move heavy dataset processing to a separate worker:

```bash
VOX_JOB_MODE=external
```

Start the web server as usual, then start one worker process.

Windows:

```bat
run_worker.bat
```

Linux:

```bash
chmod +x run_worker.sh
./run_worker.sh
```

In external mode, `/api/datasets/process` queues the job in SQLite and returns immediately. `worker.py` claims queued jobs and updates job status in the database.

Logs are written to:

```text
logs/vox-app.log
logs/vox-worker.log
```

## 7. Human Handoffs

When VOX cannot confidently answer or the caller asks for a human, it creates a handoff ticket in SQLite.

Admins can review open tickets:

```bash
curl http://localhost:5000/api/handoffs \
  -H "X-VOX-Admin-Token: <admin-token>"
```

Resolve a ticket:

```bash
curl -X PATCH http://localhost:5000/api/handoffs/<ticket_id> \
  -H "X-VOX-Admin-Token: <admin-token>" \
  -H "Content-Type: application/json" \
  -d "{\"status\":\"resolved\",\"notes\":\"Caller was contacted.\"}"
```

The Organization Setup screen also shows open handoffs.

## 8. Dataset Versions

Every successful dataset processing run creates a snapshot under the active organization:

```text
organizations/<org_id>/versions/<version_id>/
```

The snapshot includes the dataset manifest and vector index directory when available.

List versions:

```bash
curl http://localhost:5000/api/datasets/versions \
  -H "X-VOX-Admin-Token: <admin-token>"
```

Roll back:

```bash
curl -X POST http://localhost:5000/api/datasets/versions/<version_id>/rollback \
  -H "X-VOX-Admin-Token: <admin-token>"
```

The Organization Setup screen also shows dataset versions and rollback controls.

## 9. Maintenance Cleanup

Preview cleanup:

```bat
run_maintenance.bat --dry-run
```

Run cleanup:

```bat
run_maintenance.bat
```

Linux:

```bash
chmod +x run_maintenance.sh
./run_maintenance.sh --dry-run
./run_maintenance.sh
```

Maintenance removes expired sessions, expired answer-cache rows, old finished jobs, old audit events, and old resolved handoffs according to `.env` retention settings. Expired DB admin tokens are disabled, not deleted.

Root admins can also run a dry-run from the API:

```bash
curl -X POST http://localhost:5000/api/admin/maintenance \
  -H "X-VOX-Admin-Token: <root-admin-token>" \
  -H "Content-Type: application/json" \
  -d "{\"dry_run\":true}"
```

## 10. Backups

Organization datasets, generated intents, vector indexes, sessions, jobs, and audit records are runtime data. They are not stored in Git.

Create backups before upgrades or migrations:

```bat
.\.venv\Scripts\python.exe backup_vox.py
```

For PostgreSQL, this creates `database/postgres.sql` inside the backup archive using `pg_dump`. Pass `--pg-dump-path` if PostgreSQL tools are not on `PATH`.

Restore from a backup:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --dry-run
```

PostgreSQL database restore requires `--restore-postgres-database` so a database is never overwritten by accident.

See `BACKUP_RESTORE.md` for the full workflow and the `.env` secrets warning.
