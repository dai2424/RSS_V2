-- 合并撤销改为按"每次合并的独立变更记录"回放（阶段 3 修缺陷）：
--
-- 原先别名行只保存一层历史（previous_canonical / previous_merge_id）。链式合并时后一次会把
-- 前一次的记录覆盖掉：A→B 之后 B→C，再逆序撤销 C 与 B，别名行会被当成"由 B 新建"而删掉，
-- 于是 A 的合并也一起丢了（实测：撤销后别名表清空，回不到合并 A 之后的状态）。
--
-- 新表按"合并 → 该次改动过的每条别名 → 改动前的原值"记一行，撤销时按记录回放，
-- 与撤销顺序无关；previous_canonical 为 NULL 表示这条别名是本次合并新建的。
CREATE TABLE keyword_alias_changes (
    merge_id TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    previous_canonical TEXT,       -- 改动前的规范词；NULL 表示本次合并新建了这条别名
    previous_merge_id TEXT,        -- 改动前所属的合并
    PRIMARY KEY (merge_id, alias_norm)
);

-- 用现存的一层历史回填，保证已经发生的合并在升级后仍可撤销。
INSERT INTO keyword_alias_changes(merge_id, alias_norm, previous_canonical, previous_merge_id)
SELECT merge_id, alias_norm, previous_canonical, previous_merge_id
FROM keyword_aliases
WHERE merge_id IS NOT NULL;

-- 两层历史不再需要：它既是缺陷来源，也会与变更记录打架。
ALTER TABLE keyword_aliases DROP COLUMN previous_canonical;
ALTER TABLE keyword_aliases DROP COLUMN previous_merge_id;
