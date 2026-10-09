"""版本入库后的自动处理：按来源配置与内容特征决定要建哪些模型任务。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from rss_v2.domain import DomainError, MessageVersion, Source, SourceLanguage, Task, TaskType
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessingService:
    """采集入库后的唯一判定点，自动任务的规则只在这里维护。"""

    translate: Callable[[MessageVersion, str], Task]  # 翻译任务入队：(版本, 来源 ID)
    enrich: Callable[[MessageVersion, str], Task]  # 内容加工任务入队
    prompts: PromptResolver  # 任务分配与提示词解析
    enrich_title_threshold: int  # 标题超过该字符数才加工
    enrich_text_threshold: int  # 摘要或正文超过该字符数才加工

    def enqueue(self, version: MessageVersion, source: Source) -> list[str]:
        """为新版本创建自动任务，返回新建或复用的任务 ID。

        内容特征（语言、长度）与配置开关（来源或分类的任务分配）都满足才入队。
        单个任务建不出来（没有可用提示词或没有可用 Key）只记录原因并跳过：
        自动处理是采集的附加效果，不能因此让采集失败。
        """
        task_ids: list[str] = []
        if version.language == SourceLanguage.ENGLISH and self._wanted(
            source, TaskType.TRANSLATE_MESSAGE
        ):
            task_ids += self._enqueue("translate", self.translate, version, source.id)
        if self.is_long(version) and self._wanted(source, TaskType.ENRICH_MESSAGE):
            task_ids += self._enqueue("enrich", self.enrich, version, source.id)
        return task_ids

    def is_long(self, version: MessageVersion) -> bool:
        """标题或文本超过阈值才加工，避免为短消息多花一次模型调用。"""

        return (
            len(version.title) > self.enrich_title_threshold
            or max(len(version.summary), len(version.content)) > self.enrich_text_threshold
        )

    def _wanted(self, source: Source, kind: TaskType) -> bool:
        """来源或分类是否要跑这类任务，并且确实有可用提示词。"""

        resolved = self.prompts.resolve(source.id, kind.value)
        if resolved.enabled and resolved.prompt is not None:
            return True
        logger.info(
            "auto_task_off kind=%s source=%s enabled=%s prompt=%s",
            kind.value,
            source.id,
            resolved.enabled,
            resolved.prompt.id if resolved.prompt else "",
        )
        return False

    def _enqueue(
        self,
        label: str,
        create: Callable[[MessageVersion, str], Task],
        version: MessageVersion,
        source_id: str,
    ) -> list[str]:
        try:
            return [create(version, source_id).id]
        except DomainError as exc:
            logger.warning(
                "auto_task_skipped kind=%s version=%s code=%s reason=%s",
                label,
                version.id,
                exc.code,
                exc.message,
            )
            return []
