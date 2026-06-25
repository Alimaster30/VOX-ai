# VOX Backup and Restore

VOX keeps customer data outside Git. Production backups must include organization profiles, uploaded datasets, generated chunks, active intents, and vector indexes.

## What Is Backed Up

By default, `backup_vox.py` includes:

- `organizations/` with organization profiles, documents, manifests, intents, and vector indexes
- `organizations/*/versions/` with dataset manifest/vector index rollback snapshots
- SQLite deployments: `runtime_cache/vox.sqlite3` with durable jobs, sessions, audit records, admin tokens, and handoff tickets
- PostgreSQL deployments: `database/postgres.sql`, created with `pg_dump`
- cached high-confidence answers used to speed up repeated questions
- deployment support files such as `requirements.txt`, `PRODUCTION.md`, `serve.py`, and run scripts
- database migration files under `src/migrations/`
- deployment templates under `deploy/` and `DEPLOYMENT_CHECKLIST.md`

It excludes:

- `.env` secrets unless `--include-secrets` is used
- virtual environments
- logs, Python caches, temporary runtime caches, and previous backups

Logs are operational evidence, not application state. Archive `logs/` separately only when you need incident history.

## Create A Backup

Windows:

```bat
.\.venv\Scripts\python.exe backup_vox.py
```

Linux:

```bash
./.venv/bin/python backup_vox.py
```

The archive is created in `backups/`:

```text
backups/vox_backup_YYYYMMDD_HHMMSS.zip
```

Check what will be included without creating a zip:

```bat
.\.venv\Scripts\python.exe backup_vox.py --dry-run
```

For PostgreSQL deployments, make sure `pg_dump` is available on `PATH` or pass it explicitly:

```bat
.\.venv\Scripts\python.exe backup_vox.py --pg-dump-path "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
```

Skip database contents when you only want organization files and deployment templates:

```bat
.\.venv\Scripts\python.exe backup_vox.py --skip-database
```

Include `.env` only for encrypted or offline storage:

```bat
.\.venv\Scripts\python.exe backup_vox.py --include-secrets
```

## Restore A Backup

Stop VOX before restoring.

Preview the restore first:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --dry-run
```

Restore organization data without replacing existing files:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip
```

Restore and replace existing organization files:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --overwrite
```

For PostgreSQL backups, database restore is intentionally separate and requires an explicit flag:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --restore-postgres-database --dry-run
```

Run the real PostgreSQL restore only after confirming `VOX_DATABASE_URL` points at the intended database:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --restore-postgres-database --psql-path "C:\Program Files\PostgreSQL\16\bin\psql.exe"
```

Restore `.env` only when the archive was created with `--include-secrets` and you trust the backup:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --include-secrets --overwrite
```

Restored database admin tokens keep their original expiration dates. Rotate old operator tokens after restoring a production backup.

After restore, confirm upload security settings in `.env`, especially `VOX_MAX_UPLOAD_MB` and any `VOX_UPLOAD_SCAN_COMMAND` used by the server.

Run `maintenance.py --dry-run` after restoring older backups to review expired sessions, cache rows, old jobs, and resolved handoffs before cleanup.

Support files such as `requirements.txt` and production notes are included in the archive for reference. Restore them only when you intentionally want to replace local deployment files:

```bat
.\.venv\Scripts\python.exe restore_vox.py backups\vox_backup_YYYYMMDD_HHMMSS.zip --include-support-files --overwrite
```

## After Restore

Start VOX and check:

- `/api/health`
- `/api/status`
- `/api/organizations`
- `/api/ollama/health`

If vector indexes were missing from an old backup, upload/process the dataset again from Organization Setup so VOX can rebuild chunks and embeddings.

If the SQLite database is missing from an older backup, VOX can still run from the organization files, but old job history, session records, and audit events will not be available.

If the PostgreSQL dump is missing from an archive, restore the organization files first, then use the latest managed database snapshot or `pg_dump` backup for runtime records.
