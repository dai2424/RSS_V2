-- 归一"来源只有 description/summary"的正文：这些版本的 content 是采集器把摘要
-- 复制一遍得到的副本，与 summary 完全相同，详情页会把同一段文字展示两次。
-- 内容指纹口径没有变（无独立正文时仍按摘要计算，见 domain.values.message_content_hash），
-- 因此这里不重算 content_hash，重新采集同一内容也不会被判定为版本变化。
UPDATE message_versions SET content = '' WHERE content = summary AND content <> '';
