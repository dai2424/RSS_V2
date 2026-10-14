-- 线索（阶段 4 L1）：把"同一条线索的几条消息"变成可命名、可复用的实体。
-- 成员精确到消息版本：加入时锁定当时的最新版本；消息之后出现新版本不改写线索成员，
-- 也不改写已经生成的草稿（草稿的引用指向生成那一刻的版本）。
-- 成员随消息版本级联删除：消息被删掉后它不该继续留在线索里。
CREATE TABLE leads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,           -- 人工命名的线索名，用于列表与草稿标题默认值
    note TEXT,                     -- 可选备注，只作展示
    created_at INTEGER NOT NULL,   -- 创建时间，UTC 秒
    updated_at INTEGER NOT NULL    -- 最近一次成员变化时间，UTC 秒
);

CREATE TABLE lead_messages (
    lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    message_version_id TEXT NOT NULL REFERENCES message_versions(id) ON DELETE CASCADE,
    added_at INTEGER NOT NULL,     -- 加入时间，UTC 秒
    PRIMARY KEY (lead_id, message_version_id)
);
CREATE INDEX idx_lead_messages_version ON lead_messages(message_version_id);
