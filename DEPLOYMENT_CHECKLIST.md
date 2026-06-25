# VOX Deployment Checklist

Use this checklist when moving VOX from local development to a production server.

## Before Deployment

- Create `.env` from `.env.example`.
- Set `VOX_PRODUCTION=1`.
- Set strong values for `VOX_SECRET_KEY` and `VOX_ADMIN_TOKEN`.
- Choose the database backend. Use SQLite for a small first deployment, or configure PostgreSQL from `DATABASE.md` for production database operations.
- Pull Ollama models:

```bash
ollama pull qwen3.2:3b
ollama pull nomic-embed-text
```

- Install Python dependencies into `.venv`.
- Run tests:

```bat
.\.venv\Scripts\python.exe -m pytest -q
```

- Run database migrations:

```bat
.\.venv\Scripts\python.exe migrate_db.py
```

- Confirm GitHub Actions CI is passing for the branch being deployed.

- Run backup dry-run:

```bat
.\.venv\Scripts\python.exe backup_vox.py --dry-run
```

- Create a read-only monitoring token if Prometheus/Grafana will be used.

## Runtime Processes

For production, run at least:

- VOX web server: `serve.py`
- VOX background worker: `worker.py`
- VOX maintenance cleanup: scheduled `maintenance.py`
- Ollama service

Recommended `.env`:

```bash
VOX_JOB_MODE=external
VOX_HOST=127.0.0.1
VOX_PORT=5000
VOX_WAITRESS_THREADS=8
```

Use a reverse proxy for public HTTPS traffic.
Use `deploy/monitoring/docker-compose.yml` for optional Prometheus and Grafana monitoring.

## Windows

Run PowerShell as Administrator:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\install_services.ps1
```

Uninstall:

```powershell
.\deploy\windows\uninstall_services.ps1
```

The Windows scripts create:

- `VOXWeb`, displayed as `VOX Web`
- `VOXWorker`, displayed as `VOX Worker`
- `VOX Maintenance`, scheduled task

## Linux

Copy unit files:

```bash
sudo cp deploy/linux/systemd/*.service /etc/systemd/system/
sudo cp deploy/linux/systemd/*.timer /etc/systemd/system/
```

Edit paths and user names inside the copied files.

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vox-web.service
sudo systemctl enable --now vox-worker.service
sudo systemctl enable --now vox-maintenance.timer
```

Check:

```bash
systemctl status vox-web
systemctl status vox-worker
systemctl list-timers | grep vox
```

## Reverse Proxy

Use one of:

- `deploy/reverse-proxy/nginx.conf`
- `deploy/reverse-proxy/Caddyfile`

Set the proxy target to:

```text
http://127.0.0.1:5000
```

## After Deployment

Check:

- `/api/health`
- `/api/ready`
- `/api/status`
- `/api/metrics`
- `/api/ollama/health`
- `logs/vox-app.log`
- `logs/vox-worker.log`

Run the smoke test:

```bat
.\.venv\Scripts\python.exe smoke_test.py --base-url http://127.0.0.1:5000
```

For a stricter production check after models are loaded:

```bat
.\.venv\Scripts\python.exe smoke_test.py --base-url http://127.0.0.1:5000 --admin-token <read-or-admin-token> --require-models --require-metrics --require-ollama
```

Create a database admin token from the root env token, then use the database token for normal operator work.
If monitoring is enabled, confirm Prometheus can scrape `/api/metrics` and Grafana loads the VOX Overview dashboard.

## Routine Operations

- Backup before upgrades:

```bat
.\.venv\Scripts\python.exe backup_vox.py
```

- Preview maintenance:

```bat
.\.venv\Scripts\python.exe maintenance.py --dry-run
```

- Run maintenance:

```bat
.\.venv\Scripts\python.exe maintenance.py
```

Keep `.env` and backups protected. They may contain operational secrets or organization data.
