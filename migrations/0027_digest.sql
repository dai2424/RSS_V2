-- 阶段 4 资料整理与核对（P3b）：
-- 把已读资料整理成"关键事实 + 支持证据 + 分歧 + 缺口"，供用户逐项核对后再进提纲。
-- 设计要点：
--   * 证据由"模型给出的引文"落库：模型只能引用本项目已读资料的原文片段，服务端校验
--     引文确实出现在那份资料里（内容支持关系的第一次校验，不只看 ID 是否合法）；
--   * 每次整理是一个"资料包版本"（digest_runs）：记录输入资料指纹与计数，事实/证据/
--     缺口都带 run_id，重跑新增版本而不改写历史；
--   * 缺口/分歧（project_notes）可以人工标记已处理，重跑时保留已处理的记录。
-- SQLite 不能修改 CHECK 约束，tasks 表按 0009/0019/0025/0026 的方式重建。
CREATE TABLE tasks_digest (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN (
        'collect_source', 'translate_message', 'enrich_message',
        'keyword_merge_suggest', 'discover_events', 'prepare_materials', 'digest_materials'
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

INSERT INTO tasks_digest
SELECT id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,
       payload_json,error_code,error_message,created_at,updated_at,lease_token,available_at
FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_digest RENAME TO tasks;
CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);

-- 资料包版本：一次资料整理的输入指纹与计数；历史版本保留，不被后续版本覆盖。
CREATE TABLE digest_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    materials_hash TEXT NOT NULL,        -- 输入资料（read/summary_only）的 (id, 指纹) 快照
    task_id TEXT,
    fact_count INTEGER NOT NULL DEFAULT 0,
    analysis_count INTEGER NOT NULL DEFAULT 0,
    speculation_count INTEGER NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    gap_count INTEGER NOT NULL DEFAULT 0,
    blocking_count INTEGER NOT NULL DEFAULT 0,
    rejected_quotes INTEGER NOT NULL DEFAULT 0,  -- 引文在资料里找不到、被丢弃的条数
    out_of_range INTEGER NOT NULL DEFAULT 0,     -- 引用了不属于本项目资料的条数
    notes_json TEXT NOT NULL DEFAULT '[]',       -- 整理结束时记录的缺口说明
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_digest_runs_project ON digest_runs(project_id, created_at DESC);

-- 支持证据：一段引文 + 它来自哪份资料；locator 记录引文在正文里的位置，便于跳转核对。
CREATE TABLE material_evidence (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES digest_runs(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    material_id TEXT NOT NULL REFERENCES project_materials(id) ON DELETE CASCADE,
    quote TEXT NOT NULL,
    locator INTEGER NOT NULL DEFAULT -1,  -- 引文在资料正文中的字符偏移；-1 表示未定位
    quote_hash TEXT NOT NULL DEFAULT '',  -- 引文的规范化指纹，用于跨版本比对
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_material_evidence_material ON material_evidence(material_id);

-- 关键事实：事实 / 分析 / 推测分开标记；状态表示来源支持情况。
CREATE TABLE project_facts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES digest_runs(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'analysis', 'speculation')),
    statement TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('supported', 'conflicting', 'missing')),
    conflict_note TEXT NOT NULL DEFAULT '',
    conflict_refs_json TEXT NOT NULL DEFAULT '[]',  -- 分歧涉及的其他事实（同一 run 内）
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_project_facts_run ON project_facts(run_id, created_at);

-- 事实—证据关系：一条事实可以由多条引文支持（同一份资料的不同片段或不同资料）。
CREATE TABLE fact_evidence (
    fact_id TEXT NOT NULL REFERENCES project_facts(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES material_evidence(id) ON DELETE CASCADE,
    PRIMARY KEY (fact_id, evidence_id)
);

-- 缺口与分歧待办：blocking 表示影响主线、未处理前不应该进提纲；人工可标记已处理。
CREATE TABLE project_notes (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES digest_runs(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('gap', 'conflict', 'note')),
    text TEXT NOT NULL,
    fact_id TEXT REFERENCES project_facts(id) ON DELETE SET NULL,
    blocking INTEGER NOT NULL DEFAULT 0 CHECK (blocking IN (0, 1)),
    resolved_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_project_notes_project ON project_notes(project_id, resolved_at, created_at);

-- 写作项目：资料整理的结果摘要（最近一次 run、计数、是否有未解决的阻塞缺口）。
ALTER TABLE writing_projects ADD COLUMN digest_summary_json TEXT NOT NULL DEFAULT '{}';

-- 资料整理的提示词：输入是选题与已读资料的正文片段，输出是事实/分歧/缺口与引文。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-digest-v1','digest_materials','digest',1,'资料整理（内置）','active','',
'你在为一篇科技博客整理资料。下面是选题与已经读到的资料（每条资料前有材料 ID 与来源信息）。

选题：{{title}}
{{summary}}

已读资料（只能引用这里出现的内容）：
{{materials}}

请输出三类内容：
1. facts：支撑这篇报道的关键事实。每条包含：
   - statement：一句话陈述（事实要能被引文直接支持）；
   - kind：fact（客观事实）/ analysis（基于事实的行业分析）/ speculation（推测，必须用"可能/或许"等不确定表达）；
   - supports：支持它的引文列表，每条包含 material_id（必须是上面出现过的材料 ID）与 quote（从该资料正文里**原样摘录**的一句或一小段，20–200 字，不要改写、不要跨段拼接）。
   事实必须有引文；分析与推测也要给出依据（可以引用事实所依据的同一段资料）。
2. conflicts：资料之间的分歧或互相矛盾之处（例如厂商说法与测试结果不一致）。每条包含 text 与 statement_indexes（涉及第几条 facts，从 0 开始）。
3. gaps：关键信息缺口（例如缺少官方一手资料、缺少第三方检测结论）。每条包含 text 与 blocking（true 表示不解决就不应该动笔写主线）。

要求：
- 只使用上面资料里的信息，不要引入模型记忆里的数字、引语或时间。
- 找不到引文支持的内容不要写进 facts：宁可少写，也不要编造。
- 同一件事在资料里重复出现时只写一条，把不同来源的引文都放进 supports。
- 中文输出。

只返回 JSON 对象，字段严格为 facts、conflicts、gaps，不要 Markdown 包裹。',
'内置提示词：阶段 4 资料整理的默认版本',strftime('%s','now'),strftime('%s','now'));
