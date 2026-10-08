-- API Key 改为界面直接录入：密钥值保存在运行目录数据库，不再使用环境变量引用。
-- 旧引用行只保存环境变量名，没有可兑换的密钥值，因此不迁移数据；升级后在界面重新填写。
CREATE TABLE llm_provider_keys_new (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
    secret TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    cooldown_until INTEGER,
    last_status TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

DROP TABLE llm_provider_keys;
ALTER TABLE llm_provider_keys_new RENAME TO llm_provider_keys;
CREATE INDEX idx_llm_keys_provider ON llm_provider_keys(provider_id, enabled, priority);

-- 审计与译文只记录密钥掩码标签，列名与语义保持一致。
ALTER TABLE llm_calls RENAME COLUMN key_ref TO key_masked;
ALTER TABLE translations RENAME COLUMN key_ref TO key_masked;
