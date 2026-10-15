-- 阶段 4 事件选题发现（P2a）：
-- 一次"发现选题"= 一个批次任务：按固定时间窗口（默认最近 7 天）和可选分类/来源取消息，
-- 由模型按事件分组，服务端校验成员、合并跨批候选后落成候选。
-- 候选的事件成员复用 leads（消息版本集合），因此这里只增加 kind 区分人工与自动；
-- 人工线索页与旧 API 默认只看 'manual'，行为不变。
-- SQLite 不能修改 CHECK 约束，tasks 表按 0009/0019 的方式重建以加入新任务类型。
CREATE TABLE tasks_discovery (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN (
        'collect_source', 'translate_message', 'enrich_message',
        'keyword_merge_suggest', 'discover_events'
    )),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER,
    input_version_id TEXT,
    output_version_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    lease_token TEXT,
    available_at INTEGER NOT NULL DEFAULT 0
);

INSERT INTO tasks_discovery
SELECT id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,
       payload_json,error_code,error_message,created_at,updated_at,lease_token,available_at
FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_discovery RENAME TO tasks;
CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);

-- 线索来源：manual 人工创建（默认，既有行为）、discovery 由选题发现自动建立。
ALTER TABLE leads ADD COLUMN kind TEXT NOT NULL DEFAULT 'manual'
    CHECK (kind IN ('manual', 'discovery'));
CREATE INDEX idx_leads_kind ON leads(kind, updated_at DESC);

-- 发现批次：输入范围与输入快照。input_fingerprint 是窗口内 (消息, 版本) 有序列表的
-- 指纹，用于"同样输入不重复付费"；幂等键再带上范围哈希，因此换筛选条件是另一批。
CREATE TABLE discovery_batches (
    id TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,      -- 窗口起点，UTC 秒，含
    window_end INTEGER NOT NULL,        -- 窗口终点，UTC 秒，不含
    category_id TEXT,                   -- 限定分类；为空表示全部分类
    source_id TEXT,                     -- 限定来源；为空表示全部来源
    input_fingerprint TEXT NOT NULL,    -- 窗口内输入版本快照指纹
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    task_id TEXT,                       -- 最近一次执行的任务；任务被清理后保留批次
    source_label TEXT NOT NULL DEFAULT '',   -- 展示用：来源或分类名，避免看时间戳猜范围
    message_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    duplicate_members INTEGER NOT NULL DEFAULT 0,  -- 同一消息被模型放进多个候选的次数
    out_of_range INTEGER NOT NULL DEFAULT 0,       -- 模型给出但不在本批输入里的成员数
    dropped_members INTEGER NOT NULL DEFAULT 0,    -- 超出成员上限被丢弃的成员数
    dropped_candidates INTEGER NOT NULL DEFAULT 0, -- 超出候选上限被丢弃的候选数
    error_code TEXT,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_discovery_batches_status ON discovery_batches(status, created_at DESC);

-- 候选：同一事件的一组消息 + 推荐理由。成员固定在 lead_messages，
-- 这里只存"事件"层面的展示与排序信息；lead 删除时候选随之删除。
CREATE TABLE discovery_candidates (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES discovery_batches(id) ON DELETE CASCADE,
    lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,              -- 本批次内的展示顺序，从 1 开始
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    event_time INTEGER,                 -- 事件时间（成员最早发布时间），UTC 秒；无法判定为空
    score INTEGER NOT NULL,             -- 模型自评的推荐度 1–5，只用于排序
    reasons_json TEXT NOT NULL DEFAULT '[]',  -- [{"kind":"interesting|important","text":"..."}]
    member_count INTEGER NOT NULL DEFAULT 0,
    dropped_members INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_discovery_candidates_batch ON discovery_candidates(batch_id, rank);
CREATE INDEX idx_discovery_candidates_lead ON discovery_candidates(lead_id);

-- 写作项目：用户选定候选后的写作上下文。一个候选只建一个项目（唯一约束保证重复选择幂等），
-- 后续资料、事实、提纲与文章版本都以 project_id 归属（阶段 4 后续任务各自新增表）。
CREATE TABLE writing_projects (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES discovery_candidates(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'materials_pending', 'materials_ready', 'outline_ready',
        'outline_confirmed', 'article_ready', 'saved'
    )),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_writing_projects_candidate ON writing_projects(candidate_id)
    WHERE candidate_id IS NOT NULL;
CREATE INDEX idx_writing_projects_status ON writing_projects(status, updated_at DESC);

-- 选题发现的提示词：输入是窗口内一批消息，输出是候选事件分组与推荐理由。
-- 不是按来源、按消息跑的任务，因此不参与任务分配（assignable=False）。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-discovery-v1','discover_events','discovery',1,'选题发现（内置）','active','',
'你是科技资讯编辑，负责从一批订阅消息里挑出"值得写一篇科技博客文章"的事件。

下面是时间窗口 {{window_start}} 到 {{window_end}}（UTC 秒）内的第 {{batch_index}}/{{batch_total}} 批消息，范围：{{scope}}，本批 {{message_count}} 条。
每行格式为：[消息ID] 时间 | 来源 | 标题 | 摘要 | 关键词（无加工结果的消息没有关键词）。

{{messages}}

请把描述**同一件事**的消息合并成一个候选事件，然后给出候选。判定同一事件的条件：同一对象 + 同一动作或状态变化 + 同一事件时间窗（发布、评测、官方回应、用户反馈可以共同支持同一事件）。仅共享公司、产品或关键词不算同一事件；同一公司不同产品的发布要分开。

要求：
- member_message_ids：只能使用上面出现过的消息ID，逐字照抄；一条消息只能属于一个候选。
- 单条消息也可以单独成为候选（有趣或重要的单篇报道）。
- title：一句话说明这件事，25 字以内，不要标题党。
- summary：2–3 句，说清发生了什么、为什么值得写。
- event_time：该事件的时间，取成员消息里最早的时间（UTC 秒）。
- score：1–5 的推荐度，5 最值得写。
- reasons：1–3 条推荐理由，kind 取 interesting（有趣）或 important（重要），text 说明新意、故事性或影响；不要写"值得关注"这类空话。

只返回 JSON 对象，字段严格为 candidates（数组，元素含 member_message_ids、title、summary、event_time、score、reasons），不要 Markdown 包裹；没有值得写的就返回空数组。',
'内置提示词：阶段 4 选题发现的默认版本',strftime('%s','now'),strftime('%s','now'));
