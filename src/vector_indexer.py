import shutil
from pathlib import Path
from typing import Any, Dict


def chunks_to_documents(manifest: Dict[str, Any]) -> list[Any]:
    from langchain_core.documents import Document

    documents = []
    for chunk in manifest.get("chunks", []):
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        documents.append(
            Document(
                page_content=text,
                metadata={
                    "org_id": manifest.get("org_id", "default"),
                    "document_id": chunk.get("document_id"),
                    "chunk_id": chunk.get("chunk_id"),
                    "chunk_index": chunk.get("chunk_index"),
                    "source": chunk.get("source"),
                },
            )
        )
    return documents


def rebuild_vector_index(profile: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings

    documents = chunks_to_documents(manifest)
    chroma_dir = Path(profile["chroma_dir"])
    embedding_model = profile.get("embedding_model", "nomic-embed-text")

    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)
    chroma_dir.mkdir(parents=True, exist_ok=True)

    if not documents:
        return {
            "status": "empty",
            "embedding_model": embedding_model,
            "chroma_dir": str(chroma_dir),
            "indexed_chunk_count": 0,
        }

    Chroma.from_documents(
        documents=documents,
        embedding=OllamaEmbeddings(model=embedding_model),
        persist_directory=str(chroma_dir),
    )

    return {
        "status": "indexed",
        "embedding_model": embedding_model,
        "chroma_dir": str(chroma_dir),
        "indexed_chunk_count": len(documents),
    }
