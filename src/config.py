import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ORGANIZATIONS_DIR = PROJECT_ROOT / "organizations"
ENV_PATH = PROJECT_ROOT / ".env"


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class AppSettings:
    org_id: str
    production: bool
    flask_secret_key: str
    admin_token: str
    host: str
    port: int
    debug: bool
    autoload_models: bool
    max_upload_mb: int
    max_upload_pdf_pages: int
    max_upload_spreadsheet_cells: int
    max_upload_zip_ratio: int
    upload_scan_command: str
    answer_cache_enabled: bool
    answer_cache_ttl_seconds: int
    answer_cache_min_confidence: float
    max_history: int
    sample_rate: int
    max_concurrent_stt: int
    max_concurrent_queries: int
    session_ttl_seconds: int
    waitress_threads: int
    rate_limit_window_seconds: int
    rate_limit_voice: int
    rate_limit_upload: int
    rate_limit_admin: int
    db_backend: str
    db_path: str
    database_url: str
    job_mode: str
    log_dir: str
    log_level: str
    log_max_bytes: int
    log_backup_count: int
    maintenance_audit_retention_days: int
    maintenance_job_retention_days: int
    maintenance_handoff_retention_days: int
    tts_engine: str
    tts_fallback_engine: str
    tts_kokoro_voice_en: str
    tts_kokoro_voice_ur: str
    llm_model: str | None
    embedding_model: str | None
    intents_path: str | None
    threshold: float


def get_settings() -> AppSettings:
    production = env_bool("VOX_PRODUCTION", False)
    secret_key = os.environ.get("VOX_SECRET_KEY", "")
    admin_token = os.environ.get("VOX_ADMIN_TOKEN", "")
    if production and (not secret_key or secret_key.startswith("change-this")):
        raise RuntimeError("VOX_SECRET_KEY must be set to a real secret when VOX_PRODUCTION=1")
    if production and (not admin_token or admin_token.startswith("change-this")):
        raise RuntimeError("VOX_ADMIN_TOKEN must be set to a real token when VOX_PRODUCTION=1")

    def env_float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, str(default)))
        except ValueError:
            return default

    try:
        threshold = float(os.environ.get("VOX_CONFIDENCE_THRESHOLD", "0.5"))
    except ValueError:
        threshold = 0.5

    return AppSettings(
        org_id=os.environ.get("VOX_ORG_ID", "default"),
        production=production,
        flask_secret_key=secret_key or os.urandom(24).hex(),
        admin_token=admin_token,
        host=os.environ.get("VOX_HOST", "0.0.0.0"),
        port=env_int("VOX_PORT", 5000),
        debug=env_bool("VOX_DEBUG", False),
        autoload_models=env_bool("VOX_AUTOLOAD_MODELS", True),
        max_upload_mb=env_int("VOX_MAX_UPLOAD_MB", 50),
        max_upload_pdf_pages=env_int("VOX_MAX_UPLOAD_PDF_PAGES", 500),
        max_upload_spreadsheet_cells=env_int("VOX_MAX_UPLOAD_SPREADSHEET_CELLS", 250000),
        max_upload_zip_ratio=env_int("VOX_MAX_UPLOAD_ZIP_RATIO", 20),
        upload_scan_command=os.environ.get("VOX_UPLOAD_SCAN_COMMAND", ""),
        answer_cache_enabled=env_bool("VOX_ANSWER_CACHE_ENABLED", True),
        answer_cache_ttl_seconds=env_int("VOX_ANSWER_CACHE_TTL_SECONDS", 86400),
        answer_cache_min_confidence=env_float("VOX_ANSWER_CACHE_MIN_CONFIDENCE", 0.65),
        max_history=env_int("VOX_MAX_HISTORY", 6),
        sample_rate=env_int("VOX_SAMPLE_RATE", 16000),
        max_concurrent_stt=env_int("VOX_MAX_CONCURRENT_STT", 2),
        max_concurrent_queries=env_int("VOX_MAX_CONCURRENT_QUERIES", 4),
        session_ttl_seconds=env_int("VOX_SESSION_TTL_SECONDS", 3600),
        waitress_threads=env_int("VOX_WAITRESS_THREADS", 8),
        rate_limit_window_seconds=env_int("VOX_RATE_LIMIT_WINDOW_SECONDS", 60),
        rate_limit_voice=env_int("VOX_RATE_LIMIT_VOICE", 30),
        rate_limit_upload=env_int("VOX_RATE_LIMIT_UPLOAD", 10),
        rate_limit_admin=env_int("VOX_RATE_LIMIT_ADMIN", 120),
        db_backend=os.environ.get("VOX_DB_BACKEND", "sqlite").strip().lower(),
        db_path=os.environ.get("VOX_DB_PATH", "./runtime_cache/vox.sqlite3"),
        database_url=os.environ.get("VOX_DATABASE_URL", "").strip(),
        job_mode=os.environ.get("VOX_JOB_MODE", "inline").strip().lower(),
        log_dir=os.environ.get("VOX_LOG_DIR", "./logs"),
        log_level=os.environ.get("VOX_LOG_LEVEL", "INFO"),
        log_max_bytes=env_int("VOX_LOG_MAX_BYTES", 5 * 1024 * 1024),
        log_backup_count=env_int("VOX_LOG_BACKUP_COUNT", 5),
        maintenance_audit_retention_days=env_int("VOX_MAINTENANCE_AUDIT_RETENTION_DAYS", 30),
        maintenance_job_retention_days=env_int("VOX_MAINTENANCE_JOB_RETENTION_DAYS", 14),
        maintenance_handoff_retention_days=env_int("VOX_MAINTENANCE_HANDOFF_RETENTION_DAYS", 90),
        tts_engine=os.environ.get("VOX_TTS_ENGINE", "kokoro").strip().lower(),
        tts_fallback_engine=os.environ.get("VOX_TTS_FALLBACK_ENGINE", "none").strip().lower(),
        tts_kokoro_voice_en=os.environ.get("VOX_TTS_KOKORO_VOICE_EN", "af_heart").strip(),
        tts_kokoro_voice_ur=os.environ.get("VOX_TTS_KOKORO_VOICE_UR", "").strip(),
        llm_model=os.environ.get("VOX_LLM_MODEL"),
        embedding_model=os.environ.get("VOX_EMBED_MODEL"),
        intents_path=os.environ.get("VOX_INTENTS_PATH"),
        threshold=threshold,
    )


SETTINGS = get_settings()
DEFAULT_ORG_ID = SETTINGS.org_id


def resolve_project_path(path_value: str) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def load_org_profile(org_id: str | None = None) -> Dict[str, Any]:
    selected_org_id = org_id or DEFAULT_ORG_ID
    profile_path = ORGANIZATIONS_DIR / selected_org_id / "profile.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Organization profile not found: {profile_path}")

    with profile_path.open("r", encoding="utf-8") as f:
        profile = json.load(f)

    profile["org_id"] = profile.get("org_id", selected_org_id)
    profile["chroma_dir"] = resolve_project_path(profile.get("chroma_dir", f"./organizations/{selected_org_id}/vector_index"))
    profile["intents_path"] = resolve_project_path(profile.get("intents_path", f"./organizations/{selected_org_id}/intents.json"))
    profile["source_data_dir"] = resolve_project_path(profile.get("source_data_dir", f"./organizations/{selected_org_id}/documents"))
    profile["cache_dir"] = resolve_project_path(profile.get("cache_dir", f"./organizations/{selected_org_id}/cache"))
    return profile


def sanitize_org_id(value: str) -> str:
    org_id = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    if not org_id:
        raise ValueError("Organization id is required")
    if len(org_id) > 48:
        raise ValueError("Organization id must be 48 characters or less")
    return org_id


def build_default_profile(
    org_id: str,
    organization_name: str,
    domain: str = "general",
    assistant_name: str = "VOX",
) -> Dict[str, Any]:
    return {
        "org_id": org_id,
        "organization_name": organization_name,
        "assistant_name": assistant_name,
        "domain": domain,
        "supported_languages": ["en", "ur"],
        "llm_model": SETTINGS.llm_model or "qwen3.2:3b",
        "embedding_model": SETTINGS.embedding_model or "nomic-embed-text",
        "legacy_intent_handler": False,
        "chroma_dir": f"./organizations/{org_id}/vector_index",
        "intents_path": f"./organizations/{org_id}/intents.json",
        "source_data_dir": f"./organizations/{org_id}/documents",
        "cache_dir": f"./organizations/{org_id}/cache",
        "greetings": {
            "en": f"Welcome to {organization_name}. How can I help you today?",
            "ur": f"Welcome to {organization_name}. How can I help you today?",
        },
        "fallback": {
            "en": "Sorry, I could not answer that from the available organization data. Please contact support.",
            "ur": "Sorry, I could not answer that from the available organization data. Please contact support.",
        },
        "handoff": {
            "department": "Support",
            "phone": "",
            "email": "",
            "hours": "",
        },
        "rag_system_prompt": (
            "You are VOX, a helpful assistant for {organization_name}. "
            "Use only the provided organization context to answer accurately. "
            "If the context is insufficient, say you do not have enough confirmed information and suggest contacting support."
        ),
    }


def list_org_profiles() -> list[Dict[str, Any]]:
    organizations = []
    if not ORGANIZATIONS_DIR.exists():
        return organizations
    for profile_path in sorted(ORGANIZATIONS_DIR.glob("*/profile.json")):
        try:
            profile = load_org_profile(profile_path.parent.name)
            organizations.append({
                "org_id": profile.get("org_id"),
                "organization_name": profile.get("organization_name"),
                "assistant_name": profile.get("assistant_name", "VOX"),
                "domain": profile.get("domain", "general"),
                "active": profile.get("org_id") == SETTINGS.org_id,
            })
        except Exception:
            continue
    return organizations


def create_org_profile(
    org_id: str,
    organization_name: str,
    domain: str = "general",
    assistant_name: str = "VOX",
) -> Dict[str, Any]:
    clean_org_id = sanitize_org_id(org_id)
    if not organization_name.strip():
        raise ValueError("Organization name is required")

    org_dir = ORGANIZATIONS_DIR / clean_org_id
    profile_path = org_dir / "profile.json"
    if profile_path.exists():
        raise FileExistsError(f"Organization already exists: {clean_org_id}")

    profile = build_default_profile(clean_org_id, organization_name.strip(), domain.strip() or "general", assistant_name.strip() or "VOX")
    org_dir.mkdir(parents=True, exist_ok=False)
    (org_dir / "documents").mkdir(parents=True, exist_ok=True)
    (org_dir / "vector_index").mkdir(parents=True, exist_ok=True)
    (org_dir / "cache").mkdir(parents=True, exist_ok=True)
    (org_dir / "intents.json").write_text('{\n  "intents": []\n}\n', encoding="utf-8")
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_org_profile(clean_org_id)


def ensure_org_runtime_files(profile: Dict[str, Any]) -> None:
    Path(profile["source_data_dir"]).mkdir(parents=True, exist_ok=True)
    Path(profile["chroma_dir"]).mkdir(parents=True, exist_ok=True)
    Path(profile["cache_dir"]).mkdir(parents=True, exist_ok=True)

    intents_path = Path(profile["intents_path"])
    if not intents_path.exists():
        fallback_intents = PROJECT_ROOT / "data" / "responses" / "enhanced_intents.json"
        intents_path.parent.mkdir(parents=True, exist_ok=True)
        if profile.get("org_id") == "default" and fallback_intents.exists():
            shutil.copyfile(fallback_intents, intents_path)
        else:
            intents_path.write_text('{"intents": []}', encoding="utf-8")


def get_handoff_text(profile: Dict[str, Any]) -> str:
    handoff = profile.get("handoff", {})
    parts = []
    if handoff.get("department"):
        parts.append(handoff["department"])
    if handoff.get("phone"):
        parts.append(f"Phone: {handoff['phone']}")
    if handoff.get("email"):
        parts.append(f"Email: {handoff['email']}")
    if handoff.get("hours"):
        parts.append(f"Hours: {handoff['hours']}")
    return ", ".join(parts) if parts else "organization support"
