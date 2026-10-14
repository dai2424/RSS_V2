-- 合并建议（阶段 3 M7–M8）：
-- 词表分批交给模型找疑似同指组，结果落库供人工确认；采纳复用既有合并流程，
-- 只有"确认过"和"不再想看"两种处置，因此状态只有 pending/accepted/dismissed。
CREATE TABLE keyword_suggestions (
    id TEXT PRIMARY KEY,
    members_json TEXT NOT NULL,       -- 组内写法：[{"text","kind","key"}]，key 是规范词键
    target_key TEXT NOT NULL,         -- 建议保留的规范词键，必是成员之一
    target_text TEXT NOT NULL,        -- 目标词的展示写法
    rationale TEXT NOT NULL DEFAULT '',  -- 模型给出的理由，只作展示
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','dismissed')),
    batch_offset INTEGER NOT NULL,    -- 生成时在词表里的起始偏移
    batch_fingerprint TEXT NOT NULL,  -- 该批词表的指纹，词表变了就重新开始
    fingerprint TEXT NOT NULL UNIQUE, -- 成员集合指纹：同一组不会被重复建议
    merge_id TEXT,                    -- 采纳后关联的合并记录
    created_at INTEGER NOT NULL,      -- 创建时间，UTC 秒
    resolved_at INTEGER               -- 处理时间，UTC 秒；待处理为空
);
CREATE INDEX idx_keyword_suggestions_status ON keyword_suggestions(status, created_at DESC);

-- 批次台账：同一批（同一词表指纹 + 同一偏移）不重复调用模型，避免重复付费。
CREATE TABLE keyword_suggestion_batches (
    batch_fingerprint TEXT NOT NULL,
    batch_offset INTEGER NOT NULL,
    groups INTEGER NOT NULL,         -- 本批落库的建议组数
    dropped INTEGER NOT NULL,        -- 本批被丢弃的组数
    created_at INTEGER NOT NULL,     -- 创建时间，UTC 秒
    PRIMARY KEY (batch_fingerprint, batch_offset)
);

-- 合并建议的提示词：只用于词表页的按钮，不参与来源/分类的任务分配
-- （TASK_SPECS 里标了 assignable=False，worker 也不认领这个类型）。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-merge-suggest','keyword_merge_suggest','merge-suggest',1,'合并建议（内置）','active','',
'你在维护一份检索关键词词表。下面是一批正在使用的写法，每行一个，括号里是类型。
请找出其中**指同一事物**的写法，组成建议组，供人工确认后再合并。

只处理同指，不处理上下级关系：
- 可以建议：同一家公司、产品或概念的几种写法。
- 不要建议：大概念与其下属型号（粒度不同），或两个不同主体的词。
- 拿不准就不要提，宁少勿多。

每组给出：
terms 是组内全部写法，2 至 5 个，必须逐字照抄下面的行，不要改写、不要翻译；
target 是建议保留的写法，必须是 terms 之一；
rationale 是一句话说明为什么认为是同一个事物。

必须只返回 JSON 对象，字段严格为 groups，不要 Markdown 包裹：
{"groups":[{"terms":["写法A","写法B"],"target":"写法A","rationale":"同一产品的中英文写法"}]}
没有同指写法时返回 {"groups":[]}。

词表：
{{vocabulary}}',
'词表太长不适合人工逐个比对：由模型给候选组，人工只做确认，合并仍走既有流程',
       strftime('%s','now'),strftime('%s','now'));
