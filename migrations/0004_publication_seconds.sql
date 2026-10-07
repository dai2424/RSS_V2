-- 发布时间统一为 UTC 秒；升级时保留已有译文，无法解析的旧时间保持未知。
CREATE TEMP TABLE translations_backup AS SELECT * FROM translations;
CREATE TABLE message_versions_seconds (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at INTEGER,
    collected_at INTEGER NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('auto', 'en', 'zh', 'mixed')),
    content_hash TEXT NOT NULL,
    UNIQUE(message_id, version_number)
);

INSERT INTO message_versions_seconds
SELECT id,message_id,version_number,title,summary,content,url,
    CASE WHEN typeof(published_at)='integer' THEN published_at
         ELSE CAST(strftime('%s',published_at) AS INTEGER) END,
    collected_at,language,content_hash
FROM message_versions;
DROP TABLE message_versions;
ALTER TABLE message_versions_seconds RENAME TO message_versions;
CREATE INDEX idx_message_versions_collected ON message_versions(collected_at DESC);
INSERT INTO translations SELECT * FROM translations_backup;
DROP TABLE translations_backup;
