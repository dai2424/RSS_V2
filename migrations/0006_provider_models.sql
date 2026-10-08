-- 供应商下增加可启停的模型列表；原单模型字段迁入新表后移除。
CREATE TABLE llm_provider_models (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    priority INTEGER NOT NULL DEFAULT 100,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(provider_id, model)
);

INSERT INTO llm_provider_models (id, provider_id, model, enabled, priority, created_at, updated_at)
SELECT id, id, model, enabled, priority, updated_at, updated_at
FROM llm_providers;

ALTER TABLE llm_providers DROP COLUMN model;
