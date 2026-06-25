from typing import Any, Callable, Dict

from src.config import SETTINGS, ensure_org_runtime_files, load_org_profile
from src.dataset_manager import process_dataset, save_manifest
from src.dataset_versions import create_dataset_version
from src.ollama_health import check_ollama_health
from src.vector_indexer import rebuild_vector_index


ProgressCallback = Callable[[int, str], None]


def process_dataset_for_profile(
    profile: Dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> Dict[str, Any]:
    ensure_org_runtime_files(profile)
    embed_model = SETTINGS.embedding_model or profile.get("embedding_model", "nomic-embed-text")
    llm_model = SETTINGS.llm_model or profile.get("llm_model", "qwen3.2:3b")

    health = check_ollama_health(llm_model, embed_model)
    if not health.get("reachable"):
        raise RuntimeError(f"Ollama is not reachable: {health.get('error')}")
    if embed_model in health.get("missing_models", []):
        raise RuntimeError(f"Embedding model is missing in Ollama: {embed_model}")

    if progress_callback:
        progress_callback(15, "Reading and chunking documents")
    manifest = process_dataset(profile)

    chunk_count = manifest.get("stats", {}).get("chunk_count", 0)
    if progress_callback:
        progress_callback(45, f"Building embeddings for {chunk_count} chunks")
    index_result = rebuild_vector_index(profile, manifest)

    if progress_callback:
        progress_callback(80, "Saving dataset manifest")
    manifest["vector_index"] = index_result
    version = create_dataset_version(profile, manifest)
    manifest["current_dataset_version"] = version["version_id"]
    save_manifest(profile, manifest)

    return {
        "processing_status": manifest.get("processing_status"),
        "stats": manifest.get("stats", {}),
        "vector_index": index_result,
        "dataset_version": version,
    }


def process_dataset_for_org(
    org_id: str,
    progress_callback: ProgressCallback | None = None,
) -> Dict[str, Any]:
    profile = load_org_profile(org_id)
    return process_dataset_for_profile(profile, progress_callback=progress_callback)
