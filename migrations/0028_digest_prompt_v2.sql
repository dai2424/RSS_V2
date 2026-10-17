-- 资料整理提示词升级到 v2：限制单批产出、引文限长、缺口从严。
-- 起因（真实环境实测，43 份资料的选题）：v1 没有条数上限，模型每批写出 6k–22k token 的
-- 事实与缺口，16 批共 23.2 万 token、耗时 22 分钟；其中两批输入只有 59/87 token（相当于
-- 三条搜索摘要）却写出 1–2 万 token，明显在凑数与扩写。同一次整理还产出 108 条缺口说明
-- （53 条阻塞），把真正的缺口淹没掉。
-- v2 把"最多几条"写进提示词，并明确"资料支撑不了就少写"；服务端同时按
-- digest_facts_per_batch / digest_gaps_per_batch 截断兜底（见 domain/facts.py 的 DigestLimits）。
-- 提示词不可变，因此另存一条版本并归档 v1；被归档的版本仍可回溯与重新启用。
UPDATE llm_prompts
SET status='archived', updated_at=strftime('%s','now')
WHERE task_kind='digest_materials' AND status='active';

INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
SELECT 'prompt-digest-v2','digest_materials','digest',
       (SELECT COALESCE(MAX(version),0)+1 FROM llm_prompts WHERE prompt_key='digest'),
       '资料整理（限量输出）','active','',
'你在为一篇科技博客整理资料。下面是选题与**本批**已读资料（每条资料前有材料 ID 与来源信息）。

选题：{{title}}
{{summary}}

本批资料（只能引用这里出现的内容）：
{{materials}}

请输出三类内容：
1. facts：本批资料能直接支撑的关键事实，最多 {{facts_per_batch}} 条。每条包含：
   - statement：一句话陈述（必须能被引文直接支持）；
   - kind：fact（客观事实）/ analysis（基于事实的行业分析）/ speculation（推测，必须用"可能/或许"等不确定表达）；
   - supports：支持它的引文列表，每条包含 material_id（必须是上面出现过的材料 ID）与 quote。
     quote 必须是该资料正文里原样连续出现的一段，20 至 120 字，只取最能支撑这条事实的那一句；
     不要整段抄写、不要跨段拼接、不要改写、不要用省略号。
2. conflicts：资料之间的分歧或互相矛盾之处（例如厂商说法与测试结果不一致）。每条包含 text 与
   statement_indexes（涉及本批第几条 facts，从 0 开始）。
3. gaps：真正影响动笔的关键信息缺口，最多 {{gaps_per_batch}} 条；只有"不解决就不该写主线"的才把
   blocking 设为 true，一般性补充建议用 false，不值得提醒的就不写。

要求：
- 只使用上面资料里的信息，不要引入模型记忆里的数字、引语或时间。
- 本批资料支撑不了那么多条就少写：宁可写 3 条扎实的，也不要凑满条数或编造。
- 只有资料里确实缺某项关键信息时才写 gaps，不要每批都例行补一条缺口说明。
- 同一件事在本批资料里重复出现时只写一条，把不同来源的引文都放进 supports。
- 中文输出。

只返回 JSON 对象，字段严格为 facts、conflicts、gaps，不要 Markdown 包裹。',
'v1 没有条数上限，实测每批写出 1–2 万 token 的事实与缺口（凑数、编造、例行补缺口）；本版限量输出',
       strftime('%s','now'),strftime('%s','now');
