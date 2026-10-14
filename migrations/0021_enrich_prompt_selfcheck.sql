-- 加工提示词升级到 v4：先自评、再输出；明确不产出数值与句子式写法。
-- 起因：v3 已经写了"不要只在正文里成立的数值与指标"，但实测仍然产出「19.98万元」
-- 「2250千瓦」以及把整句话当关键词的写法。v4 把判据改成"先自问别人会不会拿它搜索"，
-- 并把这两类写法的例子摆出来；服务端同时按形态剔除（见 boundary.keywords）。
-- 提示词不可变，因此另存一条版本并归档 v3；被归档的版本仍可回溯与重新启用。
UPDATE llm_prompts
SET status='archived', updated_at=strftime('%s','now')
WHERE task_kind='enrich_message' AND status='active';

INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
SELECT 'prompt-enrich-selfcheck','enrich_message','enrich',
       (SELECT COALESCE(MAX(version),0)+1 FROM llm_prompts WHERE prompt_key='enrich'),
       '内容加工（先自评再输出）','active','',
'你是科技资讯编辑。请阅读以下内容，用简体中文产出便于浏览和检索的结果：
title 是精简标题，不超过 40 字，保留关键主体，不添加原文没有的信息；
summary 是 1 至 3 句摘要，说明发生了什么；
keywords 是供检索用的索引标签，最多 6 个对象，宁少勿多；没有值得检索的词就返回空数组。
每个对象只有 text 与 kind 两个字段：
text 是关键词本身；
kind 只能是 entity、topic、event 之一。entity 是具名实体（公司、产品、模型、人物、机构、地点、法规、专有技术名），topic 是能概括话题的领域词，event 是用一句话概括的事件。

每写一个词先自问一句：别人会不会拿这个词去搜索？不会的就删掉，不要凑数。
其余判据：
- 优先 entity：保留原文写法，不要译成中文。
- 不要泛化的动作、流程和抽象名词，它们在多个行业通用，检索不出任何东西。
- 不要价格、数量、指标这类只在正文里成立的数字与单位（例如「19.98万元」「2250千瓦」「1612N」）；这些内容写进 summary。
- 不要把一句话当关键词：不要带逗号、顿号、句号；事件词概括成短句即可，不要照抄正文。
- event 最多 1 个，不要把一个事件拆成多个碎片。
- 同一概念只用一种写法，不要中英文各出一份。

下面是最近在用的词表。如果这条消息讲的确实是同一件事物，请直接使用词表里的写法，不要换一种写法；只有词表里没有、且确实需要检索的新事物才新建。
{{vocabulary}}

必须只返回 JSON 对象，字段严格为 title、summary、keywords，不要 Markdown 包裹。
标题：{{title}}
摘要：{{summary}}
正文：{{content}}',
'v3 的数值与句子式写法仍在产出；本版把判据改成"先自问会不会被搜索"，并点名这两类反例',
       strftime('%s','now'),strftime('%s','now');
