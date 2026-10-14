-- 合并建议提示词升级到 v2：给组数设上限并要求直接给结果。
-- 起因：首批真实调用在 60 个词的批次上把 8192 输出预算耗尽（stop_reason=max_tokens），
-- JSON 被截断后校验失败。不加限制地"找出全部同指组"会让模型先长篇分析、再写长答案。
-- 提示词不可变，因此另存一条版本并归档 v1；被归档的版本仍可回溯与重新启用。
UPDATE llm_prompts
SET status='archived', updated_at=strftime('%s','now')
WHERE task_kind='keyword_merge_suggest' AND status='active';

INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
SELECT 'prompt-merge-suggest-bounded','keyword_merge_suggest','merge-suggest',
       (SELECT COALESCE(MAX(version),0)+1 FROM llm_prompts WHERE prompt_key='merge-suggest'),
       '合并建议（限量）','active','',
'你在维护一份检索关键词词表。下面是一批正在使用的写法，每行一个，括号里是类型。
请找出其中**指同一事物**的写法，组成建议组，供人工确认后再合并。

只处理同指，不处理下面两类：
- 可以建议：同一公司、产品、人物或概念的几种写法（中英文、大小写与空格差异都算）。
- 不要建议：主体和围绕它的事件句。「Dots」与「Dots 获得重大升级」不是同一种写法，
  「Codex」与「Codex 为 Pro 用户推出测试版」也不是——事件句说的是发生了什么，不是它叫什么。
- 不要建议：大概念与它下属的型号、子品牌、细分称呼（「岚图」与「岚图梦想家9」、
  「奕境汽车」与「奕境X9」粒度不同，合并会抹平检索粒度）。
- 两条事件句只有确实在说同一件事时才能成一组。
- 拿不准就不要提，宁少勿多。

最多给 8 组，按把握从大到小排；超过 8 组时只给最像的那 8 组。
直接给出结果，不要展开分析，也不要解释你的判断过程。

每组给出：
terms 是组内全部写法，2 至 5 个，必须逐字照抄下面的行，不要改写、不要翻译；
只写写法本身：不要带括号里的类型标注，也不要把整句话当成一个词；
target 是建议保留的写法，必须是 terms 之一；
rationale 是一句话说明为什么认为是同一个事物。

必须只返回 JSON 对象，字段严格为 groups，不要 Markdown 包裹：
{"groups":[{"terms":["写法A","写法B"],"target":"写法A","rationale":"同一产品的中英文写法"}]}
没有同指写法时返回 {"groups":[]}。

词表：
{{vocabulary}}',
'v1 让模型在 60 词批次上找"全部"同指组，输出连同思考耗尽 8192 预算导致 JSON 截断；本版限量 8 组并要求直接给结果',
       strftime('%s','now'),strftime('%s','now');
