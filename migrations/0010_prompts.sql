-- 提示词库与任务分配。
-- 提示词从适配器硬编码搬进数据库。版本串沿用 {prompt_key}-v{version}，与既有
-- translations.prompt_version、message_enrichments.prompt_version 和
-- llm_calls.prompt_version 逐字一致，所以结果表、审计与任务幂等键都不需要迁移。
-- task_kind 不加 CHECK 约束：后续新增任务类型时不必重建表（0009 已吃过这个苦头），
-- 取值由 domain.prompts.TASK_SPECS 在应用层校验。
CREATE TABLE llm_prompts (
    id TEXT PRIMARY KEY,
    task_kind TEXT NOT NULL,
    prompt_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
    system_template TEXT NOT NULL DEFAULT '',
    user_template TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(prompt_key, version)
);

-- 同一任务类型只允许一个启用版本；未绑定提示词的来源与分类都用它。
CREATE UNIQUE INDEX idx_llm_prompts_active ON llm_prompts(task_kind) WHERE status = 'active';
CREATE INDEX idx_llm_prompts_kind ON llm_prompts(task_kind, status, version DESC);

-- 任务分配：来源与分类共用一张表，enabled 或 prompt_id 为空表示继承上一层。
CREATE TABLE task_settings (
    scope TEXT NOT NULL CHECK (scope IN ('source', 'category')),
    scope_id TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    enabled INTEGER CHECK (enabled IN (0, 1)),
    prompt_id TEXT REFERENCES llm_prompts(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(scope, scope_id, task_kind)
);

-- 种子：把改造前的硬编码提示词原样落库为 v1，升级后行为不变。
-- 系统提示保持为空：改造前它只承载版本标签，不含任何指令，版本串现在记在 llm_calls。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-translation-v1','translate_message','translation',1,'翻译成中文（内置）','active','',
'你是专业科技资讯翻译。请把以下英文 RSS 内容翻译成简洁、准确的简体中文。必须只返回 JSON 对象，字段严格为 title、summary、content，不要 Markdown 包裹。
标题：{{title}}
摘要：{{summary}}
正文：{{content}}',
'内置提示词，与改造前的硬编码文本一致',strftime('%s','now'),strftime('%s','now'));

INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-enrich-v1','enrich_message','enrich',1,'内容加工（内置）','active','',
'你是科技资讯编辑。请阅读以下 RSS 内容，用简体中文产出便于浏览和检索的结果：
title 是精简标题，不超过 40 字，保留关键主体，不添加原文没有的信息；
summary 是 1 至 3 句摘要，说明发生了什么；
keywords 是 3 至 8 个检索关键词，可以是中文词或原文专有名词。
必须只返回 JSON 对象，字段严格为 title、summary、keywords（字符串数组），不要 Markdown 包裹。
标题：{{title}}
摘要：{{summary}}
正文：{{content}}',
'内置提示词，与改造前的硬编码文本一致',strftime('%s','now'),strftime('%s','now'));
