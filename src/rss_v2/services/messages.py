"""消息查询用例。"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.domain import DomainError, Message, MessageVersion, Translation
from rss_v2.ports import MessageRepository, TranslationRepository


@dataclass(slots=True)
class MessageService:
    """消息列表和详情读取。"""

    messages: MessageRepository
    translations: TranslationRepository

    def list(
        self,
        query: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Message, MessageVersion | None, list[Translation]]]:
        result: list[tuple[Message, MessageVersion | None, list[Translation]]] = []
        for message, version in self.messages.list_messages(query, source_id, limit, offset):
            translations = self.translations.list_for_version(version.id) if version else []
            result.append((message, version, translations))
        return result

    def detail(
        self, message_id: str
    ) -> tuple[Message, list[tuple[MessageVersion, list[Translation]]]]:
        message = self.messages.get_message(message_id)
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        versions = self.messages.versions(message_id)
        return message, [
            (version, self.translations.list_for_version(version.id)) for version in versions
        ]
