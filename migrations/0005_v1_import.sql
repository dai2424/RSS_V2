-- 数据导入独立执行；这里只保存旧标识到 v2 UUID 的映射。
CREATE TABLE v1_import_map (
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('category', 'source', 'message', 'version', 'health')),
    legacy_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY(entity_kind, legacy_id)
);
CREATE INDEX idx_v1_import_target ON v1_import_map(entity_kind, target_id);
