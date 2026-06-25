import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from src.dataset_manager import load_manifest, manifest_path, now_iso, org_root, save_manifest


VERSIONS_DIR_NAME = "versions"


def safe_version_id(value: str) -> str:
    version_id = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value.strip())
    if not version_id or version_id in {".", ".."} or ".." in version_id:
        raise ValueError("Invalid dataset version id")
    return version_id


def versions_dir(profile: Dict[str, Any]) -> Path:
    root = org_root(profile).resolve()
    path = root / VERSIONS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def version_dir(profile: Dict[str, Any], version_id: str) -> Path:
    clean_id = safe_version_id(version_id)
    base = versions_dir(profile).resolve()
    path = (base / clean_id).resolve()
    path.relative_to(base)
    return path


def next_version_id() -> str:
    stamp = now_iso().replace(":", "").replace("+", "Z")
    return f"dataset_{stamp}_{uuid.uuid4().hex[:8]}"


def copy_directory(source: Path, target: Path) -> bool:
    if not source.exists() or not source.is_dir():
        return False
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return True


def create_dataset_version(profile: Dict[str, Any], manifest: Dict[str, Any] | None = None) -> Dict[str, Any]:
    manifest = dict(manifest or load_manifest(profile))
    version_id = next_version_id()
    target = version_dir(profile, version_id)
    target.mkdir(parents=True, exist_ok=False)

    chroma_dir = Path(profile["chroma_dir"]).resolve()
    vector_snapshot = target / "vector_index"
    has_vector_index = copy_directory(chroma_dir, vector_snapshot)

    manifest_snapshot = json.loads(json.dumps(manifest, ensure_ascii=False))
    manifest_snapshot["dataset_version_id"] = version_id
    manifest_snapshot["version_created_at"] = now_iso()

    manifest_file = target / "dataset_manifest.json"
    manifest_file.write_text(json.dumps(manifest_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    metadata = {
        "version_id": version_id,
        "org_id": profile.get("org_id", "default"),
        "created_at": manifest_snapshot["version_created_at"],
        "manifest_path": str(manifest_file),
        "vector_index_path": str(vector_snapshot) if has_vector_index else None,
        "has_vector_index": has_vector_index,
        "stats": manifest_snapshot.get("stats", {}),
        "processing_status": manifest_snapshot.get("processing_status"),
        "vector_index": manifest_snapshot.get("vector_index", {}),
    }
    (target / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def list_dataset_versions(profile: Dict[str, Any]) -> list[Dict[str, Any]]:
    base = versions_dir(profile)
    versions = []
    for metadata_path in sorted(base.glob("*/metadata.json"), reverse=True):
        try:
            versions.append(json.loads(metadata_path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return versions


def rollback_dataset_version(profile: Dict[str, Any], version_id: str) -> Dict[str, Any]:
    source = version_dir(profile, version_id)
    if not source.exists():
        raise FileNotFoundError(f"Dataset version not found: {version_id}")

    version_manifest_path = source / "dataset_manifest.json"
    if not version_manifest_path.exists():
        raise FileNotFoundError(f"Dataset version manifest missing: {version_id}")

    manifest = json.loads(version_manifest_path.read_text(encoding="utf-8"))
    manifest["rolled_back_from_version"] = version_id
    manifest["rolled_back_at"] = now_iso()
    manifest["current_dataset_version"] = version_id
    save_manifest(profile, manifest)

    source_vector = source / "vector_index"
    target_vector = Path(profile["chroma_dir"]).resolve()
    if source_vector.exists():
        if target_vector.exists():
            shutil.rmtree(target_vector)
        shutil.copytree(source_vector, target_vector)

    return {
        "status": "rolled_back",
        "version_id": version_id,
        "manifest": load_manifest(profile),
        "restored_vector_index": source_vector.exists(),
        "manifest_path": str(manifest_path(profile)),
        "vector_index_path": str(target_vector),
    }
