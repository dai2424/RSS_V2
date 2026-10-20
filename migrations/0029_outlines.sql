-- 阶段 4 提纲生成与确认（P4）：
-- 依据选题与"确定资料包版本"生成可编辑的分节提纲，用户显式确认某个版本后才具备生成全文的资格。
-- 设计要点：
--   * 提纲按版本追加（outline_versions）：模型生成写 draft，人工修改另存新的 draft（based_on_version
--     指向被改版本），确认把该版本置为 confirmed、旧的 confirmed 转 superseded——历史行不改写；
--   * material_snapshot_hash 等于生成时那个资料包版本的 digest_runs.materials_hash，
--     digest_run_id 再精确钉住是哪一个版本（同一批资料重新整理会产生新版本、事实不同）；
--   * 人工版本的 prompt_version/model/task_id 留空：提示词用量按 prompt_version 计数，
--     留空才不会把人工编辑算成模型产物。
-- SQLite 不能修改 CHECK 约束，tasks 表按 0009/0019/0025/0026/0027 的方式重建。
CREATE TABLE tasks_outlines (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN (
        'collect_source', 'translate_message', 'enrich_message',
        'keyword_merge_suggest', 'discover_events', 'prepare_materials', 'digest_materials',
        'generate_outline'
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

INSERT INTO tasks_outlines
SELECT id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,
       payload_json,error_code,error_message,created_at,updated_at,lease_token,available_at
FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_outlines RENAME TO tasks;
CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);

-- 提纲版本：标题、切入点与分节结构；只追加，不改写历史行。
CREATE TABLE outline_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,       -- 项目内递增序号；"最新"按它判定
    status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed', 'superseded')),
    source TEXT NOT NULL CHECK (source IN ('model', 'human')),
    title TEXT NOT NULL,
    angle TEXT NOT NULL DEFAULT '',        -- 切入点：从什么角度写、为什么值得读
    sections_json TEXT NOT NULL DEFAULT '[]',  -- 分节：小标题、要点、依据事实与引文、影响分析
    material_snapshot_hash TEXT NOT NULL DEFAULT '',  -- 生成时资料包版本的 materials_hash
    counters_json TEXT NOT NULL DEFAULT '{}',  -- 生成时被丢弃的引用与结构计数；人工版本为零值
    digest_run_id TEXT,                    -- 生成时用的资料整理版本（P5 据此取事实与证据）
    prompt_version TEXT,                   -- 模型生成时记录；人工版本为空
    model TEXT,                            -- 模型标识；人工版本为空
    task_id TEXT,                          -- 产出这一版的生成任务；人工版本为空
    based_on_version TEXT REFERENCES outline_versions(id) ON DELETE SET NULL,  -- 人工版本的基准
    created_at INTEGER NOT NULL,
    confirmed_at INTEGER                   -- 确认时间；未确认为空
);
CREATE INDEX idx_outline_versions_project ON outline_versions(project_id, version_number DESC);
-- 同快照的语义唯一由服务层按幂等键保证（用户主动重生成必须能新起版本，因此不建唯一索引）。
CREATE INDEX idx_outline_versions_snapshot ON outline_versions(
    project_id, material_snapshot_hash, prompt_version, model
);

-- 提纲生成的提示词：输入是选题、已核对的事实与未处理的缺口，输出是分节提纲。
-- 模板必须出现字面量 sections（编译校验要求声明结构化输出字段）。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-outline-v1','generate_outline','outline',1,'提纲规划（内置）','active','',
'你在为一位普通科技读者规划一篇科技博客的提纲。下面是选题、已经核对过的事实（含分析与推测）以及尚未处理的缺口与分歧。

选题：{{title}}
{{summary}}

可用事实（talking_points 里的判断只能依据这里出现的内容）：
{{facts}}

尚未处理的缺口与分歧（存在影响主线的缺口时，不要用流畅的表述掩盖它）：
{{notes}}

请输出一份提纲：
1. title：文章标题。具体、不夸张，与事件本身一致。
2. angle：切入点。说明这篇从什么角度讲、为什么值得普通读者读。
3. sections：分节，至少 2 节。每节包含：
   - heading：小节标题；
   - talking_points：这一节要讲清楚的内容，每条一句话；
   - fact_ids：这一节依据的事实 id（必须是上面出现过的 fact id；没有依据就留空数组，不要编造 id）；
   - evidence_ids：依据的引文 id（形如「引文 [id]」里的 id，只能取自本节所选事实自己的引文；没有就留空数组）；
   - impact_notes：这一节的行业影响分析要点，可以留空数组。

要求：
- 按普通读者的理解顺序组织：先讲清发生了什么，再补必要背景与术语，最后谈行业影响。
- 资料支撑不了的内容不要写进 talking_points，也不要为它挂 fact id。
- 中文输出。

只返回 JSON 对象，字段严格为 title、angle、sections，不要 Markdown 包裹。',
'内置提示词：阶段 4 提纲生成的默认版本',strftime('%s','now'),strftime('%s','now'));
