-- 租约 token 防止旧 worker 覆盖重新领取的任务，重试时间避免忙循环。
ALTER TABLE tasks ADD COLUMN lease_token TEXT;
ALTER TABLE tasks ADD COLUMN available_at INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);
CREATE TABLE collection_results (
    run_id TEXT NOT NULL REFERENCES collection_runs(id),
    source_id TEXT NOT NULL REFERENCES rss_sources(id),
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id, source_id)
);
-- 使用 UUID 保留既有内置分类引用，升级旧的开发数据库。
INSERT INTO categories SELECT
    CASE id
      WHEN 'builtin-tech' THEN '8cabd1f6-c0c3-4b32-863d-3836cf5a8171'
      WHEN 'builtin-business' THEN 'c00cc3b8-0778-4a85-9857-85eaa7034c8a'
      WHEN 'builtin-policy' THEN 'ffbfbd14-19ea-4724-afdc-868f0c98d878'
      ELSE '61689ce2-6d17-4422-8c19-97b43b488d70' END,
    name, slug || '-uuid', is_builtin, is_active, created_at, updated_at
FROM categories WHERE id LIKE 'builtin-%';
UPDATE rss_sources SET category_id = CASE category_id
    WHEN 'builtin-tech' THEN '8cabd1f6-c0c3-4b32-863d-3836cf5a8171'
    WHEN 'builtin-business' THEN 'c00cc3b8-0778-4a85-9857-85eaa7034c8a'
    WHEN 'builtin-policy' THEN 'ffbfbd14-19ea-4724-afdc-868f0c98d878'
    WHEN 'builtin-science' THEN '61689ce2-6d17-4422-8c19-97b43b488d70'
    ELSE category_id END;
DELETE FROM categories WHERE id LIKE 'builtin-%';
UPDATE categories SET slug = substr(slug, 1, length(slug)-5) WHERE slug LIKE '%-uuid';
