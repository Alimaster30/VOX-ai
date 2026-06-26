import argparse
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from src.config import SETTINGS


ROOT = Path(__file__).resolve().parent
POSTGRES_DUMP_ARCHIVE_NAME = "database/postgres.sql"

SUPPORT_FILES = {
    ".env.example",
    "requirements.txt",
    "README.md",
    "docs/PRODUCTION.md",
    "docs/DEPLOYMENT_CHECKLIST.md",
    "docs/LEGACY.md",
    "serve.py",
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
    "requirements-ci.txt",
    "docs/CI.md",
    "docs/DATABASE.md",
    "docs/BACKUP_RESTORE.md",
    "docs/LOAD_TESTING.md",
    "docs/MONITORING.md",
}


def safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def is_restorable_member(
    name: str,
    include_secrets: bool = False,
    include_support_files: bool = False,
) -> bool:
    path = safe_archive_path(name)
    first = path.parts[0]
    if first == "organizations":
        return True
    if include_support_files and first == "deploy":
        return True
    if include_support_files and path.parts[:2] == ("src", "migrations"):
        return True
    if first == "runtime_cache" and path.name.startswith("vox.sqlite3"):
        return True
    if name == POSTGRES_DUMP_ARCHIVE_NAME:
        return False
    if include_secrets and name == ".env":
        return True
    if include_support_files and name in SUPPORT_FILES:
        return True
    return False


def postgres_restore_command(input_path: Path, psql_path: str = "psql") -> list[str]:
    if not SETTINGS.database_url:
        raise RuntimeError("VOX_DATABASE_URL is required to restore a PostgreSQL backup.")
    return [
        psql_path,
        "--dbname",
        SETTINGS.database_url,
        "--file",
        str(input_path),
    ]


def restore_postgres_dump_from_archive(
    archive: zipfile.ZipFile,
    dry_run: bool = False,
    psql_path: str = "psql",
) -> bool:
    if POSTGRES_DUMP_ARCHIVE_NAME not in archive.namelist():
        return False
    if dry_run:
        return True
    with tempfile.TemporaryDirectory() as tmp_dir:
        dump_path = Path(tmp_dir) / "postgres.sql"
        with archive.open(POSTGRES_DUMP_ARCHIVE_NAME, "r") as source, dump_path.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        subprocess.run(postgres_restore_command(dump_path, psql_path=psql_path), check=True)
    return True


def restore_backup(
    archive_path: Path,
    root: Path = ROOT,
    overwrite: bool = False,
    dry_run: bool = False,
    include_secrets: bool = False,
    include_support_files: bool = False,
    restore_postgres_database: bool = False,
    psql_path: str = "psql",
) -> dict:
    root = root.resolve()
    archive_path = archive_path.resolve()
    if not archive_path.exists():
        raise FileNotFoundError(f"Backup archive not found: {archive_path}")

    restored = 0
    skipped = 0
    unsafe = 0
    database_restored = False

    with zipfile.ZipFile(archive_path, "r") as archive:
        if restore_postgres_database:
            database_restored = restore_postgres_dump_from_archive(archive, dry_run=dry_run, psql_path=psql_path)

        for member in archive.infolist():
            if member.is_dir():
                continue
            try:
                rel_path = safe_archive_path(member.filename)
                allowed = is_restorable_member(
                    member.filename,
                    include_secrets=include_secrets,
                    include_support_files=include_support_files,
                )
            except ValueError:
                unsafe += 1
                continue

            if not allowed:
                skipped += 1
                continue

            target = root.joinpath(*rel_path.parts).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                unsafe += 1
                continue

            if target.exists() and not overwrite:
                skipped += 1
                continue

            restored += 1
            if dry_run:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)

    return {
        "archive_path": archive_path,
        "restored": restored,
        "skipped": skipped,
        "unsafe": unsafe,
        "dry_run": dry_run,
        "database_restored": database_restored,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore organization data from a VOX backup.")
    parser.add_argument("archive", type=Path, help="Path to a VOX backup zip archive.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing files. Without this, existing files are skipped.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be restored without writing files.",
    )
    parser.add_argument(
        "--include-secrets",
        action="store_true",
        help="Restore .env if it exists in the archive.",
    )
    parser.add_argument(
        "--include-support-files",
        action="store_true",
        help="Restore support files such as requirements and production notes.",
    )
    parser.add_argument(
        "--restore-postgres-database",
        action="store_true",
        help="Restore database/postgres.sql into VOX_DATABASE_URL using psql. Use carefully.",
    )
    parser.add_argument(
        "--psql-path",
        default="psql",
        help="Path to psql when restoring a PostgreSQL dump. Defaults to psql on PATH.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = restore_backup(
        args.archive,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        include_secrets=args.include_secrets,
        include_support_files=args.include_support_files,
        restore_postgres_database=args.restore_postgres_database,
        psql_path=args.psql_path,
    )

    action = "Restore plan" if result["dry_run"] else "Restore complete"
    print(f"{action}: {result['archive_path']}")
    print(f"Restored: {result['restored']}")
    print(f"Skipped: {result['skipped']}")
    print(f"Unsafe entries ignored: {result['unsafe']}")
    print(f"PostgreSQL database restored: {result['database_restored']}")


if __name__ == "__main__":
    main()
