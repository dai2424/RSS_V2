-- 新增内容加工任务类型：SQLite 不能修改 CHECK 约束，只能按既有结构重建 tasks 表。
CREATE TABLE tasks_enrichment (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN ('collect_source', 'translate_message', 'enrich_message')),
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
    updated_at INTEGER NOT NULL,
    lease_token TEXT,
    available_at INTEGER NOT NULL DEFAULT 0
);

INSERT INTO tasks_enrichment
SELECT id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,
       payload_json,error_code,error_message,created_at,updated_at,lease_token,available_at
FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_enrichment RENAME TO tasks;
CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);

-- 内容加工结果：精简标题、中文摘要和检索关键词，按提示词版本与模型各存一份，
-- 结构对齐 translations，唯一的差别是关键词用 JSON 数组列保存。
CREATE TABLE message_enrichments (
    id TEXT PRIMARY KEY,
    message_version_id TEXT NOT NULL REFERENCES message_versions(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    title TEXT,
    summary TEXT,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    provider_id TEXT,
    key_masked TEXT,
    model TEXT,
    prompt_version TEXT NOT NULL,
    task_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(message_version_id, prompt_version, model)
);
