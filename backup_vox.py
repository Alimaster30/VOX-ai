import argparse
import fnmatch
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from src.config import SETTINGS, resolve_project_path


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT / "backups"
POSTGRES_DUMP_ARCHIVE_NAME = "database/postgres.sql"

SUPPORT_FILES = (
    ".env.example",
    "requirements.txt",
    "requirements-ci.txt",
    "README.md",
    "docs/CI.md",
    "docs/DATABASE.md",
    "docs/BACKUP_RESTORE.md",
    "docs/PRODUCTION.md",
    "docs/DEPLOYMENT_CHECKLIST.md",
    "docs/LOAD_TESTING.md",
    "docs/MONITORING.md",
    "docs/LEGACY.md",
    "serve.py",
    "load_test.py",
    "smoke_test.py",
    "migrate_db.py",
    "maintenance.py",
    "worker.py",
    "wsgi.py",
    "run_production.bat",
    "run_production.sh",
    "run_worker.bat",
    "run_worker.sh",
    "run_maintenance.bat",
    "run_maintenance.sh",
)

DEFAULT_ROOTS = ("organizations", "deploy", ".github", "src/migrations", *SUPPORT_FILES)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "backups",
    "cache",
    "runtime_cache",
    "evaluation_results",
}
EXCLUDED_FILE_PATTERNS = (
    "*.pyc",
    "*.pyo",
    "*.log",
)


def is_excluded(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_FILE_PATTERNS)


def database_files(root: Path) -> list[Path]:
    if database_backend() != "sqlite":
        return []
    db_path = Path(resolve_project_path(SETTINGS.db_path))
    try:
        db_path.relative_to(root)
    except ValueError:
        return []
    candidates = [db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")]
    return [path for path in candidates if path.exists() and path.is_file()]


def database_backend() -> str:
    backend = (SETTINGS.db_backend or "sqlite").strip().lower()
    return "postgres" if backend == "postgresql" else backend


def postgres_dump_command(output_path: Path, pg_dump_path: str = "pg_dump") -> list[str]:
    if not SETTINGS.database_url:
        raise RuntimeError("VOX_DATABASE_URL is required to create a PostgreSQL backup.")
    return [
        pg_dump_path,
        "--dbname",
        SETTINGS.database_url,
        "--file",
        str(output_path),
        "--format",
        "plain",
        "--no-owner",
        "--no-privileges",
    ]


def create_postgres_dump(output_path: Path, pg_dump_path: str = "pg_dump") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(postgres_dump_command(output_path, pg_dump_path=pg_dump_path), check=True)


def collect_backup_files(
    root: Path = ROOT,
    include_secrets: bool = False,
    include_database: bool = True,
) -> list[Path]:
    root = root.resolve()
    candidates = [root / name for name in DEFAULT_ROOTS]
    if include_secrets:
        candidates.append(root / ".env")

    files: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.is_file():
            if not is_excluded(candidate, root):
                files.append(candidate)
            continue

        for path in candidate.rglob("*"):
            if path.is_file() and not is_excluded(path, root):
                files.append(path)

    if include_database:
        files.extend(database_files(root))
    return sorted(set(files), key=lambda item: item.relative_to(root).as_posix())


def default_archive_path(output: Path | None = None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = output or DEFAULT_OUTPUT_DIR
    if base.suffix.lower() == ".zip":
        return base
    return base / f"vox_backup_{timestamp}.zip"


def create_backup(
    root: Path = ROOT,
    output: Path | None = None,
    include_secrets: bool = False,
    include_database: bool = True,
    pg_dump_path: str = "pg_dump",
    dry_run: bool = False,
) -> dict:
    root = root.resolve()
    archive_path = default_archive_path(output).resolve()
    files = collect_backup_files(root, include_secrets=include_secrets, include_database=include_database)
    total_bytes = sum(path.stat().st_size for path in files)
    backend = database_backend()
    database_artifact = None

    if include_database and backend == "postgres":
        database_artifact = POSTGRES_DUMP_ARCHIVE_NAME
        if dry_run:
            total_bytes = None

    if not dry_run:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if include_database and backend == "postgres":
            with tempfile.TemporaryDirectory() as tmp_dir:
                dump_path = Path(tmp_dir) / "postgres.sql"
                create_postgres_dump(dump_path, pg_dump_path=pg_dump_path)
                total_bytes += dump_path.stat().st_size
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in files:
                        archive.write(path, path.relative_to(root).as_posix())
                    archive.write(dump_path, POSTGRES_DUMP_ARCHIVE_NAME)
        else:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, path.relative_to(root).as_posix())

    return {
        "archive_path": archive_path,
        "file_count": len(files) + (1 if database_artifact else 0),
        "total_bytes": total_bytes,
        "dry_run": dry_run,
        "included_secrets": include_secrets,
        "included_database": include_database,
        "database_backend": backend,
        "database_artifact": database_artifact,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a VOX backup archive.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip file or output directory. Defaults to backups/vox_backup_<timestamp>.zip.",
    )
    parser.add_argument(
        "--include-secrets",
        action="store_true",
        help="Include .env in the archive. Store this backup securely.",
    )
    parser.add_argument(
        "--skip-database",
        action="store_true",
        help="Do not include the SQLite files or PostgreSQL dump in the archive.",
    )
    parser.add_argument(
        "--pg-dump-path",
        default="pg_dump",
        help="Path to pg_dump when VOX_DB_BACKEND=postgres. Defaults to pg_dump on PATH.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be backed up without creating an archive.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = create_backup(
        output=args.output,
        include_secrets=args.include_secrets,
        include_database=not args.skip_database,
        pg_dump_path=args.pg_dump_path,
        dry_run=args.dry_run,
    )

    action = "Backup plan" if result["dry_run"] else "Backup created"
    print(f"{action}: {result['archive_path']}")
    print(f"Files: {result['file_count']}")
    size = "unknown until pg_dump runs" if result["total_bytes"] is None else f"{result['total_bytes']} bytes"
    print(f"Size: {size}")
    print(f"Database: {result['database_backend']} {'included' if result['included_database'] else 'skipped'}")
    if result["database_artifact"]:
        print(f"Database artifact: {result['database_artifact']}")
    if not result["included_secrets"]:
        print("Secrets: .env excluded. Use --include-secrets only for secure offline backups.")


if __name__ == "__main__":
    main()
