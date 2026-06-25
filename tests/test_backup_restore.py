import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import backup_vox
import restore_vox
from backup_vox import collect_backup_files, create_backup
from restore_vox import restore_backup, safe_archive_path


def test_backup_excludes_env_unless_requested(tmp_path):
    (tmp_path / ".env").write_text("VOX_ADMIN_TOKEN=secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("VOX_ADMIN_TOKEN=change-this", encoding="utf-8")
    org_dir = tmp_path / "organizations" / "demo"
    org_dir.mkdir(parents=True)
    (org_dir / "profile.json").write_text("{}", encoding="utf-8")

    without_secrets = {path.name for path in collect_backup_files(tmp_path, include_secrets=False)}
    with_secrets = {path.name for path in collect_backup_files(tmp_path, include_secrets=True)}

    assert ".env" not in without_secrets
    assert ".env" in with_secrets


def test_backup_and_restore_organization_data(tmp_path):
    source = tmp_path / "source"
    restore_target = tmp_path / "restore"
    org_dir = source / "organizations" / "demo"
    org_dir.mkdir(parents=True)
    (org_dir / "profile.json").write_text('{"org_id": "demo"}', encoding="utf-8")
    (org_dir / "documents").mkdir()
    (org_dir / "documents" / "faq.txt").write_text("hello", encoding="utf-8")

    archive = tmp_path / "backup.zip"
    result = create_backup(source, output=archive)
    restore_result = restore_backup(result["archive_path"], root=restore_target)

    assert restore_result["restored"] == 2
    assert (restore_target / "organizations" / "demo" / "profile.json").exists()
    assert (restore_target / "organizations" / "demo" / "documents" / "faq.txt").read_text(encoding="utf-8") == "hello"


def test_restore_rejects_unsafe_archive_paths(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../outside.txt", "bad")
        zf.writestr("organizations/demo/profile.json", "{}")

    result = restore_backup(archive, root=tmp_path / "restore")

    assert result["unsafe"] == 1
    assert result["restored"] == 1
    assert not (tmp_path / "outside.txt").exists()


def test_safe_archive_path_rejects_parent_reference():
    with pytest.raises(ValueError):
        safe_archive_path("organizations/../bad.json")


def test_postgres_backup_adds_database_dump(monkeypatch, tmp_path):
    source = tmp_path / "source"
    org_dir = source / "organizations" / "demo"
    org_dir.mkdir(parents=True)
    (org_dir / "profile.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "backup.zip"

    monkeypatch.setattr(
        backup_vox,
        "SETTINGS",
        SimpleNamespace(db_backend="postgres", db_path="./runtime_cache/vox.sqlite3", database_url="postgresql://demo"),
    )

    def fake_dump(output_path: Path, pg_dump_path: str = "pg_dump") -> None:
        output_path.write_text("-- vox dump", encoding="utf-8")

    monkeypatch.setattr(backup_vox, "create_postgres_dump", fake_dump)

    result = backup_vox.create_backup(source, output=archive)

    assert result["database_backend"] == "postgres"
    assert result["database_artifact"] == backup_vox.POSTGRES_DUMP_ARCHIVE_NAME
    with zipfile.ZipFile(archive, "r") as zf:
        assert backup_vox.POSTGRES_DUMP_ARCHIVE_NAME in zf.namelist()
        assert zf.read(backup_vox.POSTGRES_DUMP_ARCHIVE_NAME).decode("utf-8") == "-- vox dump"


def test_postgres_restore_requires_explicit_database_flag(tmp_path):
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(backup_vox.POSTGRES_DUMP_ARCHIVE_NAME, "-- vox dump")

    normal = restore_vox.restore_backup(archive, root=tmp_path / "restore")
    dry_run = restore_vox.restore_backup(
        archive,
        root=tmp_path / "restore",
        restore_postgres_database=True,
        dry_run=True,
    )

    assert normal["database_restored"] is False
    assert dry_run["database_restored"] is True


def test_postgres_backup_command_requires_database_url(monkeypatch, tmp_path):
    monkeypatch.setattr(
        backup_vox,
        "SETTINGS",
        SimpleNamespace(db_backend="postgres", db_path="./runtime_cache/vox.sqlite3", database_url=""),
    )

    with pytest.raises(RuntimeError):
        backup_vox.postgres_dump_command(tmp_path / "postgres.sql")
