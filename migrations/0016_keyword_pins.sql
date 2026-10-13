-- 常驻词（阶段 3 M5）：
-- 人工标记"这个词必须一直可用"，注入提示词时即使频次低、超出条数上限也带上。
-- 以规范词键为键：合并后由合并逻辑把标记搬到目标词，撤销时按记录还原。
CREATE TABLE keyword_pins (
    key TEXT PRIMARY KEY,     -- 规范词键，与词表页的行键一致
    created_at INTEGER NOT NULL  -- 标记时间，UTC 秒
);

-- 合并记录增加常驻标记的搬运事实：撤销时据此把标记还给原写法，
-- 并清掉"本次合并才戴上"的目标标记（链式合并逆序撤销可能留下多余标记，界面可手动取消）。
ALTER TABLE keyword_merges ADD COLUMN pinned_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE keyword_merges ADD COLUMN target_was_pinned INTEGER NOT NULL DEFAULT 0;
