import json
from pathlib import Path

from backup_vox import collect_backup_files


def test_deployment_artifacts_exist():
    required = [
        "README.md",
        "docs/DEPLOYMENT_CHECKLIST.md",
        "deploy/windows/install_services.ps1",
        "deploy/windows/uninstall_services.ps1",
        "deploy/linux/systemd/vox-web.service",
        "deploy/linux/systemd/vox-worker.service",
        "deploy/linux/systemd/vox-maintenance.service",
        "deploy/linux/systemd/vox-maintenance.timer",
        "deploy/reverse-proxy/nginx.conf",
        "deploy/reverse-proxy/Caddyfile",
        "deploy/postgres/docker-compose.yml",
        "deploy/monitoring/docker-compose.yml",
        "deploy/monitoring/prometheus/prometheus.yml",
        "deploy/monitoring/prometheus/vox-alerts.yml",
        "deploy/monitoring/grafana/provisioning/datasources/prometheus.yml",
        "deploy/monitoring/grafana/provisioning/dashboards/vox.yml",
        "deploy/monitoring/grafana/dashboards/vox-overview.json",
        ".github/workflows/ci.yml",
        "requirements-ci.txt",
        "docs/CI.md",
        "docs/DATABASE.md",
        "docs/BACKUP_RESTORE.md",
        "smoke_test.py",
        "migrate_db.py",
        "src/migrations/sqlite/001_initial_schema.sql",
        "src/migrations/sqlite/002_admin_token_expires.sql",
        "src/migrations/postgres/001_initial_schema.sql",
        "src/migrations/postgres/002_admin_token_expires.sql",
    ]

    for path in required:
        assert Path(path).exists(), path


def test_backup_includes_deployment_templates():
    root = Path.cwd().resolve()
    files = {path.resolve().relative_to(root).as_posix() for path in collect_backup_files()}

    assert "README.md" in files
    assert "docs/DEPLOYMENT_CHECKLIST.md" in files
    assert "deploy/linux/systemd/vox-web.service" in files
    assert "deploy/windows/install_services.ps1" in files
    assert "deploy/reverse-proxy/nginx.conf" in files
    assert "deploy/postgres/docker-compose.yml" in files
    assert "deploy/monitoring/prometheus/prometheus.yml" in files
    assert "docs/MONITORING.md" in files
    assert ".github/workflows/ci.yml" in files
    assert "requirements-ci.txt" in files
    assert "docs/CI.md" in files
    assert "docs/DATABASE.md" in files
    assert "docs/BACKUP_RESTORE.md" in files
    assert "smoke_test.py" in files
    assert "migrate_db.py" in files
    assert "src/migrations/sqlite/001_initial_schema.sql" in files
    assert "src/migrations/postgres/001_initial_schema.sql" in files


def test_monitoring_artifacts_are_wired_to_vox_metrics():
    prometheus_config = Path("deploy/monitoring/prometheus/prometheus.yml").read_text(encoding="utf-8")
    alert_rules = Path("deploy/monitoring/prometheus/vox-alerts.yml").read_text(encoding="utf-8")
    dashboard = json.loads(Path("deploy/monitoring/grafana/dashboards/vox-overview.json").read_text(encoding="utf-8"))

    assert "metrics_path: /api/metrics" in prometheus_config
    assert "credentials_file: /etc/prometheus/secrets/vox_metrics_token" in prometheus_config
    assert "VOXModelsNotReady" in alert_rules
    assert "VOXHighServerErrorRate" in alert_rules
    assert dashboard["title"] == "VOX Overview"
    assert any(panel["title"] == "Models Ready" for panel in dashboard["panels"])


def test_ci_workflow_runs_lightweight_test_suite():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    ci_requirements = Path("requirements-ci.txt").read_text(encoding="utf-8")

    assert "requirements-ci.txt" in workflow
    assert "python -m py_compile app.py backup_vox.py restore_vox.py smoke_test.py migrate_db.py maintenance.py load_test.py" in workflow
    assert "python -m pytest -q" in workflow
    assert "PostgreSQL Integration" in workflow
    assert "postgres:16" in workflow
    assert "tests/test_postgres_integration.py" in workflow
    assert "VOX_TEST_POSTGRES_URL" in workflow
    assert "VOX_AUTOLOAD_MODELS" in workflow
    assert "psycopg" in ci_requirements.lower()
    assert "torch" not in ci_requirements.lower()
    assert "openai-whisper" not in ci_requirements.lower()
