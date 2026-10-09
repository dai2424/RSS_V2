-- 关键词别名与可撤销的人工合并。
-- 合并只处理"同指"（OpenAI / openai / Open AI），不处理上下位（尊界 与 尊界V800），
-- 因为后者会抹平检索粒度。别名永远指向最终规范词（写入时压平），因此解析只需一跳。
CREATE TABLE keyword_merges (
    id TEXT PRIMARY KEY,
    target_norm TEXT NOT NULL,          -- 合并后的规范词键，不随后续合并变化，供审计
    target_raw TEXT NOT NULL,           -- 当时的展示写法
    members_json TEXT NOT NULL,         -- 被并入的写法快照，只用于展示，不参与查询
    affected_messages INTEGER NOT NULL, -- 合并时受影响的消息数
    created_at INTEGER NOT NULL,
    undone_at INTEGER                   -- 撤销时间；为空表示仍然生效
);

CREATE TABLE keyword_aliases (
    alias_norm TEXT PRIMARY KEY,        -- 一个写法只能指向一个规范词
    canonical_norm TEXT NOT NULL,       -- 始终是最终规范词
    merge_id TEXT,                      -- 产生当前指向的那次合并；撤销按它定位
    previous_canonical TEXT,            -- 本次被改写前的指向；本行由本次合并新建时为空
    previous_merge_id TEXT,             -- 改写前所属的合并；撤销时一并回填
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_keyword_aliases_canonical ON keyword_aliases(canonical_norm);
