-- Provider 请求协议可选：默认 chat_completions 保持既有行为不变，
-- anthropic_messages 用于只提供 /v1/messages 形状的上游（含 Anthropic 兼容网关）。
-- 不加 CHECK 约束：沿用 0010 的结论——新增协议时不必重建表，取值由
-- domain.models.LLMProtocol 在应用层校验。
ALTER TABLE llm_providers ADD COLUMN protocol TEXT NOT NULL DEFAULT 'chat_completions';
