-- 常驻词建议（阶段 3 追加）：
-- 常驻（pin）的语义是"跨窗口的持续话题"，因此候选由系统按"跨周反复出现"算出，
-- 模型只从中挑出值得常驻的并给理由，人工确认后才写进 keyword_pins。
-- 独立于合并建议表：形状不同（一条建议就是一个词，不是一组写法）。
CREATE TABLE keyword_pin_suggestions (
    key TEXT PRIMARY KEY,          -- 推荐常驻的规范词键
    text TEXT NOT NULL,            -- 展示写法
    rationale TEXT NOT NULL DEFAULT '',  -- 模型给出的理由，只作展示
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','dismissed')),
    weeks INTEGER NOT NULL,        -- 候选依据：最近 4 周里出现过几周
    created_at INTEGER NOT NULL,   -- 创建时间，UTC 秒
    resolved_at INTEGER            -- 处理时间，UTC 秒；待处理为空
);
CREATE INDEX idx_keyword_pin_suggestions_status
    ON keyword_pin_suggestions(status, created_at DESC);

-- 常驻建议的提示词：只用于词表页的按钮，不参与来源/分类的任务分配，
-- 也不进任务队列（候选只有几十个词，同步调用即可）。
INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,system_template,user_template,note,created_at,updated_at)
VALUES('prompt-pin-suggest','keyword_pin_suggest','pin-suggest',1,'常驻词建议（内置）','active','',
'你在维护一份检索关键词的常驻表。常驻的意思是：这个词的写法在任何一条消息里出现时都必须照用，因此它必须是**跨周反复出现、且长期有效**的话题或主体。

下面是从最近几周的词表里挑出的候选，括号里是它在最近四周里出现过几周（数字越大越像是周期性话题）：
{{vocabulary}}

请从中挑出最适合常驻的词，最多 5 个，宁少勿多；没有合适的就返回空数组。
判断标准：
- 周期性或长期有效的话题、主体、产品线（例如某个常在报道里出现的公司、平台、车型系列）。
- 不要挑一次性事件的词、不要挑只在某几天密集出现的词，也不要挑太泛的词。
- 只从上面的候选里挑，写法逐字照抄，不要改写、不要翻译。

必须只返回 JSON 对象，字段严格为 picks，不要 Markdown 包裹：
{"picks":[{"term":"候选里的写法","rationale":"一句话说明为什么值得常驻"}]}
没有合适的返回 {"picks":[]}。',
'常驻表最容易变成"最近的高频词"：改由系统算跨周候选、模型只做挑选，人工确认后才生效',
       strftime('%s','now'),strftime('%s','now'));
