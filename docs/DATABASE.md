# VOX Database Backends

VOX uses SQLite by default:

```env
VOX_DB_BACKEND=sqlite
VOX_DB_PATH=./runtime_cache/vox.sqlite3
```

SQLite is suitable for local development and small single-server deployments.

## PostgreSQL

For production deployments that need stronger concurrency, external backups, and database operations tooling, configure PostgreSQL:

```env
VOX_DB_BACKEND=postgres
VOX_DATABASE_URL=postgresql://vox:password@localhost:5432/vox
```

The PostgreSQL adapter uses the same persistence API as SQLite for organizations, jobs, sessions, audit events, admin tokens, handoff tickets, and answer cache entries.

## Migrations

Database schema changes live in `src/migrations/<backend>/` and are tracked in the `schema_migrations` table.

Run migrations during deployment:

```bat
.\.venv\Scripts\python.exe migrate_db.py
```

On Linux:

```bash
./.venv/bin/python migrate_db.py
```

VOX also applies pending migrations on startup, but running `migrate_db.py` first makes deployment failures easier to see before the web and worker processes start.

## PostgreSQL Integration Test

Run the live PostgreSQL test locally when Docker is available:

```bat
docker compose -f deploy/postgres/docker-compose.yml up -d
set VOX_TEST_POSTGRES_URL=postgresql://vox:vox@localhost:5432/vox_test
.\.venv\Scripts\python.exe -m pytest tests\test_postgres_integration.py -q
```

On Linux:

```bash
docker compose -f deploy/postgres/docker-compose.yml up -d
export VOX_TEST_POSTGRES_URL=postgresql://vox:vox@localhost:5432/vox_test
./.venv/bin/python -m pytest tests/test_postgres_integration.py -q
```

The integration test drops and recreates VOX tables in the configured database. Use only a disposable test database.

Before switching a live deployment:

- create the PostgreSQL database and user
- set `VOX_DATABASE_URL` from a secret manager or protected environment file
- run `migrate_db.py` with `VOX_DB_BACKEND=postgres` so VOX creates/updates its tables
- run the production smoke tests against that database
- confirm worker job claiming with at least two workers
- configure `pg_dump` backups with `backup_vox.py` or managed database backups

Remaining hardening work:

- add full application smoke tests against PostgreSQL in a staging environment
