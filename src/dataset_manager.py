import json
import os
import re
import hashlib
import shlex
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.config import SETTINGS

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".csv", ".tsv", ".xlsx", ".xls"}
MAX_UPLOAD_BYTES = SETTINGS.max_upload_mb * 1024 * 1024
MANIFEST_NAME = "dataset_manifest.json"
SNIFF_BYTES = 4096
FORMULA_PREFIXES = ("=", "+", "-", "@")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def org_root(profile: Dict[str, Any]) -> Path:
    source_dir = Path(profile["source_data_dir"])
    if source_dir.name == "documents":
        return source_dir.parent
    return Path("organizations") / profile.get("org_id", "default")


def manifest_path(profile: Dict[str, Any]) -> Path:
    root = org_root(profile)
    root.mkdir(parents=True, exist_ok=True)
    return root / MANIFEST_NAME


def load_manifest(profile: Dict[str, Any]) -> Dict[str, Any]:
    path = manifest_path(profile)
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "org_id": profile.get("org_id", "default"),
        "organization_name": profile.get("organization_name", ""),
        "documents": [],
        "last_processed_at": None,
        "processing_status": "not_started",
        "chunks": [],
        "vector_index": {
            "status": "not_started",
            "embedding_model": profile.get("embedding_model", ""),
            "chroma_dir": profile.get("chroma_dir", ""),
            "indexed_chunk_count": 0,
        },
        "stats": {"document_count": 0, "chunk_count": 0, "total_characters": 0},
    }


def save_manifest(profile: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    path = manifest_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["org_id"] = profile.get("org_id", "default")
    manifest["organization_name"] = profile.get("organization_name", "")
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def safe_upload_name(filename: str) -> str:
    base = Path(filename or "upload").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(base).stem).strip("._-") or "document"
    suffix = Path(base).suffix.lower()
    return f"{stem}{suffix}"


def decode_text_sample(sample: bytes) -> str:
    try:
        return sample.decode("utf-8")
    except UnicodeDecodeError:
        return sample.decode("utf-16")


def validate_text_upload(filename: str, sample: bytes) -> None:
    try:
        text = decode_text_sample(sample)
    except UnicodeDecodeError as exc:
        raise ValueError("Text-like uploads must be UTF-8 or UTF-16 encoded.") from exc

    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".tsv"}:
        return
    for line_number, line in enumerate(text.splitlines(), start=1):
        cells = line.split("\t" if suffix == ".tsv" else ",")
        for cell in cells:
            stripped = cell.strip().strip('"').strip("'")
            if stripped.startswith(FORMULA_PREFIXES):
                raise ValueError(f"Spreadsheet formula-like cell rejected at line {line_number}.")


def validate_pdf_upload(stream) -> None:
    stream.seek(0)
    try:
        from pypdf import PdfReader

        reader = PdfReader(stream, strict=True)
        page_count = len(reader.pages)
        if page_count > SETTINGS.max_upload_pdf_pages:
            raise ValueError(
                f"PDF has too many pages. Maximum allowed: {SETTINGS.max_upload_pdf_pages}."
            )
        if page_count:
            # Touch the first page enough to reject many malformed PDFs before saving.
            reader.pages[0].extract_text() or ""
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF could not be parsed safely.") from exc
    finally:
        stream.seek(0)


def validate_xlsx_upload(stream) -> None:
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            total_compressed = sum(max(info.compress_size, 0) for info in infos)
            total_uncompressed = sum(max(info.file_size, 0) for info in infos)
            if total_compressed and total_uncompressed / total_compressed > SETTINGS.max_upload_zip_ratio:
                raise ValueError("XLSX compression ratio is too high.")
            if any(info.file_size > MAX_UPLOAD_BYTES for info in infos):
                raise ValueError("XLSX contains an internal file that is too large.")
    except ValueError:
        raise
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid XLSX archive.") from exc
    finally:
        stream.seek(0)


def validate_spreadsheet_cells(stream, suffix: str) -> None:
    stream.seek(0)
    try:
        import pandas as pd

        if suffix in {".xlsx", ".xls"}:
            sheets = pd.read_excel(stream, sheet_name=None, nrows=SETTINGS.max_upload_spreadsheet_cells + 1)
            cell_count = sum(int(df.shape[0] * df.shape[1]) for df in sheets.values())
        else:
            sep = "\t" if suffix == ".tsv" else ","
            df = pd.read_csv(stream, sep=sep, nrows=SETTINGS.max_upload_spreadsheet_cells + 1)
            cell_count = int(df.shape[0] * df.shape[1])
        if cell_count > SETTINGS.max_upload_spreadsheet_cells:
            raise ValueError(
                f"Spreadsheet has too many cells. Maximum allowed: {SETTINGS.max_upload_spreadsheet_cells}."
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Spreadsheet could not be parsed safely.") from exc
    finally:
        stream.seek(0)


def run_upload_scan(path: Path) -> None:
    command = SETTINGS.upload_scan_command.strip()
    if not command:
        return
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise ValueError("Upload scan command is invalid.") from exc
    if not args:
        return
    result = subprocess.run(
        [*args, str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stdout or result.stderr or "scanner rejected file").strip()
        raise ValueError(f"Upload scan failed: {detail[:300]}")


def validate_upload(filename: str, size_bytes: int, sample: bytes, stream=None) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Allowed: {allowed}")
    if size_bytes <= 0:
        raise ValueError("File is empty.")
    if size_bytes > MAX_UPLOAD_BYTES:
        raise ValueError(f"File is too large. Maximum upload size is {SETTINGS.max_upload_mb} MB.")

    if suffix == ".pdf" and not sample.startswith(b"%PDF"):
        raise ValueError("Invalid PDF file signature.")
    if suffix == ".xlsx" and not sample.startswith(b"PK\x03\x04"):
        raise ValueError("Invalid XLSX file signature.")
    if suffix == ".xls" and not sample.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("Invalid XLS file signature.")
    if suffix in {".txt", ".csv", ".tsv"}:
        validate_text_upload(filename, sample)

    if stream is None:
        return
    if suffix == ".pdf":
        validate_pdf_upload(stream)
    elif suffix == ".xlsx":
        validate_xlsx_upload(stream)
        validate_spreadsheet_cells(stream, suffix)
    elif suffix in {".xls", ".csv", ".tsv"}:
        validate_spreadsheet_cells(stream, suffix)


def stream_sha256(stream) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def existing_hashes(manifest: Dict[str, Any]) -> set[str]:
    return {
        str(doc.get("sha256"))
        for doc in manifest.get("documents", [])
        if doc.get("sha256")
    }


def save_uploaded_file(profile: Dict[str, Any], file_storage) -> Dict[str, Any]:
    filename = safe_upload_name(file_storage.filename)
    file_storage.stream.seek(0, os.SEEK_END)
    size_bytes = file_storage.stream.tell()
    file_storage.stream.seek(0)
    sample = file_storage.stream.read(SNIFF_BYTES)
    file_storage.stream.seek(0)
    validate_upload(filename, size_bytes, sample, file_storage.stream)
    sha256 = stream_sha256(file_storage.stream)

    manifest = load_manifest(profile)
    if sha256 in existing_hashes(manifest):
        raise ValueError("This file has already been uploaded for this organization.")

    document_id = uuid.uuid4().hex
    target_dir = Path(profile["source_data_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{document_id}_{filename}"
    file_storage.save(target_path)
    try:
        run_upload_scan(target_path)
    except Exception:
        try:
            target_path.unlink()
        finally:
            raise

    record = {
        "document_id": document_id,
        "original_filename": filename,
        "stored_filename": target_path.name,
        "path": str(target_path),
        "extension": target_path.suffix.lower(),
        "size_bytes": size_bytes,
        "sha256": sha256,
        "uploaded_at": now_iso(),
        "status": "uploaded",
        "character_count": 0,
        "chunk_count": 0,
        "error": None,
    }

    manifest["documents"].append(record)
    manifest["processing_status"] = "uploaded"
    manifest["stats"]["document_count"] = len(manifest["documents"])
    save_manifest(profile, manifest)
    return record


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return normalize_text("\n\n".join(pages))
    if suffix in {".txt"}:
        return normalize_text(path.read_text(encoding="utf-8", errors="ignore"))
    if suffix in {".csv", ".tsv"}:
        import pandas as pd

        sep = "\t" if suffix == ".tsv" else ","
        df = pd.read_csv(path, sep=sep)
        return normalize_text(df.to_csv(index=False))
    if suffix in {".xlsx", ".xls"}:
        import pandas as pd

        sheets = pd.read_excel(path, sheet_name=None)
        parts = []
        for name, df in sheets.items():
            parts.append(f"Sheet: {name}\n{df.to_csv(index=False)}")
        return normalize_text("\n\n".join(parts))
    raise ValueError(f"Unsupported file type: {suffix}")


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 120) -> List[str]:
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start:start + chunk_size].strip())
                start += max(1, chunk_size - overlap)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def process_dataset(profile: Dict[str, Any]) -> Dict[str, Any]:
    manifest = load_manifest(profile)
    manifest["processing_status"] = "processing"
    save_manifest(profile, manifest)

    chunks = []
    total_chars = 0

    for doc in manifest.get("documents", []):
        path = Path(doc["path"])
        try:
            text = extract_text(path)
            doc_chunks = chunk_text(text)
            total_chars += len(text)
            doc["status"] = "processed"
            doc["character_count"] = len(text)
            doc["chunk_count"] = len(doc_chunks)
            doc["error"] = None
            for idx, chunk in enumerate(doc_chunks):
                chunks.append({
                    "chunk_id": f"{doc['document_id']}:{idx}",
                    "document_id": doc["document_id"],
                    "source": doc["original_filename"],
                    "chunk_index": idx,
                    "text": chunk,
                    "character_count": len(chunk),
                })
        except Exception as exc:
            doc["status"] = "error"
            doc["error"] = str(exc)

    manifest["chunks"] = chunks
    manifest["last_processed_at"] = now_iso()
    manifest["processing_status"] = "processed"
    manifest["stats"] = {
        "document_count": len(manifest.get("documents", [])),
        "chunk_count": len(chunks),
        "total_characters": total_chars,
    }
    save_manifest(profile, manifest)
    return manifest
