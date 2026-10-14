-- 合并建议改为按"词"记账（阶段 3 追加）：
-- 原先按位置切片（第 0–20 个词、20–40 个…）并用偏移记台账。词表按频次与最近出现排序，
-- 新采集的词会插到前面把整张表挤位，于是旧切片内容全变、指纹失效，长期运行等于每次都要
-- 从头重扫。改成按词记账：扫过的写法永不重扫，换序也不受影响，稳态成本只剩新词。
CREATE TABLE keyword_suggestion_scanned (
    normalized TEXT PRIMARY KEY,   -- 已扫描写法的匹配键
    scanned_at INTEGER NOT NULL    -- 扫描时间，UTC 秒
);

-- 已有安装的近似回填：过去按 20 个词一批、从第 0 个词连续生成过，
-- 因此把当前排序下的前 (最大偏移 + 20) 个词视为已扫描——宁可少扫一次，也不再付一遍费用。
-- 这里刻意自带一份聚合查询而不是引用应用里的 SQL：迁移是历史快照，不随应用演进。
INSERT INTO keyword_suggestion_scanned(normalized, scanned_at)
SELECT keyword_key, strftime('%s','now') FROM (
    SELECT COALESCE(a.canonical_norm, k.normalized) AS keyword_key,
           COUNT(DISTINCT k.message_version_id) AS mentions,
           MAX(COALESCE(v.published_at, v.collected_at)) AS last_seen
    FROM enrichment_keywords k
    JOIN message_versions v ON v.id = k.message_version_id
    JOIN messages m ON m.id = v.message_id
    LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
    GROUP BY keyword_key
    ORDER BY mentions DESC, last_seen DESC, keyword_key
    LIMIT (
        SELECT COALESCE(MAX(batch_offset), -20) + 20 FROM keyword_suggestion_batches
    )
);
