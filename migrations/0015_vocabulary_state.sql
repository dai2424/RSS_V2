-- 词表版本（阶段 3 M4）：
-- 记录"模型这次看到的词表是哪一版"，用于解释同一版本、同一提示词前后两次加工结果为何不同。
-- vocabulary_state 只有一行：fingerprint 是最近一次注入文本的哈希，revision 在指纹变化时 +1。
-- 计数无法从事实数据推导（注入文本已被解析、别名表也记录不了"模型看到什么"），才允许落字段。
CREATE TABLE vocabulary_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fingerprint TEXT NOT NULL,        -- 最近一次注入文本的 SHA-256 前 16 位
    revision INTEGER NOT NULL,        -- 词表版本号，从 1 开始，内容变化时递增
    updated_at INTEGER NOT NULL       -- 最近推进时间，UTC 秒
);

-- 加工结果记录当时注入的词表版本；NULL 表示没有注入（关闭注入或旧数据）。
ALTER TABLE message_enrichments ADD COLUMN vocab_revision INTEGER;
