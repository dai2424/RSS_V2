-- 关键词检索（阶段 2 第一期）：
-- 1) message_enrichments 的关键词从 JSON 数组搬进可索引的关系表，检索不再用 LIKE 打 JSON；
-- 2) 加工提示词升级到 v2：keywords 改为带 kind 的对象数组，并给出准入判据。
-- 关系表里每行是一个"匹配键"：raw 保留模型原始写法用于展示，normalized 只做字面归一化
-- （全角折半角、大小写、空白、首尾标点），由应用层计算，SQL 不参与归一化。
CREATE TABLE enrichment_keywords (
    enrichment_id TEXT NOT NULL REFERENCES message_enrichments(id) ON DELETE CASCADE,
    message_version_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,   -- 模型给出的顺序，即重要度次第
    raw TEXT NOT NULL,          -- 模型原始写法，展示用
    normalized TEXT NOT NULL,   -- 字面归一化后的匹配键，检索与统计只用它
    kind TEXT NOT NULL,         -- entity 具名实体 / topic 领域主题 / event 事件，取值由领域层校验
    PRIMARY KEY (enrichment_id, ordinal)
);
CREATE INDEX idx_enrichment_keywords_normalized ON enrichment_keywords(normalized);
CREATE INDEX idx_enrichment_keywords_version ON enrichment_keywords(message_version_id);

-- 历史行只有 JSON、没有关系表行：迁移阶段不做归一化（SQL 无法表达 NFKC 与 casefold），
-- 由 POST /api/keywords/rebuild 在应用层重建，不调用模型。
-- 本任务类型下已有的启用版本一并归档（含用户在界面上自建的版本），保证升级后
-- 生效的是 v2；被归档的版本仍在提示词列表里，可以随时重新启用。
UPDATE llm_prompts
SET status='archived', updated_at=strftime('%s','now')
WHERE task_kind='enrich_message' AND status='active';

-- 版本号取当前最大值加一：用户若已自建过 enrich 版本，这里不会撞 UNIQUE(prompt_key, version)。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
SELECT 'prompt-enrich-structured','enrich_message','enrich',
       (SELECT COALESCE(MAX(version),0)+1 FROM llm_prompts WHERE prompt_key='enrich'),
       '内容加工（结构化关键词）','active','',
'你是科技资讯编辑。请阅读以下内容，用简体中文产出便于浏览和检索的结果：
title 是精简标题，不超过 40 字，保留关键主体，不添加原文没有的信息；
summary 是 1 至 3 句摘要，说明发生了什么；
keywords 是供检索用的索引标签，最多 6 个对象，宁少勿多；没有值得检索的词就返回空数组。
每个对象只有 text 与 kind 两个字段：
text 是关键词本身；
kind 只能是 entity、topic、event 之一。entity 是具名实体（公司、产品、模型、人物、机构、地点、法规、专有技术名），topic 是能概括话题的领域词，event 是用一句话概括的事件。

判断一个词该不该收，只有一条标准：别人会不会用它来查找这条消息。
- 优先 entity：保留原文写法，不要译成中文。
- 不要泛化的动作、流程和抽象名词，它们在多个行业通用，检索不出任何东西。
- 不要只在正文里才成立的数值和指标，那些写进 summary。
- event 最多 1 个，不要把一个事件拆成多个碎片。
- 同一概念只用一种写法，不要中英文各出一份。

必须只返回 JSON 对象，字段严格为 title、summary、keywords，不要 Markdown 包裹。
标题：{{title}}
摘要：{{summary}}
正文：{{content}}',
'v1 的关键词是自由字符串，检索只能打在 JSON 文本上；本版按准入判据产出带类型的结构化关键词',
       strftime('%s','now'),strftime('%s','now');
