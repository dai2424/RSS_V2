"""消息查询用例。"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.domain import (
    DomainError,
    Enrichment,
    Message,
    MessageVersion,
    PromptSample,
    Translation,
)
from rss_v2.ports import EnrichmentRepository, MessageRepository, TranslationRepository


@dataclass(frozen=True, slots=True)
class MessageRow:
    """一个消息版本及其机器生成内容；列表与详情共用。"""

    message: Message  # 逻辑消息
    version: MessageVersion | None  # 该行对应的版本；没有版本时为空
    translations: list[Translation]  # 该版本的译文，按更新时间倒序
    enrichments: list[Enrichment]  # 该版本的加工结果，按更新时间倒序


@dataclass(slots=True)
class MessageService:
    """消息列表和详情读取。"""

    messages: MessageRepository
    translations: TranslationRepository
    enrichments: EnrichmentRepository

    def list(
        self,
        query: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MessageRow]:
        return [
            self._row(message, version)
            for message, version in self.messages.list_messages(query, source_id, limit, offset)
        ]

    def version_sample(self, version_id: str) -> PromptSample:
        """把某个消息版本转成提示词试跑样例。"""

        version = self.messages.get_version(version_id)
        if version is None:
            raise DomainError("version_not_found", "消息版本不存在")
        return PromptSample(version.title, version.summary, version.content)

    def count_for_source(self, source_id: str) -> int:
        """来源下的消息条数，用于删除确认中的影响说明。"""

        return self.messages.count_for_source(source_id)

    def detail(self, message_id: str) -> tuple[Message, list[MessageRow]]:
        message = self.messages.get_message(message_id)
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        versions = self.messages.versions(message_id)
        return message, [self._row(message, version) for version in versions]

    def _row(self, message: Message, version: MessageVersion | None) -> MessageRow:
        return MessageRow(
            message,
            version,
            self.translations.list_for_version(version.id) if version else [],
            self.enrichments.list_for_version(version.id) if version else [],
        )
