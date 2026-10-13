-- 热词注入（阶段 3 M1–M3）：
-- 1) enrichment_keywords 增加"是否复用注入词表"列，供复用率统计；
-- 2) 加工提示词升级到 v3：注入现有词表并要求优先复用已有写法。
-- reused 是服务端判定（模型输出的写法归一化后是否落在注入集合里），0/1/NULL 三态，
-- NULL 表示当时没有词表可判定（注入关闭或词表为空），不参与复用率统计。
ALTER TABLE enrichment_keywords ADD COLUMN reused INTEGER;

-- 本任务类型下已有的启用版本一并归档（含用户在界面上自建的版本），保证升级后生效的是 v3；
-- 被归档的版本仍在提示词列表里，可以随时重新启用。
UPDATE llm_prompts
SET status='archived', updated_at=strftime('%s','now')
WHERE task_kind='enrich_message' AND status='active';

-- 版本号取当前最大值加一：用户若已自建过 enrich 版本，这里不会撞 UNIQUE(prompt_key, version)。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
SELECT 'prompt-enrich-vocabulary','enrich_message','enrich',
       (SELECT COALESCE(MAX(version),0)+1 FROM llm_prompts WHERE prompt_key='enrich'),
       '内容加工（注入词表）','active','',
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

下面是最近在用的词表。如果这条消息讲的确实是同一件事物，请直接使用词表里的写法，不要换一种写法；只有词表里没有、且确实需要检索的新事物才新建。
{{vocabulary}}

必须只返回 JSON 对象，字段严格为 title、summary、keywords，不要 Markdown 包裹。
标题：{{title}}
摘要：{{summary}}
正文：{{content}}',
'把现有词表注入提示词，让模型复用同一批写法，减少同义写法继续扩散；服务端按注入集合判定复用率',
       strftime('%s','now'),strftime('%s','now');
