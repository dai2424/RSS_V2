-- 阶段 4 资料准备（P3a）：
-- 一个写作项目 = 一个资料准备任务：把选题与成员渲染成检索式（一次模型调用），
-- 读取 RSS 原文与联网补充的网页，去重后按证据快照落库。
-- 资料行是写作的输入证据：读取失败的候选也落行（status=failed + 错误码），
-- 这样"没读到什么、为什么没读到"看得见，不会静默丢材料。
-- SQLite 不能修改 CHECK 约束，tasks 表按 0009/0019/0025 的方式重建以加入新任务类型。
CREATE TABLE tasks_materials (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK (task_type IN (
        'collect_source', 'translate_message', 'enrich_message',
        'keyword_merge_suggest', 'discover_events', 'prepare_materials'
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

INSERT INTO tasks_materials
SELECT id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,
       payload_json,error_code,error_message,created_at,updated_at,lease_token,available_at
FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_materials RENAME TO tasks;
CREATE INDEX idx_tasks_claim ON tasks(status, lease_until, created_at);
CREATE INDEX idx_tasks_available ON tasks(status, available_at, created_at);

-- 写作项目：资料准备的结果摘要（检索式、各状态条数、缺口说明），
-- 界面直接读它显示"上一轮跑到哪、缺什么"；权威数据仍以 project_materials 为准。
ALTER TABLE writing_projects ADD COLUMN materials_summary_json TEXT NOT NULL DEFAULT '{}';

-- 资料快照：RSS 资料固定消息版本（message_version_id），外部资料保存 URL 与获取现场。
-- content_hash 是规范化正文的指纹，同一项目内唯一（部分索引避开失败行的空指纹）。
CREATE TABLE project_materials (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('rss', 'web')),
    status TEXT NOT NULL CHECK (status IN ('read', 'summary_only', 'unavailable', 'failed')),
    url TEXT NOT NULL DEFAULT '',
    final_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    published_at INTEGER,               -- 原始发布时间，UTC 秒；历史背景可早于发现窗口
    fetched_at INTEGER NOT NULL,        -- 获取时间，UTC 秒
    content_text TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    byte_size INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    message_version_id TEXT REFERENCES message_versions(id) ON DELETE SET NULL,
    task_id TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_project_materials_hash
    ON project_materials(project_id, content_hash) WHERE content_hash <> '';
CREATE INDEX idx_project_materials_project ON project_materials(project_id, created_at);
CREATE INDEX idx_project_materials_status ON project_materials(project_id, status);

-- 资料检索式的提示词：输入是选题与成员报道，输出是几条用于找资料的检索式。
-- 不是按来源、按消息跑的任务，因此不参与任务分配（assignable=False）。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-materials-v1','prepare_materials','materials',1,'资料检索式（内置）','active','',
'你是科技资讯编辑，正在为下面这个选题准备写作用的资料。

选题：{{title}}
{{summary}}

已经订阅到的报道（按时间从早到晚）：
{{members}}

请给出用于检索补充资料的检索式，最多 3 条，覆盖三类用途：
1. 中文关键词检索（用事件关键词，不要整句照抄标题）；
2. 英文检索（同一个事件的英文报道或官方发布）；
3. 限定官方来源的检索（用 site: 加厂商/机构域名，例如公司官网、官方博客、监管机构）。

要求：
- 每条检索式只写查询词，不要写解释；控制在 60 字以内。
- 日期不要写进检索式（时间范围由系统控制）。
- 不要编造网址、产品名或不存在的机构名；只使用上面出现过的公司、产品与事件信息。
- purpose 用一句话说明这条检索式想找什么，便于人工复核。

只返回 JSON 对象，字段严格为 queries（数组，元素含 text、purpose），不要 Markdown 包裹。',
'内置提示词：阶段 4 资料准备（检索式生成）的默认版本',strftime('%s','now'),strftime('%s','now'));
