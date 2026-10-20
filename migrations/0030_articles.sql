-- 阶段 4 全文生成（P5）：
-- 按"已确认的提纲版本"生成可阅读的全文，引用与来源都随版本落快照，
-- 历史成稿的依据不随资料包重跑或清理而改变。
-- 设计要点：
--   * 只追加版本：模型生成写 generated+draft；P6a 的人工编辑另存 edited+saved
--     （base_article_version_id 指向被改版本），旧行不改写；
--   * outline_version_id 钉住生成时用的提纲版本（不含外键：提纲版本可以被清理，
--     成稿的历史依据不能被级联删）；
--   * body_json 是权威结构（分节段落 + 引用快照 + 来源列表），body_markdown 由它派生；
--   * recheck_fact_ids_json / saved_at / base_article_version_id 是 P6a 要用的列：
--     契约约定 P6a 只用不改结构，所以这里一次建全；
--   * 人工版本的 prompt_version/model/task_id 留空，提示词用量只统计模型产物。
-- SQLite 不能修改 CHECK 约束，tasks 表按 0009/0019/0025/0026/0027/0029 的方式重建。
CREATE TABLE tasks_articles (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN (
        'collect_source', 'translate_message', 'enrich_message',
        'keyword_merge_suggest', 'discover_events', 'prepare_materials', 'digest_materials',
        'generate_outline', 'generate_article'
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

INSERT INTO tasks_articles
SELECT id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,
       payload_json,error_code,error_message,created_at,updated_at,lease_token,available_at
FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_articles RENAME TO tasks;
CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);

-- 全文版本：正文结构、引用快照与来源列表；只追加，不改写历史行。
CREATE TABLE article_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    outline_version_id TEXT NOT NULL,      -- 生成时使用的已确认提纲版本
    version_number INTEGER NOT NULL,       -- 项目内递增序号；"最新"按它判定
    kind TEXT NOT NULL CHECK (kind IN ('generated', 'edited')),
    status TEXT NOT NULL CHECK (status IN ('draft', 'saved')),
    title TEXT NOT NULL,
    lead TEXT NOT NULL DEFAULT '',         -- 导语
    body_json TEXT NOT NULL DEFAULT '{}',  -- 分节段落 + 引用快照（权威结构）
    body_markdown TEXT NOT NULL DEFAULT '',-- 由 body_json 派生：复制与导出用
    source_list_json TEXT NOT NULL DEFAULT '[]',   -- 文末来源列表（服务端按正文推导）
    counters_json TEXT NOT NULL DEFAULT '{}',      -- 生成时被丢弃/降级的条目计数
    material_snapshot_hash TEXT NOT NULL DEFAULT '', -- 等于提纲版本的资料包快照哈希
    digest_run_id TEXT,                    -- 提纲绑定的资料整理版本（取事实/证据的入口）
    prompt_version TEXT,                   -- 模型生成时记录；人工版本为空
    model TEXT,                            -- 模型标识；人工版本为空
    task_id TEXT,                          -- 产出这一版的生成任务；人工版本为空
    base_article_version_id TEXT REFERENCES article_versions(id) ON DELETE SET NULL,
    recheck_fact_ids_json TEXT NOT NULL DEFAULT '[]', -- P6a：人工改过引用后需重新核对的事实
    created_at INTEGER NOT NULL,
    saved_at INTEGER                       -- P6a 人工保存时间；生成稿为空
);
CREATE INDEX idx_article_versions_project ON article_versions(project_id, version_number DESC);
-- 同快照的语义唯一由服务层按幂等键保证（用户主动重生成必须能新起版本，因此不建唯一索引）。
CREATE INDEX idx_article_versions_outline ON article_versions(
    project_id, outline_version_id, material_snapshot_hash, prompt_version, model
);

-- 全文生成的提示词：输入是已确认提纲、已核对事实与未处理缺口，输出是分节正文。
-- 模板必须出现字面量 sections（编译校验要求声明结构化输出字段）。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-article-v1','generate_article','article',1,'全文写作（内置）','active','',
'你在为一位普通科技读者写一篇科技博客文章的正文。下面是已确认的提纲、已经核对过的事实（含分析与推测），以及尚未处理的缺口与分歧。

选题：{{title}}
{{summary}}
切入角度：{{outline}}

可用事实（正文里的判断只能依据这里出现的内容；带"没有可用引文"的事实不要作为依据）：
{{facts}}

尚未处理的缺口与分歧（存在影响主线的缺口时，不要用流畅的表述掩盖它）：
{{notes}}

请输出一篇完整的文章：
1. title：文章标题。具体、不夸张，与事件本身一致。
2. lead：导语，一段话，说清这篇讲什么、为什么值得读。
3. sections：分节正文，至少 1 节。每节包含：
   - heading：小节标题；
   - paragraphs：段落数组，每段包含：
     * text：这一段正文；
     * kind：段落类型，只能是 fact（客观事实）、analysis（分析）或 speculation（推测）。
       kind=fact 的段落必须带至少一个引用标记 [^n]；没有依据的判断写成 analysis；
       推测写成 speculation，并且必须使用"可能""或许""预计"等不确定表述。
4. citations：引用表，每项包含：
   - marker：与正文里的 [^n] 对应的编号（字符串，如 "1"）；
   - fact_id：上面出现过的事实 id；
   - evidence_ids：该事实的引文 id 数组（不确定就留空数组，服务端会按事实补全）。
   一个标记只在正文里使用一次，编号从 1 开始连续。
5. source_list：来源候选，每项包含 material_id（正文用到的资料 id；不确定就留空数组，
   服务端会按正文引用推导）。title/publisher/url 可以不填，服务端只采信 material_id。

要求：
- 按普通读者的理解顺序组织：先讲清发生了什么，再补必要背景与术语，最后谈行业影响。
- 长度按材料与事件复杂度决定：不堆叠报道摘要、不为凑字数扩写；资料支撑不了的内容不要写进 fact 段。
- 事实、分析、推测要区分清楚，不能把推测写成确定性结论；单一来源时不要暗示多方共识。
- 链接只使用 http/https 地址。中文输出。

只返回 JSON 对象，字段严格为 title、lead、sections、citations、source_list，不要 Markdown 包裹。',
'内置提示词：阶段 4 全文生成的默认版本',strftime('%s','now'),strftime('%s','now'));
