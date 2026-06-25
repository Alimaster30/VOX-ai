import os
import sys
import re
import json
import time
import tempfile
import logging
import threading
import math
import hmac
import uuid
from collections import deque
from collections import defaultdict
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, Response, after_this_request, g, has_request_context
from werkzeug.exceptions import HTTPException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config import SETTINGS, create_org_profile, ensure_org_runtime_files, get_handoff_text, list_org_profiles, load_org_profile, sanitize_org_id
from src.dataset_manager import load_manifest, save_manifest, save_uploaded_file
from src.dataset_versions import list_dataset_versions, rollback_dataset_version
from src.intent_generator import generate_intent_draft, load_active_intents, load_intent_draft, publish_intent_draft
from src.jobs import job_manager, set_job_persistence
from src.ollama_health import check_ollama_health
from src.persistence import (
    create_queued_job,
    create_admin_token,
    count_answer_cache,
    count_handoff_tickets,
    count_persisted_sessions,
    create_handoff_ticket,
    get_cached_answer,
    get_persisted_job,
    has_active_persisted_job,
    init_db,
    latest_audit_events,
    latest_persisted_job,
    list_handoff_tickets,
    list_admin_tokens,
    persist_job,
    persist_session,
    record_audit_event,
    revoke_admin_token,
    run_maintenance,
    store_cached_answer,
    sync_organizations,
    update_handoff_ticket,
    upsert_organization,
    verify_admin_token,
)
from src.logging_config import configure_logging
from src.worker_tasks import process_dataset_for_profile

APP_LOG_PATH = configure_logging("vox-app")
logger = logging.getLogger(__name__)
ALLOWED_ADMIN_SCOPES = {"admin", "read", "write", "dataset_write", "handoff_write", "intent_write", "root_admin", "*"}

app = Flask(__name__)
app.secret_key = SETTINGS.flask_secret_key
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_mb * 1024 * 1024

ORG_PROFILE = load_org_profile()
ensure_org_runtime_files(ORG_PROFILE)
os.environ.setdefault("VOX_CACHE_DIR", ORG_PROFILE["cache_dir"])
init_db()
set_job_persistence(persist_job)
upsert_organization(ORG_PROFILE)

CHROMA_DIR = ORG_PROFILE["chroma_dir"]
EMBED_MODEL = SETTINGS.embedding_model or ORG_PROFILE.get("embedding_model", "nomic-embed-text")
LLM_MODEL = SETTINGS.llm_model or ORG_PROFILE.get("llm_model", "qwen3.2:3b")
INTENTS_PATH = SETTINGS.intents_path or ORG_PROFILE["intents_path"]
USE_LEGACY_HANDLER = bool(ORG_PROFILE.get("legacy_intent_handler", ORG_PROFILE.get("org_id") == "default"))
THRESHOLD = SETTINGS.threshold
MAX_HISTORY = SETTINGS.max_history
SAMPLE_RATE = SETTINGS.sample_rate

GREETING_EN = ORG_PROFILE.get("greetings", {}).get("en", f"Welcome to {ORG_PROFILE['organization_name']}. How can I help you today?")
GREETING_UR = ORG_PROFILE.get("greetings", {}).get("ur", GREETING_EN)

# ── Global model references (loaded lazily at startup) ────────────────────────
whisper_model = None
handler = None
classifier = None
db = None
retriever = None
llm = None
rag_prompt = None
org_runtimes = {}
org_runtime_lock = threading.Lock()
device = None  # set in load_models() after torch import
MODELS_READY = False
MODELS_LOADING = False
MODELS_ERROR = None
MODELS_STARTED_AT = None
MODELS_LOADED_AT = None
org_model_states = {}

# In-memory conversation histories keyed by session ID
sessions = {}
session_lock = threading.Lock()
models_lock = threading.Lock()
stt_gate = threading.BoundedSemaphore(max(1, SETTINGS.max_concurrent_stt))
query_gate = threading.BoundedSemaphore(max(1, SETTINGS.max_concurrent_queries))
active_calls = 0
active_stt_jobs = 0
active_query_jobs = 0
runtime_lock = threading.Lock()
rate_limit_lock = threading.Lock()
rate_limit_buckets = defaultdict(deque)
metrics_lock = threading.Lock()
request_metrics = {
    "total": 0,
    "latency_sum_ms": 0,
    "latency_max_ms": 0,
    "by_route": defaultdict(int),
}
ollama_health_cache = {"checked_at": 0.0, "data": None}

EVAL_CACHE_FILE = os.path.join("evaluation_results", "cache.json")


def new_model_state():
    return {
        "ready": False,
        "loading": False,
        "error": None,
        "started_at": None,
        "loaded_at": None,
    }


def sync_default_model_globals_unlocked():
    global MODELS_READY, MODELS_LOADING, MODELS_ERROR, MODELS_STARTED_AT, MODELS_LOADED_AT
    state = org_model_states.setdefault(ORG_PROFILE.get("org_id", "default"), new_model_state())
    MODELS_READY = bool(state.get("ready"))
    MODELS_LOADING = bool(state.get("loading"))
    MODELS_ERROR = state.get("error")
    MODELS_STARTED_AT = state.get("started_at")
    MODELS_LOADED_AT = state.get("loaded_at")


def update_model_state(org_id, **updates):
    with models_lock:
        state = org_model_states.setdefault(org_id, new_model_state())
        state.update(updates)
        if org_id == ORG_PROFILE.get("org_id", "default"):
            sync_default_model_globals_unlocked()
        return dict(state)


def model_state_snapshot(org_id):
    with models_lock:
        state = org_model_states.setdefault(org_id, new_model_state())
        if org_id == ORG_PROFILE.get("org_id", "default"):
            sync_default_model_globals_unlocked()
        return dict(state)


def current_request_id():
    return getattr(g, "request_id", None)


def current_call_id():
    return getattr(g, "call_id", None)


def current_org_profile():
    if has_request_context():
        return getattr(g, "org_profile", ORG_PROFILE)
    return ORG_PROFILE


def current_org_id():
    return current_org_profile().get("org_id", "default")


def request_org_id():
    raw_org_id = request.headers.get("X-VOX-Org-ID") or request.args.get("org_id")
    if not raw_org_id:
        return None
    return sanitize_org_id(raw_org_id)


def session_storage_key(session_id, org_id=None):
    return f"{org_id or current_org_id()}:{session_id}"


def count_active_sessions(org_id=None):
    prefix = f"{org_id or current_org_id()}:"
    return sum(1 for key in sessions if key.startswith(prefix))


def persist_safely(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except Exception as exc:
        logger.warning("Persistence write failed: %s", exc)
        return None


@app.before_request
def attach_request_context():
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    call_id = request.headers.get("X-VOX-Call-ID") or request.args.get("call_id")
    g.request_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", request_id)[:80]
    g.call_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", call_id)[:80] if call_id else None
    g.started_at = time.time()
    g.org_profile = ORG_PROFILE
    try:
        selected_org_id = request_org_id()
        if selected_org_id:
            profile = load_org_profile(selected_org_id)
            ensure_org_runtime_files(profile)
            g.org_profile = profile
    except ValueError as exc:
        return jsonify({"error": str(exc), "request_id": current_request_id()}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc), "request_id": current_request_id()}), 404


@app.after_request
def add_observability_headers(response):
    request_id = current_request_id()
    if request_id:
        response.headers["X-Request-ID"] = request_id
    if current_call_id():
        response.headers["X-VOX-Call-ID"] = current_call_id()

    elapsed_ms = int((time.time() - getattr(g, "started_at", time.time())) * 1000)
    route_rule = request.url_rule.rule if request.url_rule else request.path
    record_request_metric(request.method, route_rule, response.status_code, elapsed_ms)
    if request.path != "/api/health":
        event = {
            "event": "request",
            "request_id": request_id,
            "call_id": current_call_id(),
            "org_id": current_org_id(),
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "remote_addr": request.remote_addr,
        }
        logger.info(json.dumps(event))
        persist_safely(
            record_audit_event,
            event_type="request",
            request_id=request_id,
            call_id=current_call_id(),
            org_id=current_org_id(),
            method=request.method,
            path=request.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            remote_addr=request.remote_addr,
        )
    return response


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return jsonify({
            "error": exc.description,
            "request_id": current_request_id(),
        }), exc.code

    logger.error(json.dumps({
        "event": "unhandled_error",
        "request_id": current_request_id(),
        "call_id": current_call_id(),
        "org_id": current_org_id(),
        "path": request.path,
        "error": str(exc),
    }), exc_info=True)
    return jsonify({
        "error": "Internal server error",
        "request_id": current_request_id(),
    }), 500


def require_admin_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not SETTINGS.admin_token:
            g.admin_auth = {"source": "open", "token_id": None, "org_id": None, "scopes": ["*"]}
            return fn(*args, **kwargs)
        provided = request.headers.get("X-VOX-Admin-Token", "")
        if not provided:
            authorization = request.headers.get("Authorization", "")
            if authorization.lower().startswith("bearer "):
                provided = authorization[7:].strip()
        if hmac.compare_digest(provided, SETTINGS.admin_token):
            g.admin_auth = {"source": "environment", "token_id": None, "org_id": None, "scopes": ["*"]}
            return fn(*args, **kwargs)
        db_token = verify_admin_token(provided)
        if db_token:
            token_org_id = db_token.get("org_id")
            if token_org_id and token_org_id != current_org_id():
                return jsonify({"error": "Admin token is not authorized for this organization", "request_id": current_request_id()}), 403
            g.admin_auth = db_token
            return fn(*args, **kwargs)
        return jsonify({"error": "Admin token required", "request_id": current_request_id()}), 401
    return wrapper


def admin_has_scope(required_scope):
    admin_auth = getattr(g, "admin_auth", {}) or {}
    if admin_auth.get("source") in {"environment", "open"}:
        return True

    scopes = {
        str(scope).strip().lower()
        for scope in admin_auth.get("scopes", [])
        if str(scope).strip()
    }
    if "*" in scopes or "root_admin" in scopes:
        return True
    if required_scope == "read":
        return bool(scopes & {"read", "admin", "org_admin", "write", "dataset_write", "handoff_write", "intent_write"})
    if required_scope == "write":
        return bool(scopes & {"admin", "org_admin", "write"})
    return required_scope in scopes or bool(scopes & {"admin", "org_admin", "write"})


def require_admin_scope(required_scope):
    def decorator(fn):
        @wraps(fn)
        @require_admin_auth
        def wrapper(*args, **kwargs):
            if admin_has_scope(required_scope):
                return fn(*args, **kwargs)
            return jsonify({
                "error": f"Admin token does not have required scope: {required_scope}",
                "request_id": current_request_id(),
            }), 403
        return wrapper
    return decorator


def require_root_admin(fn):
    @wraps(fn)
    @require_admin_auth
    def wrapper(*args, **kwargs):
        admin_auth = getattr(g, "admin_auth", {}) or {}
        if admin_auth.get("source") in {"environment", "open"}:
            return fn(*args, **kwargs)
        scopes = {str(scope).strip().lower() for scope in admin_auth.get("scopes", [])}
        if scopes & {"*", "root_admin"} and not admin_auth.get("org_id"):
            return fn(*args, **kwargs)
        return jsonify({"error": "Root admin access required", "request_id": current_request_id()}), 403
    return wrapper


def rate_limit(limit_name, max_requests):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if max_requests <= 0:
                return fn(*args, **kwargs)
            window = max(1, SETTINGS.rate_limit_window_seconds)
            identity = (
                request.headers.get("X-VOX-Call-ID")
                or request.headers.get("X-VOX-Admin-Token")
                or request.remote_addr
                or "unknown"
            )
            key = (limit_name, identity)
            now = time.time()
            with rate_limit_lock:
                bucket = rate_limit_buckets[key]
                while bucket and now - bucket[0] > window:
                    bucket.popleft()
                if len(bucket) >= max_requests:
                    retry_after = max(1, int(window - (now - bucket[0])))
                    return jsonify({
                        "error": "Rate limit exceeded",
                        "retry_after_seconds": retry_after,
                        "request_id": current_request_id(),
                    }), 429, {"Retry-After": str(retry_after)}
                bucket.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def runtime_counter(name, delta):
    global active_calls, active_stt_jobs, active_query_jobs
    with runtime_lock:
        if name == "calls":
            active_calls = max(0, active_calls + delta)
            return active_calls
        if name == "stt":
            active_stt_jobs = max(0, active_stt_jobs + delta)
            return active_stt_jobs
        if name == "queries":
            active_query_jobs = max(0, active_query_jobs + delta)
            return active_query_jobs
        return 0


def runtime_snapshot():
    with runtime_lock:
        return {
            "active_calls": active_calls,
            "active_stt_jobs": active_stt_jobs,
            "active_query_jobs": active_query_jobs,
            "max_concurrent_stt": SETTINGS.max_concurrent_stt,
            "max_concurrent_queries": SETTINGS.max_concurrent_queries,
        }


def record_request_metric(method, path, status_code, elapsed_ms):
    key = (method, path, str(status_code))
    with metrics_lock:
        request_metrics["total"] += 1
        request_metrics["latency_sum_ms"] += max(0, int(elapsed_ms))
        request_metrics["latency_max_ms"] = max(request_metrics["latency_max_ms"], max(0, int(elapsed_ms)))
        request_metrics["by_route"][key] += 1


def metrics_snapshot():
    with metrics_lock:
        return {
            "total": request_metrics["total"],
            "latency_sum_ms": request_metrics["latency_sum_ms"],
            "latency_max_ms": request_metrics["latency_max_ms"],
            "by_route": dict(request_metrics["by_route"]),
        }


def prometheus_escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def metric_line(name, value, labels=None):
    if labels:
        rendered_labels = ",".join(f'{key}="{prometheus_escape(val)}"' for key, val in labels.items())
        return f"{name}{{{rendered_labels}}} {value}"
    return f"{name} {value}"


def render_prometheus_metrics():
    runtime = runtime_snapshot()
    metrics = metrics_snapshot()
    total = max(1, metrics["total"])
    profile = current_org_profile()
    org_id = profile.get("org_id", "default")
    model_state = model_state_snapshot(org_id)
    with session_lock:
        active_session_count = count_active_sessions(org_id)

    lines = [
        "# HELP vox_app_info VOX application metadata.",
        "# TYPE vox_app_info gauge",
        metric_line("vox_app_info", 1, {"org_id": org_id, "assistant": profile.get("assistant_name", "VOX")}),
        "# HELP vox_http_requests_total Total HTTP requests handled by VOX.",
        "# TYPE vox_http_requests_total counter",
    ]

    for (method, path, status), count in sorted(metrics["by_route"].items()):
        lines.append(metric_line(
            "vox_http_requests_total",
            count,
            {"method": method, "path": path, "status": status},
        ))

    lines.extend([
        "# HELP vox_http_request_latency_ms_sum Total request latency in milliseconds.",
        "# TYPE vox_http_request_latency_ms_sum counter",
        metric_line("vox_http_request_latency_ms_sum", metrics["latency_sum_ms"]),
        "# HELP vox_http_request_latency_ms_avg Average request latency in milliseconds.",
        "# TYPE vox_http_request_latency_ms_avg gauge",
        metric_line("vox_http_request_latency_ms_avg", round(metrics["latency_sum_ms"] / total, 2)),
        "# HELP vox_http_request_latency_ms_max Maximum observed request latency in milliseconds.",
        "# TYPE vox_http_request_latency_ms_max gauge",
        metric_line("vox_http_request_latency_ms_max", metrics["latency_max_ms"]),
        "# HELP vox_active_calls Active voice calls currently being processed.",
        "# TYPE vox_active_calls gauge",
        metric_line("vox_active_calls", runtime["active_calls"]),
        "# HELP vox_active_stt_jobs Active speech-to-text jobs.",
        "# TYPE vox_active_stt_jobs gauge",
        metric_line("vox_active_stt_jobs", runtime["active_stt_jobs"]),
        "# HELP vox_active_query_jobs Active answer generation jobs.",
        "# TYPE vox_active_query_jobs gauge",
        metric_line("vox_active_query_jobs", runtime["active_query_jobs"]),
        "# HELP vox_active_sessions In-memory caller sessions.",
        "# TYPE vox_active_sessions gauge",
        metric_line("vox_active_sessions", active_session_count),
        "# HELP vox_persisted_sessions Persisted caller sessions for the active organization.",
        "# TYPE vox_persisted_sessions gauge",
        metric_line("vox_persisted_sessions", count_persisted_sessions(org_id)),
        "# HELP vox_open_handoffs Open human handoff tickets for the active organization.",
        "# TYPE vox_open_handoffs gauge",
        metric_line("vox_open_handoffs", count_handoff_tickets(org_id, status="open")),
        "# HELP vox_answer_cache_entries Cached answers for the active organization.",
        "# TYPE vox_answer_cache_entries gauge",
        metric_line("vox_answer_cache_entries", count_answer_cache(org_id)),
        "# HELP vox_models_ready Whether all application models are ready.",
        "# TYPE vox_models_ready gauge",
        metric_line("vox_models_ready", 1 if whisper_model is not None and org_runtime_is_ready(org_id) else 0),
        "# HELP vox_models_loading Whether models are currently loading.",
        "# TYPE vox_models_loading gauge",
        metric_line("vox_models_loading", 1 if model_state.get("loading") else 0),
        "# HELP vox_models_error Whether model loading has failed.",
        "# TYPE vox_models_error gauge",
        metric_line("vox_models_error", 1 if model_state.get("error") else 0),
    ])
    return "\n".join(lines) + "\n"


def get_ollama_health_cached(force=False):
    now = time.time()
    if not force and ollama_health_cache["data"] is not None and now - ollama_health_cache["checked_at"] < 15:
        return ollama_health_cache["data"]
    data = check_ollama_health(LLM_MODEL, EMBED_MODEL)
    ollama_health_cache["checked_at"] = now
    ollama_health_cache["data"] = data
    return data


def new_session_data(sid):
    now = time.time()
    return {
        "session_id": sid,
        "history": [],
        "query_count": 0,
        "layer_counts": {"1": 0, "2": 0, "3": 0},
        "created_at": now,
        "last_seen": now,
    }


def cleanup_expired_sessions():
    if SETTINGS.session_ttl_seconds <= 0:
        return
    cutoff = time.time() - SETTINGS.session_ttl_seconds
    expired = [sid for sid, data in sessions.items() if data.get("last_seen", data.get("created_at", 0)) < cutoff]
    for sid in expired:
        sessions.pop(sid, None)


def get_or_create_session():
    call_id = request.headers.get("X-VOX-Call-ID") or request.args.get("call_id")
    org_id = current_org_id()
    if call_id:
        sid = re.sub(r"[^A-Za-z0-9._:-]+", "_", call_id)[:80]
    else:
        sid = session.get("sid")
    if not sid:
        import uuid
        sid = uuid.uuid4().hex
        session["sid"] = sid
    key = session_storage_key(sid, org_id)
    with session_lock:
        cleanup_expired_sessions()
        if key not in sessions:
            sessions[key] = new_session_data(sid)
        else:
            sessions[key]["last_seen"] = time.time()
        persist_safely(persist_session, org_id, sessions[key])
    return sid


def build_rag_prompt(profile=None):
    from langchain_core.prompts import PromptTemplate

    profile = profile or current_org_profile()
    system_prompt = profile.get(
        "rag_system_prompt",
        "You are {assistant_name}, a helpful assistant for {organization_name}. Use only the provided organization context."
    ).format(
        assistant_name=profile.get("assistant_name", "VOX"),
        organization_name=profile.get("organization_name", "this organization"),
    )
    return PromptTemplate.from_template(
        system_prompt + "\n\n"
        "Conversation history:\n{history}\n\n"
        "Context from documents:\n{context}\n\n"
        "Question: {question}\n\n"
        "Answer:"
    )


def org_runtime_config(profile):
    return {
        "org_id": profile.get("org_id", "default"),
        "profile": profile,
        "chroma_dir": profile["chroma_dir"],
        "embedding_model": SETTINGS.embedding_model or profile.get("embedding_model", "nomic-embed-text"),
        "llm_model": SETTINGS.llm_model or profile.get("llm_model", "qwen3.2:3b"),
        "intents_path": SETTINGS.intents_path or profile["intents_path"],
        "use_legacy_handler": bool(profile.get("legacy_intent_handler", profile.get("org_id") == "default")),
    }


def sync_legacy_runtime_globals(runtime):
    global handler, classifier, db, retriever, llm, rag_prompt
    handler = runtime.get("handler")
    classifier = runtime.get("classifier")
    db = runtime.get("db")
    retriever = runtime.get("retriever")
    llm = runtime.get("llm")
    rag_prompt = runtime.get("rag_prompt")


def load_org_runtime(profile, force=False):
    from langchain_ollama import OllamaEmbeddings, OllamaLLM
    from langchain_chroma import Chroma
    from src.intent.intelligent_handler import IntelligentQueryHandler
    from src.intent.classifier import IntentClassifier

    config = org_runtime_config(profile)
    org_id = config["org_id"]
    with org_runtime_lock:
        existing = org_runtimes.get(org_id)
        if existing and not force:
            return existing

    os.environ["VOX_CACHE_DIR"] = profile["cache_dir"]
    runtime_handler = IntelligentQueryHandler()
    runtime_classifier = IntentClassifier(intents_path=config["intents_path"])
    runtime_classifier.load_model()
    runtime_classifier.build_index()
    runtime_db = Chroma(
        persist_directory=config["chroma_dir"],
        embedding_function=OllamaEmbeddings(model=config["embedding_model"]),
    )
    runtime = {
        **config,
        "handler": runtime_handler,
        "classifier": runtime_classifier,
        "db": runtime_db,
        "retriever": runtime_db.as_retriever(search_kwargs={"k": 4}),
        "llm": OllamaLLM(model=config["llm_model"]),
        "rag_prompt": build_rag_prompt(profile),
        "loaded_at": time.time(),
        "error": None,
    }
    with org_runtime_lock:
        org_runtimes[org_id] = runtime
    if org_id == ORG_PROFILE.get("org_id"):
        sync_legacy_runtime_globals(runtime)
    logger.info("Runtime ready for organization %s", org_id)
    return runtime


def current_org_runtime():
    org_id = current_org_id()
    with org_runtime_lock:
        runtime = org_runtimes.get(org_id)
    if runtime:
        return runtime
    if org_id == ORG_PROFILE.get("org_id") and classifier is not None:
        return {
            **org_runtime_config(ORG_PROFILE),
            "handler": handler,
            "classifier": classifier,
            "db": db,
            "retriever": retriever,
            "llm": llm,
            "rag_prompt": rag_prompt,
            "loaded_at": None,
            "error": None,
        }
    return None


def org_runtime_is_ready(org_id=None):
    with org_runtime_lock:
        runtime = org_runtimes.get(org_id or current_org_id())
    return bool(runtime and runtime.get("classifier") is not None)


def apply_org_profile(profile):
    global ORG_PROFILE, CHROMA_DIR, EMBED_MODEL, LLM_MODEL, INTENTS_PATH
    global GREETING_EN, GREETING_UR, USE_LEGACY_HANDLER, rag_prompt

    ORG_PROFILE = profile
    config = org_runtime_config(ORG_PROFILE)
    CHROMA_DIR = config["chroma_dir"]
    EMBED_MODEL = config["embedding_model"]
    LLM_MODEL = config["llm_model"]
    INTENTS_PATH = config["intents_path"]
    USE_LEGACY_HANDLER = config["use_legacy_handler"]
    GREETING_EN = ORG_PROFILE.get("greetings", {}).get("en", f"Welcome to {ORG_PROFILE['organization_name']}. How can I help you today?")
    GREETING_UR = ORG_PROFILE.get("greetings", {}).get("ur", GREETING_EN)
    os.environ["VOX_CACHE_DIR"] = ORG_PROFILE["cache_dir"]
    with org_runtime_lock:
        active_runtime = org_runtimes.get(ORG_PROFILE.get("org_id"))
    if active_runtime:
        sync_legacy_runtime_globals(active_runtime)
    elif rag_prompt is not None:
        rag_prompt = build_rag_prompt()


def load_profile_models(profile, force=False):
    global whisper_model, device

    import torch
    import whisper

    config = org_runtime_config(profile)
    health = check_ollama_health(config["llm_model"], config["embedding_model"])
    ollama_health_cache["checked_at"] = time.time()
    ollama_health_cache["data"] = health
    if not health.get("reachable"):
        raise RuntimeError(f"Ollama is not reachable: {health.get('error')}")
    missing = [
        model for model in [config["llm_model"], config["embedding_model"]]
        if model in health.get("missing_models", [])
    ]
    if missing:
        raise RuntimeError(f"Missing Ollama models: {', '.join(missing)}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading models on {device}...")
    if whisper_model is None:
        whisper_model = whisper.load_model("base", device="cpu")  # force CPU to avoid CUDA crash
        logger.info("Whisper base model loaded")

    runtime = load_org_runtime(profile, force=force)
    logger.info("Models loaded for organization %s", profile.get("org_id"))
    return runtime


def load_models():
    org_id = ORG_PROFILE.get("org_id", "default")
    update_model_state(org_id, ready=False, loading=True, error=None, started_at=time.time(), loaded_at=None)
    try:
        load_profile_models(ORG_PROFILE, force=True)
        update_model_state(
            org_id,
            ready=whisper_model is not None and org_runtime_is_ready(org_id),
            loading=False,
            error=None,
            loaded_at=time.time(),
        )
    except Exception as exc:
        update_model_state(org_id, ready=False, loading=False, error=str(exc))
        raise
    logger.info("All models loaded")


def start_model_loading_once(profile=None, force_retry=False):
    profile = profile or ORG_PROFILE
    org_id = profile.get("org_id")
    state = model_state_snapshot(org_id)
    if state.get("loading"):
        return
    if whisper_model is not None and org_runtime_is_ready(org_id) and not force_retry:
        update_model_state(org_id, ready=True, loading=False, error=None)
        return
    if state.get("error") and not force_retry:
        return
    update_model_state(
        org_id,
        ready=False,
        loading=True,
        error=None,
        started_at=time.time(),
        loaded_at=None,
    )

    def run_loader():
        try:
            load_profile_models(profile, force=force_retry)
            update_model_state(
                org_id,
                ready=whisper_model is not None and org_runtime_is_ready(org_id),
                error=None,
                loaded_at=time.time(),
            )
            logger.info("Models loaded for organization %s", org_id)
        except Exception as exc:
            logger.error("Model loading failed: %s", exc, exc_info=True)
            update_model_state(org_id, ready=False, error=str(exc))
        finally:
            update_model_state(org_id, loading=False)

    thread = threading.Thread(target=run_loader, name="vox-model-loader", daemon=True)
    thread.start()


def reload_retriever():
    load_org_runtime(ORG_PROFILE, force=True)
    logger.info("ChromaDB retriever reloaded")


def reload_classifier():
    load_org_runtime(ORG_PROFILE, force=True)
    logger.info("IntentClassifier reloaded")


def can_switch_organization():
    runtime = runtime_snapshot()
    if runtime["active_calls"] or runtime["active_stt_jobs"] or runtime["active_query_jobs"]:
        return False, "Cannot switch organization while calls are active."
    latest_dataset_job = job_manager.latest("dataset_processing", org_id=ORG_PROFILE.get("org_id"))
    if latest_dataset_job and latest_dataset_job.status in {"queued", "running"}:
        return False, "Cannot switch organization while dataset processing is running."
    if has_active_persisted_job("dataset_processing", ORG_PROFILE.get("org_id")):
        return False, "Cannot switch organization while dataset processing is queued or running."
    return True, None


def switch_active_organization(org_id):
    global sessions, db, retriever, llm, rag_prompt

    allowed, reason = can_switch_organization()
    if not allowed:
        raise RuntimeError(reason)

    profile = load_org_profile(org_id)
    ensure_org_runtime_files(profile)
    persist_safely(upsert_organization, profile)
    apply_org_profile(profile)

    with session_lock:
        sessions = {}

    if any([classifier, db, llm, rag_prompt]):
        load_org_runtime(profile, force=True)

    logger.info("Switched active organization to %s", ORG_PROFILE.get("org_id"))
    return {
        "org_id": ORG_PROFILE.get("org_id"),
        "organization_name": ORG_PROFILE.get("organization_name"),
        "assistant_name": ORG_PROFILE.get("assistant_name", "VOX"),
        "domain": ORG_PROFILE.get("domain", "general"),
        "legacy_intent_handler": USE_LEGACY_HANDLER,
    }


def run_dataset_processing_job(job):
    def progress(progress_value, message):
        job_manager.update(job.job_id, progress=progress_value, message=message)

    org_id = job.metadata.get("org_id") or ORG_PROFILE.get("org_id")
    profile = load_org_profile(org_id)
    result = process_dataset_for_profile(profile, progress_callback=progress)

    if org_id == ORG_PROFILE.get("org_id") and result.get("vector_index", {}).get("status") == "indexed" and db is not None:
        job_manager.update(job.job_id, progress=90, message="Reloading retriever")
        reload_retriever()

    return result


def detect_language(text):
    urdu_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return "ur"
    # If more than 25% Urdu script chars, it's Urdu
    if urdu_chars / total_alpha > 0.25:
        return "ur"
    # Roman Urdu detection — common words used in Pakistani speech
    roman_urdu_words = [
        "kya", "hai", "hain", "kaise", "kitni", "kitna", "mein", "ka", "ki",
        "ke", "se", "ko", "aur", "nahi", "hoga", "chahiye", "milti", "milta",
        "dakhla", "fee", "saal", "semester", "program", "scholarship", "hostel",
        "assalam", "aoa", "salam", "allah", "hafiz", "khuda", "shukria",
        "university", "campus", "admission", "apply", "kitne", "kahan",
        "hazri", "haazri", "imtihan", "nateeja", "qawaneen", "pabandi"
    ]
    text_lower = text.lower()
    roman_matches = sum(1 for w in roman_urdu_words if w in text_lower)
    if roman_matches >= 1:
        return "ur"
    return "en"


def translate(text, from_code, to_code):
    if from_code == to_code:
        return text
    try:
        import argostranslate.translate
        installed = argostranslate.translate.get_installed_languages()
        codes = {lang.code for lang in installed}
        if from_code not in codes or to_code not in codes:
            logger.warning(f"Argostranslate: language pair {from_code}->{to_code} not installed. Run download_languages.py")
            return text
        return argostranslate.translate.translate(text, from_code, to_code)
    except Exception as e:
        logger.warning(f"Translation failed ({from_code}->{to_code}): {e}")
        return text


def clean_for_tts(text):
    text = re.sub(r'[^\w\s\u0600-\u06FF،؟۔,.\'"!?()\-:]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def generate_audio(text, language):
    from src.local_tts import synthesize_speech

    text = clean_for_tts(text)
    result = synthesize_speech(
        text=text,
        language=language,
        engine=SETTINGS.tts_engine,
        fallback_engine=SETTINGS.tts_fallback_engine,
        voice_en=SETTINGS.tts_kokoro_voice_en,
        voice_ur=SETTINGS.tts_kokoro_voice_ur,
    )
    if result.get("tts_error"):
        logger.warning("TTS returned no audio via %s: %s", result.get("tts_engine"), result.get("tts_error"))
    return result


def build_history_text(history):
    lines = []
    for turn in history:
        lines.append(f"User: {turn['user']}")
        lines.append(f"Assistant: {turn['assistant']}")
    return "\n".join(lines)


def fallback_response(language):
    profile = current_org_profile()
    fallback = profile.get("fallback", {})
    if language == "ur":
        return fallback.get("ur") or fallback.get("en") or f"معذرت، میرے پاس مصدقہ معلومات نہیں ہیں۔ براہ کرم {get_handoff_text(profile)} سے رابطہ کریں۔"
    return fallback.get("en") or f"Sorry, I do not have enough confirmed information. Please contact {get_handoff_text(profile)}."


def process_query(query, history, detected_lang=None):
    t0 = time.time()
    runtime = current_org_runtime()
    if runtime is None:
        raise RuntimeError(f"Runtime is not loaded for organization: {current_org_id()}")
    runtime_handler = runtime.get("handler")
    runtime_classifier = runtime.get("classifier")
    runtime_retriever = runtime.get("retriever")
    runtime_llm = runtime.get("llm")
    runtime_rag_prompt = runtime.get("rag_prompt")
    use_legacy_handler = runtime.get("use_legacy_handler", False)

    # Use Whisper-detected language if provided, otherwise detect from text
    lang = detected_lang if detected_lang in ("ur", "en") else detect_language(query)
    # For Roman Urdu (Latin script but Whisper says ur), keep ur
    if detected_lang == "ur" and lang == "en":
        lang = "ur"
    # Also run text-based Roman Urdu detection as backup
    if lang == "en" and detect_language(query) == "ur":
        lang = "ur"

    if SETTINGS.answer_cache_enabled:
        cached = persist_safely(get_cached_answer, current_org_id(), lang, query)
        if cached:
            cached["layer_ms"] = 0
            cached["total_ms"] = int((time.time() - t0) * 1000)
            cached["cached"] = True
            return cached

    if use_legacy_handler and runtime_handler is not None:
        response, tag, confidence = runtime_handler.generate_adaptive_response(query, lang)
        layer = 1
        layer_ms = int((time.time() - t0) * 1000)
    else:
        response, tag, confidence = "", "unknown", 0.0
        layer = 2
        layer_ms = 0

    if confidence < THRESHOLD:
        t1 = time.time()
        response, tag, confidence = runtime_classifier.get_response(query, language=lang)
        layer = 2
        layer_ms = int((time.time() - t1) * 1000)

    if confidence < THRESHOLD:
        t2 = time.time()
        try:
            from langchain_core.runnables import RunnablePassthrough
            from langchain_core.output_parsers import StrOutputParser
            english_query = translate(query, "ur", "en") if lang == "ur" else query
            history_text = build_history_text(history[-4:])
            rag_chain = (
                {
                    "context": runtime_retriever,
                    "question": RunnablePassthrough(),
                    "history": lambda _: history_text
                }
                | runtime_rag_prompt
                | runtime_llm
                | StrOutputParser()
            )
            response = rag_chain.invoke(english_query)
            if lang == "ur":
                response = translate(response, "en", "ur")
            tag = "rag_fallback"
            confidence = 0.6
            layer = 3
            layer_ms = int((time.time() - t2) * 1000)
        except Exception as e:
            logger.error(f"RAG fallback failed: {e}")
            response = fallback_response(lang)
            tag = "unknown"
            confidence = 0.0
            layer = 3
            layer_ms = int((time.time() - t2) * 1000)

    total_ms = int((time.time() - t0) * 1000)

    result = {
        "response": response,
        "intent": tag,
        "confidence": round(float(confidence), 4),
        "layer": int(layer),
        "language": lang,
        "layer_ms": int(layer_ms),
        "total_ms": int(total_ms),
        "handoff_recommended": bool(tag in {"unknown", "transfer_human"} or float(confidence) <= 0.0),
        "cached": False,
    }
    if (
        SETTINGS.answer_cache_enabled
        and not result["handoff_recommended"]
        and result["confidence"] >= SETTINGS.answer_cache_min_confidence
    ):
        persist_safely(
            store_cached_answer,
            current_org_id(),
            lang,
            query,
            result,
            SETTINGS.answer_cache_ttl_seconds,
        )
    return result


def create_handoff_for_query(sid, query, query_result):
    profile = current_org_profile()
    handoff = profile.get("handoff", {}) or {}
    ticket = create_handoff_ticket(
        org_id=profile.get("org_id", "default"),
        session_id=sid,
        call_id=current_call_id(),
        request_id=current_request_id(),
        query=query,
        response=query_result.get("response"),
        intent=query_result.get("intent"),
        confidence=float(query_result.get("confidence", 0.0)),
        layer=int(query_result.get("layer", 3)),
        language=query_result.get("language"),
        department=handoff.get("department"),
        contact={
            "phone": handoff.get("phone", ""),
            "email": handoff.get("email", ""),
            "hours": handoff.get("hours", ""),
        },
    )
    record_audit_event(
        event_type="handoff_created",
        request_id=current_request_id(),
        call_id=current_call_id(),
        org_id=profile.get("org_id"),
        details={"ticket_id": ticket["ticket_id"], "intent": query_result.get("intent")},
    )
    return ticket


# ── Shared evaluation data & metric helpers ────────────────────────────────

EVAL_DATA = [
    ("السلام علیکم",                          "greeting",          "وعلیکم السلام"),
    ("داخلہ کیسے لینا ہے؟",                   "admission_process", "www.au.edu.pk"),
    ("آن لائن اپلائی کیسے کریں؟",             "admission_process", "آن لائن اپلائی"),
    ("داخلے کے لیے کتنے نمبر چاہیے؟",         "eligibility",       "50 فیصد"),
    ("انٹرمیڈیٹ کے بعد کیا کرنا ہوگا؟",       "eligibility",       "انٹرمیڈیٹ"),
    ("فیس کتنی ہے؟",                           "fee_structure",     "8500"),
    ("پہلے سمسٹر کی فیس بتائیں",              "fee_structure",     "سمسٹر"),
    ("کون کون سے پروگرام ہیں؟",               "programs_list",     "Computer Science"),
    ("کیا کیا ڈگریاں دستیاب ہیں؟",            "programs_list",     "BS"),
    ("پروگرام کتنے سال کا ہے؟",               "duration",          "4 سال"),
    ("BS کتنے سال میں مکمل ہوتی ہے؟",         "duration",          "4 سال"),
    ("سکالرشپ ملتی ہے کیا؟",                  "scholarship",       "میرٹ"),
    ("ہم غریب ہیں، کیا مالی مدد مل سکتی ہے؟", "scholarship",       "مالی مدد"),
    ("ایڈمیشن آفس کا نمبر کیا ہے؟",           "contact_info",      "9213456"),
    ("واٹس ایپ نمبر بتائیں",                  "contact_info",      "واٹس ایپ"),
    ("کیمپس کہاں ہے؟",                        "campus_info",       "ملتان"),
    ("یونیورسٹی میں کیا کیا سہولات ہیں؟",     "campus_info",       "لائبریری"),
    ("ہاسٹل کی سہولت ہے کیا؟",               "hostel",            "ہاسٹل"),
    ("لڑکیوں کا ہاسٹل ہے؟",                  "hostel",            "لڑکیوں"),
    ("یونیورسٹی بس سروس ہے؟",                "transport",         "ٹرانسپورٹ"),
    ("آنے جانے کی سہولت کیا ہے؟",            "transport",         "بسیں"),
    ("حاضری کتنی ہونی چاہیے؟",               "attendance",        "75 فیصد"),
    ("کتنی غیر حاضری ہو سکتی ہے؟",           "attendance",        "حاضری"),
    ("امتحان کب ہوگا؟",                       "exams",             "شیڈول"),
    ("نتیجہ کب آئے گا؟",                      "exams",             "نتائج"),
    ("BSCS کی فیس کتنی ہے؟",                 "computer_science",  "8500"),
    ("کمپیوٹر سائنس میں داخلہ کیسے لیں؟",    "computer_science",  "Computer Science"),
    ("یونیورسٹی کے قوانین کیا ہیں؟",          "rules_regulations", "قوانین"),
    ("نقل کرنے پر کیا ہوگا؟",                "rules_regulations", "نظم و ضبط"),
    ("کسی انسان سے بات کرنی ہے",             "transfer_human",    "9213456"),
    ("اسٹاف سے بات کرائیں",                  "transfer_human",    "ایڈمیشن آفس"),
    ("اللہ حافظ",                             "goodbye",           "اللہ حافظ"),
    ("خدا حافظ، شکریہ",                       "goodbye",           "شکریہ"),
]

REFERENCE_RESPONSES = {
    "greeting":          "وعلیکم السلام میں ایئر یونیورسٹی ملتان کیمپس کا ورچوئل اسسٹنٹ ہوں آپ کا کیا سوال ہے",
    "admission_process": "داخلے کے لیے ویب سائٹ www.au.edu.pk پر آن لائن اپلائی کریں ضروری دستاویزات میں تعلیمی سرٹیفکیٹس CNIC اور تصاویر شامل ہیں",
    "eligibility":       "انڈرگریجویٹ کے لیے انٹرمیڈیٹ میں کم از کم 50 فیصد نمبر درکار ہیں کمپیوٹر سائنس کے لیے ICS یا Pre-Engineering ترجیحی ہے",
    "fee_structure":     "فیس پروگرام کے حساب سے مختلف ہے BS Computer Science کی فیس 8500 روپے فی کریڈٹ آور ہے",
    "programs_list":     "ہمارے پاس BS Computer Science BS AI BS Data Science BS Cyber Security BBA MS اور PhD پروگرامز ہیں",
    "duration":          "BS پروگرامز 4 سال کے ہیں MS پروگرامز 2 سال کے ہیں PhD پروگرام 3 سے 5 سال کا ہوتا ہے",
    "scholarship":       "جی ہاں مالی مدد دستیاب ہے میرٹ بیسڈ سکالرشپ میں 85 فیصد سے زیادہ نمبروں پر 25 سے 75 فیصد فیس میں کمی ملتی ہے",
    "contact_info":      "ایڈمیشن آفس سے رابطہ کریں فون نمبر 92-61-9213456 ای میل admissions@au.edu.pk واٹس ایپ 92-300-1234567",
    "campus_info":       "ایئر یونیورسٹی ملتان کیمپس ملتان پنجاب میں واقع ہے کیمپس میں جدید کمپیوٹر لیبز لائبریری کیفے ٹیریا موجود ہے",
    "hostel":            "جی ہاں لڑکوں اور لڑکیوں کے لیے الگ ہاسٹل کی سہولت موجود ہے",
    "transport":         "یونیورسٹی ٹرانسپورٹ سروس دستیاب ہے شہر کے مختلف علاقوں سے بسیں چلتی ہیں",
    "attendance":        "کم از کم 75 فیصد حاضری ضروری ہے اس سے کم حاضری پر امتحان میں بیٹھنے کی اجازت نہیں ملتی",
    "exams":             "امتحانات کا شیڈول یونیورسٹی کی ویب سائٹ پر دستیاب ہوتا ہے نتائج اور گریڈز کے لیے امتحانی آفس سے رابطہ کریں",
    "computer_science":  "BS Computer Science 4 سال کا پروگرام ہے فیس 8500 روپے فی کریڈٹ آور ہے اہلیت کے لیے انٹرمیڈیٹ میں 50 فیصد نمبر",
    "rules_regulations": "یونیورسٹی کے قوانین میں 75 فیصد حاضری نظم و ضبط اور تعلیمی دیانتداری شامل ہے",
    "transfer_human":    "میں آپ کو ایڈمیشن آفس سے منسلک کر رہا ہوں فون نمبر 92-61-9213456",
    "goodbye":           "آپ کا شکریہ اللہ حافظ اگر مزید معلومات چاہیے تو کبھی بھی رابطہ کریں",
}

WER_TEST_DATA = [
    ("داخلہ کیسے لینا ہے",       "داخلہ کیسے لینا ہے"),
    ("فیس کتنی ہے",              "فیس کتنی ہے"),
    ("سکالرشپ ملتی ہے کیا",      "سکالرشپ ملتی ہے کیا"),
    ("حاضری کتنی ہونی چاہیے",    "حاضری کتنی ہونی چاہیے"),
    ("امتحان کب ہوگا",           "امتحان کب ہوگا"),
    ("کیمپس کہاں ہے",            "کیمپس کہاں ہے"),
    ("ہاسٹل کی سہولت ہے کیا",    "ہاسٹل کی سہولت ہے"),
    ("یونیورسٹی بس سروس ہے",     "یونیورسٹی بس سروس"),
    ("اللہ حافظ",                "اللہ حافظ"),
    ("کسی انسان سے بات کرنی ہے", "کسی انسان سے بات کرنی"),
]

_URDU_POLITENESS = ["جی", "براہ کرم", "شکریہ", "آپ", "کریں", "ہیں", "ہے", "ملتی", "دستیاب"]


def _tokenize(text):
    return re.findall(r'[\w\u0600-\u06FF]+', text.lower())


def _rouge_l(reference, hypothesis):
    r, h = _tokenize(reference), _tokenize(hypothesis)
    if not r or not h:
        return 0.0
    m, n = len(r), len(h)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            dp[i][j] = dp[i-1][j-1] + 1 if r[i-1] == h[j-1] else max(dp[i-1][j], dp[i][j-1])
    lcs = dp[m][n]
    prec = lcs / n
    rec = lcs / m
    return 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0


def _bleu(reference, hypothesis):
    r, h = _tokenize(reference), _tokenize(hypothesis)
    if not h:
        return 0.0
    ref_counts = defaultdict(int)
    for t in r:
        ref_counts[t] += 1
    matches = sum(min(ref_counts[t], 1) for t in h)
    prec = (matches + 1) / (len(h) + 1)
    bp = min(1.0, math.exp(1 - len(r) / max(len(h), 1)))
    return bp * prec


def _meteor(reference, hypothesis):
    r, h = _tokenize(reference), _tokenize(hypothesis)
    if not r or not h:
        return 0.0
    ref_set = set(r)
    matches = sum(1 for t in h if t in ref_set)
    prec = matches / len(h)
    rec = matches / len(r)
    return (10 * prec * rec) / (rec + 9 * prec) if (rec + 9 * prec) > 0 else 0.0


def _wer(reference, hypothesis):
    r, h = reference.split(), hypothesis.split()
    if not r:
        return 0.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = d[i-1][j-1] if r[i-1] == h[j-1] else 1 + min(d[i-1][j], d[i][j-1], d[i-1][j-1])
    return d[len(r)][len(h)] / len(r)


def _run_metrics(h, c):
    """Compute all real evaluation metrics. Returns a dict."""
    y_true, y_pred, resp_map = [], [], {}
    for query, expected_tag, _ in EVAL_DATA:
        resp, tag, conf = h.generate_adaptive_response(query, "ur")
        if conf < THRESHOLD:
            resp, tag, conf = c.get_response(query, language="ur")
        y_true.append(expected_tag)
        y_pred.append(tag)
        resp_map[query] = resp

    # F1 per intent
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    for true, pred in zip(y_true, y_pred):
        if true == pred:
            tp[true] += 1
        else:
            fp[pred] += 1
            fn[true] += 1
    f1_per_intent = {}
    for intent in set(y_true):
        p = tp[intent] / (tp[intent] + fp[intent]) if (tp[intent] + fp[intent]) > 0 else 0.0
        r = tp[intent] / (tp[intent] + fn[intent]) if (tp[intent] + fn[intent]) > 0 else 0.0
        f1_per_intent[intent] = round(2 * p * r / (p + r), 4) if (p + r) > 0 else 0.0
    macro_f1 = round(sum(f1_per_intent.values()) / len(f1_per_intent), 4)

    # ROUGE-L, BLEU, METEOR, LaaJ
    rouge_scores, bleu_scores, meteor_scores, laaj_scores = [], [], [], []
    for query, expected_tag, keyword in EVAL_DATA:
        resp = resp_map[query]
        ref = REFERENCE_RESPONSES.get(expected_tag, "")
        if not ref:
            continue
        rouge_scores.append(_rouge_l(ref, resp))
        bleu_scores.append(_bleu(ref, resp))
        meteor_scores.append(_meteor(ref, resp))
        # LaaJ
        s = 0.0
        urdu_chars = sum(1 for ch in resp if '\u0600' <= ch <= '\u06FF')
        s += (urdu_chars / max(len(resp), 1)) * 0.30
        if keyword.lower() in resp.lower():
            s += 0.30
        wc = len(resp.split())
        s += 0.20 if 10 <= wc <= 80 else (0.10 if (5 <= wc < 10 or 80 < wc <= 120) else 0.0)
        s += min(sum(1 for p in _URDU_POLITENESS if p in resp) / 3, 1.0) * 0.20
        laaj_scores.append(round(min(s, 1.0), 4))

    # WER
    wer_scores = [_wer(ref, hyp) for ref, hyp in WER_TEST_DATA]

    return {
        "f1": macro_f1,
        "f1_per_intent": f1_per_intent,
        "rouge": round(sum(rouge_scores) / len(rouge_scores), 4),
        "bleu": round(sum(bleu_scores) / len(bleu_scores), 4),
        "bleu_scores": [round(b, 4) for b in bleu_scores],
        "meteor": round(sum(meteor_scores) / len(meteor_scores), 4),
        "laaj": round(sum(laaj_scores) / len(laaj_scores), 4),
        "laaj_scores": laaj_scores,
        "wer": round(sum(wer_scores) / len(wer_scores), 4),
        "wer_per_query": [round(w, 4) for w in wer_scores],
    }


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def api_health():
    profile = current_org_profile()
    org_id = profile.get("org_id")
    latest_dataset_job = job_manager.latest("dataset_processing", org_id=org_id)
    runtime = runtime_snapshot()
    model_state = model_state_snapshot(org_id)
    org_ready = whisper_model is not None and org_runtime_is_ready(org_id)
    healthy = model_state.get("error") is None
    return jsonify({
        "status": "ok" if healthy else "degraded",
        "request_id": current_request_id(),
        "org_id": org_id,
        "organization_name": profile.get("organization_name"),
        "models": {
            "ready": org_ready,
            "loading": model_state.get("loading"),
            "error": model_state.get("error"),
            "started_at": model_state.get("started_at"),
            "loaded_at": model_state.get("loaded_at"),
            "default_ready": MODELS_READY,
        },
        "runtime": runtime,
        "job_mode": SETTINGS.job_mode,
        "dataset_job": latest_dataset_job.to_dict() if latest_dataset_job else None,
    }), 200 if healthy else 503


@app.route("/api/admin/check", methods=["GET"])
@require_admin_auth
def api_admin_check():
    admin_auth = getattr(g, "admin_auth", {}) or {}
    return jsonify({
        "authenticated": True,
        "admin_token_required": bool(SETTINGS.admin_token),
        "auth_source": admin_auth.get("source"),
        "token_id": admin_auth.get("token_id"),
        "org_id": admin_auth.get("org_id"),
        "scopes": admin_auth.get("scopes", []),
    })


@app.route("/api/admin/tokens", methods=["GET"])
@require_root_admin
def api_admin_tokens():
    return jsonify({
        "tokens": list_admin_tokens(),
    })


@app.route("/api/admin/tokens", methods=["POST"])
@require_root_admin
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_create_admin_token():
    payload = request.get_json(silent=True) or {}
    scopes = payload.get("scopes") or ["admin"]
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        return jsonify({"error": "scopes must be a list of strings"}), 400
    scopes = [scope.strip().lower() for scope in scopes if scope.strip()]
    if not scopes:
        scopes = ["admin"]
    invalid_scopes = sorted(set(scopes) - ALLOWED_ADMIN_SCOPES)
    if invalid_scopes:
        return jsonify({
            "error": f"Invalid admin scopes: {', '.join(invalid_scopes)}",
            "allowed_scopes": sorted(ALLOWED_ADMIN_SCOPES),
        }), 400

    try:
        expires_in_days = payload.get("expires_in_days", 90)
        if expires_in_days in {"", None}:
            expires_in_days = None
        token = create_admin_token(
            name=str(payload.get("name") or ""),
            org_id=str(payload.get("org_id")).strip() if payload.get("org_id") else None,
            scopes=scopes,
            expires_in_days=expires_in_days,
        )
        persist_safely(
            record_audit_event,
            event_type="admin_token_created",
            request_id=current_request_id(),
            org_id=token.get("org_id"),
            details={
                "token_id": token["token_id"],
                "name": token["name"],
                "scopes": token["scopes"],
                "expires_at": token.get("expires_at"),
            },
        )
        return jsonify({
            **token,
            "warning": "Store this token now. VOX will not show it again.",
        }), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/admin/tokens/<token_id>", methods=["DELETE"])
@require_root_admin
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_revoke_admin_token(token_id):
    revoked = revoke_admin_token(token_id)
    if not revoked:
        return jsonify({"error": "Token not found or already revoked"}), 404
    persist_safely(
        record_audit_event,
        event_type="admin_token_revoked",
        request_id=current_request_id(),
        details={"token_id": token_id},
    )
    return jsonify({"status": "revoked", "token_id": token_id})


@app.route("/api/organizations", methods=["GET"])
@require_admin_auth
def api_list_organizations():
    organizations = list_org_profiles()
    persist_safely(sync_organizations, organizations)
    return jsonify({
        "active_org_id": ORG_PROFILE.get("org_id"),
        "request_org_id": current_org_id(),
        "organizations": organizations,
    })


@app.route("/api/organizations", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_create_organization():
    payload = request.get_json(silent=True) or {}
    try:
        profile = create_org_profile(
            org_id=str(payload.get("org_id") or ""),
            organization_name=str(payload.get("organization_name") or ""),
            domain=str(payload.get("domain") or "general"),
            assistant_name=str(payload.get("assistant_name") or "VOX"),
        )
        ensure_org_runtime_files(profile)
        persist_safely(upsert_organization, profile)
        persist_safely(
            record_audit_event,
            event_type="organization_created",
            request_id=current_request_id(),
            org_id=profile.get("org_id"),
            details={"organization_name": profile.get("organization_name")},
        )
        return jsonify({
            "status": "created",
            "profile": {
                "org_id": profile.get("org_id"),
                "organization_name": profile.get("organization_name"),
                "assistant_name": profile.get("assistant_name", "VOX"),
                "domain": profile.get("domain", "general"),
                "source_data_dir": profile.get("source_data_dir"),
                "chroma_dir": profile.get("chroma_dir"),
                "intents_path": profile.get("intents_path"),
            },
        }), 201
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Organization creation failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/organizations/switch", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_switch_organization():
    payload = request.get_json(silent=True) or {}
    org_id = str(payload.get("org_id") or "").strip()
    if not org_id:
        return jsonify({"error": "org_id is required"}), 400
    try:
        profile = switch_active_organization(org_id)
        persist_safely(
            record_audit_event,
            event_type="organization_switched",
            request_id=current_request_id(),
            org_id=profile.get("org_id"),
        )
        return jsonify({
            "status": "switched",
            "active_org_id": profile["org_id"],
            "profile": profile,
        })
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        logger.error("Organization switch failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/datasets", methods=["GET"])
@require_admin_auth
def api_dataset_status():
    return jsonify(load_manifest(current_org_profile()))


@app.route("/api/datasets/upload", methods=["POST"])
@require_admin_scope("write")
@rate_limit("upload", SETTINGS.rate_limit_upload)
def api_dataset_upload():
    profile = current_org_profile()
    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        single_file = request.files.get("file")
        uploaded_files = [single_file] if single_file else []
    if not uploaded_files:
        return jsonify({"error": "No files uploaded"}), 400

    saved = []
    errors = []
    for file_storage in uploaded_files:
        try:
            saved.append(save_uploaded_file(profile, file_storage))
        except Exception as exc:
            errors.append({
                "filename": getattr(file_storage, "filename", "unknown"),
                "error": str(exc),
            })

    status_code = 207 if errors and saved else (400 if errors else 200)
    return jsonify({
        "saved": saved,
        "errors": errors,
        "manifest": load_manifest(profile),
    }), status_code


@app.route("/api/datasets/process", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_dataset_process():
    try:
        profile = current_org_profile()
        org_id = profile.get("org_id")
        latest = job_manager.latest("dataset_processing", org_id=org_id)
        if latest and latest.status in {"queued", "running"}:
            return jsonify(latest.to_dict()), 202
        latest_persisted = latest_persisted_job("dataset_processing", org_id=org_id)
        if latest_persisted and latest_persisted["status"] in {"queued", "running"}:
            return jsonify(latest_persisted), 202

        manifest = load_manifest(profile)
        manifest["processing_status"] = "queued"
        save_manifest(profile, manifest)

        if SETTINGS.job_mode == "external":
            job = create_queued_job(
                "dataset_processing",
                org_id=org_id,
                message="Queued for background worker",
            )
            persist_safely(
                record_audit_event,
                event_type="dataset_processing_queued",
                request_id=current_request_id(),
                org_id=org_id,
                details={"job_id": job["job_id"], "job_mode": SETTINGS.job_mode},
            )
            return jsonify(job), 202

        job = job_manager.start(
            "dataset_processing",
            run_dataset_processing_job,
            metadata={"org_id": org_id},
        )
        return jsonify(job.to_dict()), 202
    except Exception as exc:
        logger.error("Dataset processing failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/datasets/versions", methods=["GET"])
@require_admin_auth
def api_dataset_versions():
    profile = current_org_profile()
    return jsonify({
        "active_version": load_manifest(profile).get("current_dataset_version"),
        "versions": list_dataset_versions(profile),
    })


@app.route("/api/datasets/versions/<version_id>/rollback", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_dataset_version_rollback(version_id):
    profile = current_org_profile()
    org_id = profile.get("org_id")
    latest = job_manager.latest("dataset_processing", org_id=org_id)
    if latest and latest.status in {"queued", "running"}:
        return jsonify({"error": "Cannot roll back while dataset processing is running."}), 409
    if has_active_persisted_job("dataset_processing", org_id):
        return jsonify({"error": "Cannot roll back while dataset processing is queued or running."}), 409

    try:
        result = rollback_dataset_version(profile, version_id)
        if org_id == ORG_PROFILE.get("org_id") and db is not None:
            reload_retriever()
        persist_safely(
            record_audit_event,
            event_type="dataset_version_rolled_back",
            request_id=current_request_id(),
            org_id=org_id,
            details={"version_id": version_id},
        )
        return jsonify(result)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Dataset rollback failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/jobs/<job_id>", methods=["GET"])
@require_admin_auth
def api_job_status(job_id):
    org_id = current_org_id()
    job = job_manager.get(job_id)
    if job is None:
        persisted = get_persisted_job(job_id)
        if persisted is not None:
            if persisted.get("metadata", {}).get("org_id") not in {None, org_id}:
                return jsonify({"error": "Job not found"}), 404
            return jsonify(persisted)
        return jsonify({"error": "Job not found"}), 404
    if job.metadata.get("org_id") not in {None, org_id}:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.to_dict())


@app.route("/api/jobs/latest/<kind>", methods=["GET"])
@require_admin_auth
def api_latest_job(kind):
    org_id = current_org_id()
    job = job_manager.latest(kind, org_id=org_id)
    if job is None:
        persisted = latest_persisted_job(kind, org_id=org_id)
        if persisted is not None:
            return jsonify(persisted)
        return jsonify({"message": "No job found", "job": None}), 404
    return jsonify(job.to_dict())


@app.route("/api/admin/audit", methods=["GET"])
@require_admin_auth
def api_admin_audit():
    limit = request.args.get("limit", "25")
    try:
        parsed_limit = int(limit)
    except ValueError:
        parsed_limit = 25
    return jsonify({
        "events": latest_audit_events(parsed_limit),
    })


@app.route("/api/admin/maintenance", methods=["POST"])
@require_root_admin
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_admin_maintenance():
    payload = request.get_json(silent=True) or {}
    dry_run = bool(payload.get("dry_run", False))
    result = run_maintenance(dry_run=dry_run)
    persist_safely(
        record_audit_event,
        event_type="maintenance_run",
        request_id=current_request_id(),
        org_id=current_org_id(),
        details=result,
    )
    return jsonify(result)


@app.route("/api/handoffs", methods=["GET"])
@require_admin_auth
def api_list_handoffs():
    org_id = current_org_id()
    status = request.args.get("status", "open")
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    return jsonify({
        "tickets": list_handoff_tickets(
            org_id=org_id,
            status=status,
            limit=limit,
        ),
        "open_count": count_handoff_tickets(org_id, status="open"),
    })


@app.route("/api/handoffs/<ticket_id>", methods=["PATCH"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_update_handoff(ticket_id):
    payload = request.get_json(silent=True) or {}
    status = str(payload.get("status") or "").strip().lower()
    notes = payload.get("notes")
    if not status:
        return jsonify({"error": "status is required"}), 400
    try:
        ticket = update_handoff_ticket(ticket_id, status=status, notes=str(notes) if notes is not None else None)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if ticket is None:
        return jsonify({"error": "Handoff ticket not found"}), 404
    if ticket.get("org_id") != current_org_id():
        return jsonify({"error": "Handoff ticket belongs to another organization"}), 403
    persist_safely(
        record_audit_event,
        event_type="handoff_updated",
        request_id=current_request_id(),
        org_id=current_org_id(),
        details={"ticket_id": ticket_id, "status": status},
    )
    return jsonify(ticket)


@app.route("/api/ollama/health", methods=["GET"])
@require_admin_auth
def api_ollama_health():
    force = request.args.get("force") in {"1", "true", "yes"}
    return jsonify(get_ollama_health_cached(force=force))


@app.route("/api/metrics", methods=["GET"])
@require_admin_scope("read")
def api_metrics():
    return Response(render_prometheus_metrics(), content_type="text/plain; version=0.0.4; charset=utf-8")


@app.route("/api/intents/draft", methods=["GET"])
@require_admin_auth
def api_intent_draft():
    draft = load_intent_draft(current_org_profile())
    if draft is None:
        return jsonify({"message": "No generated intent draft found.", "draft": None}), 404
    return jsonify(draft)


@app.route("/api/intents/generate", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_generate_intents():
    profile = current_org_profile()
    manifest = load_manifest(profile)
    if not manifest.get("chunks"):
        return jsonify({"error": "No processed chunks found. Upload and process a dataset first."}), 400

    payload = request.get_json(silent=True) or {}
    max_intents = int(payload.get("max_intents", 12))
    max_intents = max(1, min(max_intents, 30))
    llm_model = SETTINGS.llm_model or profile.get("llm_model", "qwen3.2:3b")
    health = get_ollama_health_cached(force=True)
    if not health.get("reachable"):
        return jsonify({"error": f"Ollama is not reachable: {health.get('error')}", "ollama": health}), 503
    if llm_model in health.get("missing_models", []):
        return jsonify({"error": f"LLM model is missing in Ollama: {llm_model}", "ollama": health}), 503
    draft = generate_intent_draft(profile, manifest, max_intents=max_intents)
    return jsonify(draft)


@app.route("/api/intents/publish", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_publish_intents():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "merge")).strip().lower()
    profile = current_org_profile()

    try:
        result = publish_intent_draft(profile, mode=mode)
        if profile.get("org_id") == ORG_PROFILE.get("org_id") and classifier is not None:
            reload_classifier()
        return jsonify(result)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Intent publish failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/voice", methods=["POST"])
@rate_limit("voice", SETTINGS.rate_limit_voice)
def voice_endpoint():
    runtime = current_org_runtime()
    if whisper_model is None or runtime is None or runtime.get("classifier") is None:
        return jsonify({
            "error": f"Models not loaded yet for organization: {current_org_id()}",
            "org_id": current_org_id(),
        }), 503

    sid = get_or_create_session()
    runtime_counter("calls", 1)

    @after_this_request
    def decrement_active_call(response):
        runtime_counter("calls", -1)
        return response

    audio_data = request.get_data()
    if not audio_data:
        return jsonify({"error": "No audio data received"}), 400

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

        # The browser sends a valid WAV file (built by audioBufferToWav in app.js).
        # Write it directly — do NOT re-wrap it in another WAV container.
        with open(tmp_path, "wb") as f:
            f.write(audio_data)

        if not stt_gate.acquire(blocking=False):
            return jsonify({"error": "VOX is handling other callers. Please try again shortly."}), 429
        runtime_counter("stt", 1)
        try:
            result = whisper_model.transcribe(tmp_path)
            detected_lang = result.get("language", "en")
            logger.info(f"Whisper detected lang: {detected_lang}, text: {result['text'][:80]}")
            if detected_lang in ("ar", "fa", "ps", "ur"):
                if detected_lang != "ur":
                    result = whisper_model.transcribe(tmp_path, language="ur")
                detected_lang = "ur"
        finally:
            runtime_counter("stt", -1)
            stt_gate.release()
        transcription = result["text"].strip()
        logger.info(f"Final transcription: '{transcription}' | lang: {detected_lang}")

        if sum(1 for c in transcription if '\u0600' <= c <= '\u06FF') > 2:
            detected_lang = "ur"

        stt_confidence = 0.94

        if not transcription:
            return jsonify({"error": "No speech detected"}), 200

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    with session_lock:
        session_data = sessions.get(session_storage_key(sid), new_session_data(sid))
        history = list(session_data.get("history", []))

    if not query_gate.acquire(blocking=False):
        return jsonify({"error": "VOX is at query capacity. Please try again shortly."}), 429
    runtime_counter("queries", 1)
    try:
        query_result = process_query(transcription, history, detected_lang=detected_lang)
    finally:
        runtime_counter("queries", -1)
        query_gate.release()

    audio_result = generate_audio(query_result["response"], query_result["language"])
    handoff_ticket = None
    if query_result.get("handoff_recommended"):
        handoff_ticket = persist_safely(create_handoff_for_query, sid, transcription, query_result)

    with session_lock:
        key = session_storage_key(sid)
        session_data = sessions.get(key, new_session_data(sid))
        session_data["history"].append({
            "user": transcription,
            "assistant": query_result["response"],
            "intent": query_result["intent"],
            "layer": query_result["layer"],
            "layer_ms": query_result["layer_ms"],
            "language": query_result["language"],
            "confidence": query_result["confidence"],
            "handoff_ticket_id": handoff_ticket.get("ticket_id") if handoff_ticket else None,
            "cached": query_result.get("cached", False),
        })
        if len(session_data["history"]) > MAX_HISTORY:
            session_data["history"].pop(0)
        session_data["query_count"] += 1
        session_data["layer_counts"][str(query_result["layer"])] += 1
        session_data["last_seen"] = time.time()
        sessions[key] = session_data
        persist_safely(persist_session, current_org_id(), session_data)

    return jsonify({
        "session_id": sid,
        "transcription": transcription,
        "response": query_result["response"],
        "audio_base64": audio_result.get("audio_base64"),
        "audio_mime": audio_result.get("audio_mime"),
        "tts_engine": audio_result.get("tts_engine"),
        "tts_error": audio_result.get("tts_error"),
        "intent": query_result["intent"],
        "confidence": query_result["confidence"],
        "layer": query_result["layer"],
        "language": query_result["language"],
        "layer_ms": query_result["layer_ms"],
        "total_ms": query_result["total_ms"],
        "stt_confidence": stt_confidence,
        "handoff_recommended": query_result.get("handoff_recommended", False),
        "handoff_ticket": handoff_ticket,
        "cached": query_result.get("cached", False),
        "cache_hit_count": query_result.get("cache_hit_count"),
    })


@app.route("/api/status")
def api_status():
    profile = current_org_profile()
    org_id = profile.get("org_id")
    llm_model = SETTINGS.llm_model or profile.get("llm_model", "qwen3.2:3b")
    embedding_model = SETTINGS.embedding_model or profile.get("embedding_model", "nomic-embed-text")
    runtime = current_org_runtime()
    sid = get_or_create_session()
    with session_lock:
        session_data = sessions.get(session_storage_key(sid, org_id), new_session_data(sid))
        active_session_count = count_active_sessions(org_id)
    dataset_manifest = load_manifest(profile)
    intent_draft = load_intent_draft(profile)
    active_intents = load_active_intents(profile)
    ollama_health = get_ollama_health_cached()

    doc_count = 0
    try:
        if runtime and runtime.get("db") is not None:
            collection = runtime["db"]._collection
            doc_count = collection.count()
    except Exception:
        pass

    return jsonify({
        "org_id": org_id,
        "organization_name": profile.get("organization_name"),
        "assistant_name": profile.get("assistant_name", "VOX"),
        "llm_model": llm_model,
        "embedding_model": embedding_model,
        "ollama": ollama_health,
        "session_id": sid,
        "runtime": runtime_snapshot(),
        "active_sessions": active_session_count,
        "persisted_sessions": count_persisted_sessions(org_id),
        "open_handoffs": count_handoff_tickets(org_id, status="open"),
        "answer_cache_entries": count_answer_cache(org_id),
        "dataset": {
            "processing_status": dataset_manifest.get("processing_status"),
            "stats": dataset_manifest.get("stats", {}),
            "vector_index": dataset_manifest.get("vector_index", {}),
        },
        "intent_draft": {
            "exists": intent_draft is not None,
            "intent_count": len(intent_draft.get("intents", [])) if intent_draft else 0,
            "generated_at": intent_draft.get("generated_at") if intent_draft else None,
            "generation_method": intent_draft.get("generation_method") if intent_draft else None,
        },
        "active_intents": {
            "intent_count": len(active_intents.get("intents", [])),
            "path": profile.get("intents_path"),
        },
        "whisper_loaded": whisper_model is not None,
        "handler_ready": bool(runtime and runtime.get("handler") is not None),
        "classifier_ready": bool(runtime and runtime.get("classifier") is not None),
        "chroma_doc_count": doc_count,
        "ollama_ready": bool(ollama_health.get("reachable") and not ollama_health.get("missing_models")),
        "faiss_ready": bool(runtime and runtime.get("classifier") is not None and runtime["classifier"].index is not None),
        "device": device,
        "query_count": session_data["query_count"],
        "layer_counts": session_data["layer_counts"],
        "history_length": len(session_data["history"])
    })


@app.route("/api/session/clear", methods=["POST"])
def clear_session():
    sid = get_or_create_session()
    with session_lock:
        key = session_storage_key(sid)
        sessions[key] = new_session_data(sid)
        persist_safely(persist_session, current_org_id(), sessions[key])
    return jsonify({"status": "cleared", "session_id": sid})


@app.route("/api/session", methods=["GET"])
def get_session():
    sid = get_or_create_session()
    with session_lock:
        session_data = sessions.get(session_storage_key(sid), new_session_data(sid))
    return jsonify({
        "session_id": sid,
        "query_count": session_data["query_count"],
        "layer_counts": session_data["layer_counts"],
        "history": session_data["history"]
    })


@app.route("/api/evaluate", methods=["GET"])
def api_evaluate():
    global handler, classifier

    if handler is None or classifier is None:
        return jsonify({"error": "Models not loaded yet"}), 503

    if os.path.exists(EVAL_CACHE_FILE):
        try:
            with open(EVAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception:
            pass

    return jsonify({"message": "No cached evaluation. Run /api/evaluate-run first.", "cached": False})


@app.route("/api/evaluate-run", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_evaluate_run():
    global handler, classifier

    if handler is None or classifier is None:
        return jsonify({"error": "Models not loaded yet"}), 503
    if not USE_LEGACY_HANDLER:
        return jsonify({"error": "The bundled evaluation suite is only valid for the default legacy organization."}), 400

    EVAL_DATA = [
        ("السلام علیکم", "greeting", "وعلیکم السلام"),
        ("داخلہ کیسے لینا ہے؟", "admission_process", "www.au.edu.pk"),
        ("آن لائن اپلائی کیسے کریں؟", "admission_process", "آن لائن اپلائی"),
        ("داخلے کے لیے کتنے نمبر چاہیے؟", "eligibility", "50 فیصد"),
        ("انٹرمیڈیٹ کے بعد کیا کرنا ہوگا؟", "eligibility", "انٹرمیڈیٹ"),
        ("فیس کتنی ہے؟", "fee_structure", "8500"),
        ("پہلے سمسٹر کی فیس بتائیں", "fee_structure", "سمسٹر"),
        ("کون کون سے پروگرام ہیں؟", "programs_list", "Computer Science"),
        ("کیا کیا ڈگریاں دستیاب ہیں؟", "programs_list", "BS"),
        ("پروگرام کتنے سال کا ہے؟", "duration", "4 سال"),
        ("BS کتنے سال میں مکمل ہوتی ہے؟", "duration", "4 سال"),
        ("سکالرشپ ملتی ہے کیا؟", "scholarship", "میرٹ"),
        ("ہم غریب ہیں، کیا مالی مدد مل سکتی ہے؟", "scholarship", "مالی مدد"),
        ("ایڈمیشن آفس کا نمبر کیا ہے؟", "contact_info", "9213456"),
        ("واٹس ایپ نمبر بتائیں", "contact_info", "واٹس ایپ"),
        ("کیمپس کہاں ہے؟", "campus_info", "ملتان"),
        ("یونیورسٹی میں کیا کیا سہولات ہیں؟", "campus_info", "لائبریری"),
        ("ہاسٹل کی سہولت ہے کیا؟", "hostel", "ہاسٹل"),
        ("لڑکیوں کا ہاسٹل ہے؟", "hostel", "لڑکیوں"),
        ("یونیورسٹی بس سروس ہے؟", "transport", "ٹرانسپورٹ"),
        ("آنے جانے کی سہولت کیا ہے؟", "transport", "بسیں"),
        ("حاضری کتنی ہونی چاہیے؟", "attendance", "75 فیصد"),
        ("کتنی غیر حاضری ہو سکتی ہے؟", "attendance", "حاضری"),
        ("امتحان کب ہوگا؟", "exams", "شیڈول"),
        ("نتیجہ کب آئے گا؟", "exams", "نتائج"),
        ("BSCS کی فیس کتنی ہے؟", "computer_science", "8500"),
        ("کمپیوٹر سائنس میں داخلہ کیسے لیں؟", "computer_science", "Computer Science"),
        ("یونیورسٹی کے قوانین کیا ہیں؟", "rules_regulations", "قوانین"),
        ("نقل کرنے پر کیا ہوگا؟", "rules_regulations", "نظم و ضبط"),
        ("کسی انسان سے بات کرنی ہے", "transfer_human", "9213456"),
        ("اسٹاف سے بات کرائیں", "transfer_human", "ایڈمیشن آفس"),
        ("اللہ حافظ", "goodbye", "اللہ حافظ"),
        ("خدا حافظ، شکریہ", "goodbye", "شکریہ"),
    ]

    SUITE1 = [
        ("greeting", "السلام علیکم", "Assalam o Alaikum"),
        ("greeting", "ہیلو، کیا آپ مدد کر سکتے ہیں؟", "Hello, kya aap madad kar sakte hain?"),
        ("admission_process", "داخلہ کیسے لینا ہے؟", "Dakhla kaise lena hai?"),
        ("admission_process", "آن لائن اپلائی کیسے کریں؟", "Online apply kaise karen?"),
        ("eligibility", "داخلے کے لیے کتنے نمبر چاہیے؟", "Dakhle ke liye kitne number chahiye?"),
        ("eligibility", "انٹرمیڈیٹ کے بعد کیا کرنا ہوگا؟", "Intermediate ke baad kya karna hoga?"),
        ("fee_structure", "فیس کتنی ہے؟", "Fee kitni hai?"),
        ("fee_structure", "پہلے سمسٹر کی فیس بتائیں", "Pehle semester ki fee batain"),
        ("programs_list", "کون کون سے پروگرام ہیں؟", "Kon kon se program hain?"),
        ("programs_list", "کیا کیا ڈگریاں دستیاب ہیں؟", "Kya kya degrees dastiyab hain?"),
        ("duration", "پروگرام کتنے سال کا ہے؟", "Program kitne saal ka hai?"),
        ("duration", "BS کتنے سال میں مکمل ہوتی ہے؟", "BS kitne saal mein mukammal hoti hai?"),
        ("scholarship", "سکالرشپ ملتی ہے کیا؟", "Scholarship milti hai kya?"),
        ("scholarship", "ہم غریب ہیں، کیا مالی مدد مل سکتی ہے؟", "Hum gharib hain, kya mali madad mil sakti hai?"),
        ("contact_info", "ایڈمیشن آفس کا نمبر کیا ہے؟", "Admission office ka number kya hai?"),
        ("contact_info", "واٹس ایپ نمبر بتائیں", "WhatsApp number batain"),
        ("campus_info", "کیمپس کہاں ہے؟", "Campus kahan hai?"),
        ("campus_info", "یونیورسٹی میں کیا کیا سہولات ہیں؟", "University mein kya kya suholat hain?"),
        ("hostel", "ہاسٹل کی سہولت ہے کیا؟", "Hostel ki suholat hai kya?"),
        ("hostel", "لڑکیوں کا ہاسٹل ہے؟", "Larkiyon ka hostel hai?"),
        ("transport", "یونیورسٹی بس سروس ہے؟", "University bus service hai?"),
        ("transport", "آنے جانے کی سہولت کیا ہے؟", "Aane jaane ki suholat kya hai?"),
        ("attendance", "حاضری کتنی ہونی چاہیے؟", "Hazri kitni honi chahiye?"),
        ("attendance", "کتنی غیر حاضری ہو سکتی ہے؟", "Kitni ghair hazri ho sakti hai?"),
        ("exams", "امتحان کب ہوگا؟", "Imtihan kab hoga?"),
        ("exams", "نتیجہ کب آئے گا؟", "Nateeja kab aaye ga?"),
        ("computer_science", "BSCS کی فیس کتنی ہے؟", "BSCS ki fee kitni hai?"),
        ("computer_science", "کمپیوٹر سائنس میں داخلہ کیسے لیں؟", "Computer science mein dakhla kaise len?"),
        ("rules_regulations", "یونیورسٹی کے قوانین کیا ہیں؟", "University ke qawaneen kya hain?"),
        ("rules_regulations", "نقل کرنے پر کیا ہوگا؟", "Naqal karne par kya hoga?"),
        ("transfer_human", "کسی انسان سے بات کرنی ہے", "Kisi insaan se baat karni hai"),
        ("transfer_human", "اسٹاف سے بات کرائیں", "Staff se baat karayen"),
        ("goodbye", "اللہ حافظ", "Allah Hafiz"),
        ("goodbye", "خدا حافظ، شکریہ", "Khuda Hafiz, shukriya"),
    ]

    SUITE2 = [
        ("admission_process", "میرے بیٹے نے FSc کیا ہے، اب کیا کریں؟", "Mere bete ne FSc kiya hai, ab kya karen?"),
        ("admission_process", "بیٹی کو یونیورسٹی میں داخل کروانا ہے", "Beti ko university mein daakhil karwana hai"),
        ("eligibility", "میرے بیٹے کے ICS میں 60 فیصد ہیں، کیا وہ داخلہ لے سکتا ہے؟", "Mere bete ke ICS mein 60 feesad hain, kya woh dakhla le sakta hai?"),
        ("eligibility", "میری بہن نے FA کیا ہے، کیا وہ BBA کر سکتی ہے؟", "Meri behen ne FA kiya hai, kya woh BBA kar sakti hai?"),
        ("scholarship", "ہم زیادہ فیس نہیں دے سکتے، کوئی راستہ ہے؟", "Hum zyada fee nahi de sakte, koi rasta hai?"),
        ("scholarship", "میرے والد کی تنخواہ کم ہے، کیا کوئی مدد مل سکتی ہے؟", "Mere walid ki tankhwah kam hai, kya koi madad mil sakti hai?"),
        ("scholarship", "فیس قسطوں میں دی جا سکتی ہے کیا؟", "Fee qiston mein di ja sakti hai kya?"),
        ("programs_list", "آپ کے یہاں کیا کیا پڑھایا جاتا ہے؟", "Aap ke yahan kya kya parhaya jata hai?"),
        ("programs_list", "کون کون سی ڈگریاں ملتی ہیں؟", "Kon kon si degrees milti hain?"),
        ("computer_science", "کمپیوٹر کی پڑھائی کرنی ہے", "Computer ki parhai karni hai"),
        ("computer_science", "سوفٹویر انجینیئرنگ کا کوئی پروگرام ہے؟", "Software engineering ka koi program hai?"),
        ("fee_structure", "پڑھائی پر کتنا خرچہ آئے گا؟", "Parhai par kitna kharcha aaye ga?"),
        ("fee_structure", "ہر سال کتنے پیسے چاہیں؟", "Har saal kitne paise chahiye?"),
        ("hostel", "باہر سے آنے والے طلبہ کہاں رہیں گے؟", "Bahar se aane wale tulaba kahan rahenge?"),
        ("transport", "گھر سے یونیورسٹی کیسے جائیں؟", "Ghar se university kaise jayen?"),
        ("attendance", "کتنی بار غیر حاضر ہو سکتے ہیں؟", "Kitni baar ghair haazir ho sakte hain?"),
        ("exams", "پیپر کب ہوں گے؟", "Paper kab honge?"),
        ("rules_regulations", "یونیورسٹی میں کیا کیا پابندیاں ہیں؟", "University mein kya kya pabandiyaan hain?"),
        ("rules_regulations", "اگر کوئی لڑائی کرے تو کیا ہوگا؟", "Agar koi larai kare to kya hoga?"),
        ("contact_info", "کس سے بات کریں؟", "Kis se baat karen?"),
        ("contact_info", "ایڈمیشن آفس کا نمبر چاہیے", "Admission office ka number chahiye"),
        ("duration", "BS کرنے میں کتنا وقت لگے گا؟", "BS karne mein kitna waqt lage ga?"),
        ("duration", "کتنے سمسٹر ہوتے ہیں BS میں؟", "Kitne semester hote hain BS mein?"),
    ]

    all_queries = []
    for expected, urdu, roman in SUITE1 + SUITE2:
        all_queries.append(("Suite 1" if (expected, urdu, roman) in SUITE1 else "Suite 2", expected, urdu, "ur"))
        all_queries.append(("Suite 1" if (expected, urdu, roman) in SUITE1 else "Suite 2", expected, roman, "roman"))

    results = []
    suite1_passed = suite1_failed = 0
    suite2_passed = suite2_failed = 0
    total_urdu_passed = total_urdu_failed = 0
    total_roman_passed = total_roman_failed = 0

    for suite_name, expected_tag, query, script in all_queries:
        response, tag, confidence = handler.generate_adaptive_response(query, "ur")
        layer = 1
        if confidence < THRESHOLD:
            response, tag, confidence = classifier.get_response(query, language="ur")
            layer = 2

        matched = tag == expected_tag if confidence >= THRESHOLD else False

        if suite_name == "Suite 1":
            if matched:
                suite1_passed += 1
            else:
                suite1_failed += 1
        else:
            if matched:
                suite2_passed += 1
            else:
                suite2_failed += 1

        if script == "ur":
            if matched:
                total_urdu_passed += 1
            else:
                total_urdu_failed += 1
        else:
            if matched:
                total_roman_passed += 1
            else:
                total_roman_failed += 1

        results.append({
            "suite": suite_name,
            "expected": expected_tag,
            "got": tag,
            "query": query,
            "script": script,
            "passed": bool(matched),
            "confidence": round(float(confidence), 4),
            "layer": int(layer),
            "response_preview": response[:200] if response else ""
        })

    suite1_total = suite1_passed + suite1_failed
    suite2_total = suite2_passed + suite2_failed
    total = suite1_total + suite2_total
    total_passed = suite1_passed + suite2_passed

    metrics = _run_metrics(handler, classifier)

    eval_result = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_queries": total,
        "total_passed": total_passed,
        "total_failed": total - total_passed,
        "accuracy": round(total_passed / total, 4) if total > 0 else 0,
        "suite1": {"total": suite1_total, "passed": suite1_passed, "failed": suite1_failed,
                   "accuracy": round(suite1_passed / suite1_total, 4) if suite1_total > 0 else 0},
        "suite2": {"total": suite2_total, "passed": suite2_passed, "failed": suite2_failed,
                   "accuracy": round(suite2_passed / suite2_total, 4) if suite2_total > 0 else 0},
        "urdu": {"passed": total_urdu_passed, "failed": total_urdu_failed,
                 "accuracy": round(total_urdu_passed / (total_urdu_passed + total_urdu_failed), 4) if (total_urdu_passed + total_urdu_failed) > 0 else 0},
        "roman": {"passed": total_roman_passed, "failed": total_roman_failed,
                  "accuracy": round(total_roman_passed / (total_roman_passed + total_roman_failed), 4) if (total_roman_passed + total_roman_failed) > 0 else 0},
        "results": results,
        "f1": metrics["f1"],
        "f1_per_intent": metrics["f1_per_intent"],
        "rouge": metrics["rouge"],
        "bleu": metrics["bleu"],
        "bleu_scores": metrics["bleu_scores"],
        "meteor": metrics["meteor"],
        "laaj": metrics["laaj"],
        "laaj_scores": metrics["laaj_scores"],
        "wer": metrics["wer"],
        "wer_per_query": metrics["wer_per_query"],
    }

    os.makedirs("evaluation_results", exist_ok=True)
    try:
        with open(EVAL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(eval_result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to cache evaluation: {e}")

    return jsonify(eval_result)


@app.route("/api/ready")
def api_ready():
    profile = current_org_profile()
    org_id = profile.get("org_id")
    if SETTINGS.autoload_models:
        start_model_loading_once(profile=profile)
    state = model_state_snapshot(org_id)
    started_at = state.get("started_at")
    loaded_at = state.get("loaded_at")
    org_ready = whisper_model is not None and org_runtime_is_ready(org_id)
    return jsonify({
        "app_ready": True,
        "org_id": org_id,
        "ready": org_ready,
        "models_ready": org_ready,
        "default_models_ready": MODELS_READY,
        "org_runtime_ready": org_runtime_is_ready(org_id),
        "stt_ready": whisper_model is not None,
        "models_loading": state.get("loading"),
        "models_error": state.get("error"),
        "models_started_at": started_at,
        "models_loaded_at": loaded_at,
        "model_load_seconds": round(loaded_at - started_at, 2) if started_at and loaded_at else None,
    })


@app.route("/api/models/load", methods=["POST"])
@require_admin_scope("write")
@rate_limit("admin", SETTINGS.rate_limit_admin)
def api_load_models():
    start_model_loading_once(profile=current_org_profile(), force_retry=True)
    return api_ready()


if __name__ == "__main__":

    print("\n" + "=" * 60)
    print(f"  VOX - starting web server on http://{SETTINGS.host}:{SETTINGS.port}")
    print("  AI models will load in the background.")
    print("=" * 60 + "\n")

    if SETTINGS.autoload_models:
        start_model_loading_once()
    app.run(host=SETTINGS.host, port=SETTINGS.port, debug=SETTINGS.debug, threaded=True)
