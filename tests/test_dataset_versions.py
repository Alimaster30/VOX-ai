import json
from pathlib import Path

from src.dataset_manager import load_manifest, save_manifest
from src.dataset_versions import create_dataset_version, list_dataset_versions, rollback_dataset_version


def build_profile(tmp_path):
    org_root = tmp_path / "organizations" / "demo"
    return {
        "org_id": "demo",
        "organization_name": "Demo Org",
        "source_data_dir": str(org_root / "documents"),
        "chroma_dir": str(org_root / "vector_index"),
        "intents_path": str(org_root / "intents.json"),
        "cache_dir": str(org_root / "cache"),
        "embedding_model": "nomic-embed-text",
    }


def test_dataset_version_snapshot_and_rollback(tmp_path):
    profile = build_profile(tmp_path)
    Path(profile["source_data_dir"]).mkdir(parents=True)
    vector_dir = Path(profile["chroma_dir"])
    vector_dir.mkdir(parents=True)
    (vector_dir / "index.txt").write_text("version one", encoding="utf-8")

    manifest = {
        "org_id": "demo",
        "organization_name": "Demo Org",
        "documents": [{"document_id": "doc1", "status": "processed"}],
        "chunks": [{"chunk_id": "doc1:0", "text": "hello"}],
        "processing_status": "processed",
        "stats": {"document_count": 1, "chunk_count": 1, "total_characters": 5},
        "vector_index": {"status": "indexed", "indexed_chunk_count": 1},
    }
    save_manifest(profile, manifest)

    version = create_dataset_version(profile, manifest)
    assert version["has_vector_index"] is True
    assert list_dataset_versions(profile)[0]["version_id"] == version["version_id"]

    changed_manifest = dict(manifest)
    changed_manifest["stats"] = {"document_count": 9, "chunk_count": 9, "total_characters": 9}
    save_manifest(profile, changed_manifest)
    (vector_dir / "index.txt").write_text("changed", encoding="utf-8")

    result = rollback_dataset_version(profile, version["version_id"])

    assert result["status"] == "rolled_back"
    assert load_manifest(profile)["stats"]["chunk_count"] == 1
    assert (vector_dir / "index.txt").read_text(encoding="utf-8") == "version one"


def test_dataset_version_rejects_unsafe_id(tmp_path):
    profile = build_profile(tmp_path)
    Path(profile["source_data_dir"]).mkdir(parents=True)

    try:
        rollback_dataset_version(profile, "../bad")
    except ValueError as exc:
        assert "Invalid dataset version id" in str(exc)
    else:
        raise AssertionError("Unsafe version id was accepted")
