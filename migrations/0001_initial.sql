PRAGMA foreign_keys = ON;

CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

INSERT INTO categories (id, name, slug, is_builtin, is_active, created_at, updated_at)
VALUES
    ('builtin-tech', '科技', 'technology', 1, 1, 0, 0),
    ('builtin-business', '商业', 'business', 1, 1, 0, 0),
    ('builtin-policy', '政策', 'policy', 1, 1, 0, 0),
    ('builtin-science', '科学', 'science', 1, 1, 0, 0);

CREATE TABLE rss_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL CHECK (language IN ('auto', 'en', 'zh', 'mixed')),
    category_id TEXT NOT NULL REFERENCES categories(id),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    time_offset_minutes INTEGER NOT NULL DEFAULT 0,
    feed_title TEXT,
    feed_link TEXT,
    feed_description TEXT,
    feed_language TEXT,
    feed_author TEXT,
    feed_updated_at TEXT,
    source_type TEXT NOT NULL DEFAULT 'rss',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_rss_sources_category ON rss_sources(category_id);
CREATE INDEX idx_rss_sources_enabled ON rss_sources(enabled);

CREATE TABLE source_health_checks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES rss_sources(id) ON DELETE CASCADE,
    checked_at INTEGER NOT NULL,
    http_status INTEGER,
    latency_ms INTEGER,
    parse_success INTEGER NOT NULL CHECK (parse_success IN (0, 1)),
    entry_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT
);

CREATE INDEX idx_health_source_time ON source_health_checks(source_id, checked_at DESC);

CREATE TABLE collection_runs (
    id TEXT PRIMARY KEY,
    source_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    requested_at INTEGER NOT NULL,
    completed_at INTEGER,
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES rss_sources(id),
    external_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(source_id, external_id)
);

CREATE INDEX idx_messages_source ON messages(source_id, updated_at DESC);

CREATE TABLE message_versions (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    collected_at INTEGER NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('auto', 'en', 'zh', 'mixed')),
    content_hash TEXT NOT NULL,
    UNIQUE(message_id, version_number),
    UNIQUE(message_id, content_hash)
);

CREATE INDEX idx_message_versions_collected ON message_versions(collected_at DESC);

CREATE TABLE translations (
    id TEXT PRIMARY KEY,
    message_version_id TEXT NOT NULL REFERENCES message_versions(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    title TEXT,
    summary TEXT,
    content TEXT,
    provider_id TEXT,
    key_ref TEXT,
    model TEXT,
    prompt_version TEXT NOT NULL,
    task_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(message_version_id, prompt_version, model)
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN ('collect_source', 'translate_message')),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER,
    input_version_id TEXT,
    output_version_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);

CREATE TABLE llm_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    priority INTEGER NOT NULL DEFAULT 100,
    timeout_seconds REAL NOT NULL DEFAULT 60,
    session_header_name TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE llm_provider_keys (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
    key_ref TEXT NOT NULL UNIQUE,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    cooldown_until INTEGER,
    last_status TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_llm_keys_provider ON llm_provider_keys(provider_id, enabled, priority);

CREATE TABLE llm_calls (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    provider_id TEXT NOT NULL,
    key_ref TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    token_usage_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    error_code TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX idx_llm_calls_task ON llm_calls(task_id, created_at DESC);
