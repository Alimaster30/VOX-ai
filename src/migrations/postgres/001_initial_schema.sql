CREATE TABLE IF NOT EXISTS organizations (
    org_id TEXT PRIMARY KEY,
    organization_name TEXT NOT NULL,
    assistant_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    profile_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    org_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result_json TEXT,
    error TEXT,
    metadata_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    query_count INTEGER NOT NULL,
    layer_counts_json TEXT NOT NULL,
    history_json TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, org_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TEXT NOT NULL,
    request_id TEXT,
    call_id TEXT,
    org_id TEXT,
    event_type TEXT NOT NULL,
    method TEXT,
    path TEXT,
    status INTEGER,
    elapsed_ms INTEGER,
    remote_addr TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS admin_tokens (
    token_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    org_id TEXT,
    scopes_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS handoff_tickets (
    ticket_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    session_id TEXT,
    call_id TEXT,
    request_id TEXT,
    query TEXT NOT NULL,
    response TEXT,
    intent TEXT,
    confidence DOUBLE PRECISION NOT NULL,
    layer INTEGER NOT NULL,
    language TEXT,
    status TEXT NOT NULL,
    department TEXT,
    contact_json TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS answer_cache (
    cache_key TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    language TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    response_json TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
