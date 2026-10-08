"""版本入库后的自动处理：按语言和长度决定要建哪些模型任务。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from rss_v2.domain import DomainError, MessageVersion, SourceLanguage, Task

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessingService:
    """采集入库后的唯一判定点，自动任务的规则只在这里维护。"""

    translate: Callable[[MessageVersion], Task]  # 翻译任务入队
    enrich: Callable[[MessageVersion], Task]  # 内容加工任务入队
    enrich_title_threshold: int  # 标题超过该字符数才加工
    enrich_text_threshold: int  # 摘要或正文超过该字符数才加工

    def enqueue(self, version: MessageVersion) -> list[str]:
        """为新版本创建自动任务，返回新建或复用的任务 ID。

        单个任务建不出来（例如没有可用 Key）只记录原因并跳过：自动处理是采集的
        附加效果，不能因此让采集失败。
        """
        task_ids: list[str] = []
        if version.language == SourceLanguage.ENGLISH:
            task_ids += self._enqueue("translate", self.translate, version)
        if self.is_long(version):
            task_ids += self._enqueue("enrich", self.enrich, version)
        return task_ids

    def is_long(self, version: MessageVersion) -> bool:
        """标题或文本超过阈值才加工，避免为短消息多花一次模型调用。"""

        return (
            len(version.title) > self.enrich_title_threshold
            or max(len(version.summary), len(version.content)) > self.enrich_text_threshold
        )

    def _enqueue(
        self, kind: str, create: Callable[[MessageVersion], Task], version: MessageVersion
    ) -> list[str]:
        try:
            return [create(version).id]
        except DomainError as exc:
            logger.warning(
                "auto_task_skipped kind=%s version=%s code=%s reason=%s",
                kind,
                version.id,
                exc.code,
                exc.message,
            )
            return []
