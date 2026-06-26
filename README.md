# VOX AI

VOX AI is a multi-organization voice assistant platform for turning an organization's own documents into an automated call/query assistant.

It is designed for organizations that want a private, configurable assistant instead of a hardcoded IVR flow. An operator uploads a dataset for an organization, VOX processes the files, creates chunks and embeddings, generates draft intents, and uses a layered response pipeline to answer caller queries through speech or text.

## What VOX Does

- Supports multiple organizations from one deployment.
- Uploads and processes organization-specific datasets.
- Builds chunks, embeddings, vector indexes, and intent drafts.
- Uses local Qwen through Ollama for LLM responses.
- Uses retrieval-augmented generation when exact intents are not enough.
- Handles concurrent callers with per-call sessions.
- Supports local-first text-to-speech with Kokoro.
- Stores jobs, sessions, audit events, tokens, handoff tickets, and answer cache in SQLite or PostgreSQL.
- Includes production tooling for workers, migrations, backups, monitoring, load tests, and smoke tests.

## How The System Works

VOX follows a layered assistant pipeline:

1. Organization setup creates a profile and runtime folders.
2. Dataset upload stores source files for that organization.
3. Processing extracts text, chunks it, and builds embeddings/vector indexes.
4. Intent generation creates draft intents from the processed dataset.
5. Operators review and publish generated intents.
6. Caller queries are routed through fast intent matching, retrieval, LLM fallback, caching, and handoff logic.
7. If the answer is not reliable enough, VOX can create a human handoff ticket instead of inventing an answer.

The goal is to keep responses useful while avoiding unsafe guesses when the dataset does not contain enough verified context.

## Core Stack

- Python + Flask
- Waitress for production serving
- Ollama with `qwen3.2:3b`
- Chroma / vector search
- SQLite by default, PostgreSQL for production database operations
- Whisper-based speech-to-text
- Kokoro local-first text-to-speech
- Prometheus and Grafana monitoring templates

## Quick Start

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu121
```

Pull the local models:

```powershell
ollama pull qwen3.2:3b
ollama pull nomic-embed-text
```

Copy the environment template:

```powershell
copy .env.example .env
```

Run database migrations:

```powershell
.\.venv\Scripts\python.exe migrate_db.py
```

Start the app:

```powershell
.\.venv\Scripts\python.exe serve.py
```

Open:

```text
http://127.0.0.1:5000
```

## Production Mode

For production, set real secrets in `.env`:

```env
VOX_PRODUCTION=1
VOX_SECRET_KEY=<long-random-secret>
VOX_ADMIN_TOKEN=<long-random-admin-token>
VOX_JOB_MODE=external
```

Run the web process and worker separately:

```powershell
.\.venv\Scripts\python.exe serve.py
.\.venv\Scripts\python.exe worker.py
```

After startup, run:

```powershell
.\.venv\Scripts\python.exe smoke_test.py --base-url http://127.0.0.1:5000
```

## Tests

Run the normal suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The PostgreSQL integration test is optional and requires `VOX_TEST_POSTGRES_URL`.

## Documentation

Production and operations documentation lives in `docs/`:

- `docs/PRODUCTION.md` - production run notes
- `docs/DEPLOYMENT_CHECKLIST.md` - deployment checklist
- `docs/DATABASE.md` - SQLite/PostgreSQL and migrations
- `docs/BACKUP_RESTORE.md` - backup and restore workflow
- `docs/MONITORING.md` - Prometheus/Grafana setup
- `docs/LOAD_TESTING.md` - concurrency and load testing
- `docs/CI.md` - GitHub Actions details
- `docs/LEGACY.md` - old demo files and assumptions

## Current Status

VOX is production-oriented but should still be validated in a staging environment with real models, real organization data, and expected caller concurrency before public deployment.

## License

MIT License. See `LICENSE`.
