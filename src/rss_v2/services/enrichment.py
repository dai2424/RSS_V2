"""消息内容加工：为过长标题和描述生成精简标题、中文摘要与检索关键词。"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.domain import (
    DomainError,
    Enrichment,
    EnrichmentResult,
    MessageVersion,
    Provider,
    ProviderKey,
    Task,
    TaskStatus,
    TaskType,
)
from rss_v2.domain.values import mask_secret, new_id, now
from rss_v2.llm import LLMProvider
from rss_v2.ports import EnrichmentRepository, MessageRepository, TaskRepository
from rss_v2.services.llm_failover import LLMFailover


@dataclass(slots=True)
class EnrichmentService:
    """采集自动入队或手动入队，worker 调用模型产出加工结果。"""

    messages: MessageRepository  # 原文与不可变版本
    enrichments: EnrichmentRepository  # 加工结果持久化
    tasks: TaskRepository  # 幂等任务与结果关联
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与调用审计
    prompt_version: str  # 当前提示词版本标识
    cooldown_seconds: int  # 临时错误冷却秒数

    def create_task(self, message_id: str) -> Task:
        """为最新版本创建或复用加工任务。"""
        message = self.messages.get_message(message_id)
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        version = self.messages.latest_version(message.id)
        if version is None:
            raise DomainError("message_empty", "消息没有可加工版本")
        return self.create_task_for_version(version)

    def create_task_for_version(self, version: MessageVersion) -> Task:
        """为指定版本入队；自动加工与手动加工共用同一幂等规则。"""
        for model in self.failover.model_names():
            existing = self.tasks.get_by_idempotency(self.idempotency_key(version, model))
            if existing is not None:
                return existing
        candidate = self.failover.first_available()
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        provider, model = candidate
        return self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.ENRICH_MESSAGE,
                idempotency_key=self.idempotency_key(version, model.model),
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=version.id,
                output_version_id=None,
                payload={
                    "message_id": version.message_id,
                    "provider_id": provider.id,
                    "model": model.model,
                    "prompt_version": self.prompt_version,
                },
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
        )

    def idempotency_key(self, version: MessageVersion, model: str) -> str:
        """同一版本、同一模型、同一提示词版本只调用一次模型。"""

        return f"enrich:{version.id}:{model}:{self.prompt_version}"

    def run(self, task: Task) -> Enrichment:
        """执行任务；成功结果可复用，依次切换 Key 与 Provider×模型。"""

        version = self._input_version(task)
        prompt = str(task.payload.get("prompt_version", self.prompt_version))
        completed = next(
            (
                item
                for item in self.enrichments.list_for_version(version.id)
                if item.task_id == task.id and item.status == "succeeded"
            ),
            None,
        )
        existing = completed or self.enrichments.get_for_version(
            version.id, prompt, str(task.payload.get("model", ""))
        )
        if existing is not None and existing.status == "succeeded":
            return existing

        def save(
            provider: Provider, model_name: str, key: ProviderKey, result: EnrichmentResult
        ) -> Enrichment:
            return self._save_result(task, version, provider, model_name, key, prompt, result, None)

        try:
            return self.failover.execute(
                task, version, prompt, self.cooldown_seconds, self.provider.enrich, save
            )
        except DomainError as exc:
            provider, model_name = self._failure_pair(task)
            self._save_result(task, version, provider, model_name, None, prompt, None, exc)
            raise

    def list_for_version(self, version_id: str) -> list[Enrichment]:
        """返回版本已有的加工结果。"""

        return self.enrichments.list_for_version(version_id)

    def _input_version(self, task: Task) -> MessageVersion:
        if task.input_version_id is None:
            raise DomainError("task_invalid", "加工任务缺少输入版本")
        version = self.messages.get_version(task.input_version_id)
        if version is None or version.message_id != task.payload.get("message_id"):
            raise DomainError("version_changed", "任务对应的消息版本已不存在")
        return version

    def _failure_pair(self, task: Task) -> tuple[Provider, str]:
        """失败记录沿用首选候选，与排队时的配置快照保持一致。"""

        candidates = self.failover.candidates(task)
        if candidates:
            return candidates[0]
        return (
            Provider("", "", "", False, 0.0, None, now(), now()),
            str(task.payload.get("model", "")),
        )

    def _save_result(
        self,
        task: Task,
        version: MessageVersion,
        provider: Provider,
        model_name: str,
        key: ProviderKey | None,
        prompt: str,
        result: EnrichmentResult | None,
        error: DomainError | None,
    ) -> Enrichment:
        """成功与失败共用版本关联和元数据存储。"""

        return self.enrichments.save(
            Enrichment(
                id=new_id(),
                message_version_id=version.id,
                status="succeeded" if result else "failed",
                title=result.title if result else None,
                summary=result.summary if result else None,
                keywords=result.keywords if result else (),
                provider_id=provider.id,
                key_masked=mask_secret(key.secret) if key else None,
                model=model_name,
                prompt_version=prompt,
                task_id=task.id,
                error_code=error.code if error else None,
                error_message=error.message if error else None,
                created_at=now(),
                updated_at=now(),
            ),
            task.lease_token,
        )
