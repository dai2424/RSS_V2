-- 同样内容以后重新出现时仍应保存新版本，不能由历史 hash 唯一约束阻止。
CREATE TEMP TABLE translations_backup AS SELECT * FROM translations;
CREATE TABLE message_versions_rebuilt (
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
    UNIQUE(message_id, version_number)
);


INSERT INTO message_versions_rebuilt SELECT * FROM message_versions;
DROP TABLE message_versions;
ALTER TABLE message_versions_rebuilt RENAME TO message_versions;
CREATE INDEX idx_message_versions_collected ON message_versions(collected_at DESC);
INSERT INTO translations SELECT * FROM translations_backup;
DROP TABLE translations_backup;
ALTER TABLE llm_providers ADD COLUMN extra_headers_json TEXT NOT NULL DEFAULT '{}';
