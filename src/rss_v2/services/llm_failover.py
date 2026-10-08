"""Provider×模型×Key 的逐级回退、Key 冷却和调用审计。

翻译和内容加工共用这一层：候选顺序、Key 过滤、每次调用的审计写入和失败回退
只在这里实现一次；服务层只提供自己的输入、结构化结果和持久化方式。
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from rss_v2.domain import (
    DomainError,
    ExternalServiceError,
    LLMCall,
    MessageVersion,
    Provider,
    ProviderKey,
    ProviderModel,
    Task,
)
from rss_v2.domain.values import mask_secret, new_id, now
from rss_v2.ports import LLMCallRepository, LLMConfigRepository

ResultT = TypeVar("ResultT")
RecordT = TypeVar("RecordT")

#: 会让 Key 进入短暂冷却的错误分类；配置类错误不冷却，避免误停可用密钥。
COOLDOWN_CODES = frozenset({"rate_limited", "llm_timeout", "llm_server_error"})


def _classified(error: Exception) -> DomainError:
    """把适配器异常收敛为稳定分类；未知异常必须留下日志再分类。"""

    if isinstance(error, DomainError):
        return error
    logging.getLogger(__name__).error("llm_error class=%s", type(error).__name__)
    return ExternalServiceError("llm_internal_error", "模型适配器发生内部错误")


def _input_hash(version: MessageVersion) -> str:
    """模型输入的 SHA-256；审计只保存摘要哈希，不保存 prompt 原文。"""

    return hashlib.sha256(
        f"{version.title}\n{version.summary}\n{version.content}".encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class LLMFailover:
    """按候选顺序试跑模型调用，负责审计与 Key 冷却。"""

    llm_config: LLMConfigRepository  # Provider、模型候选与 Key
    llm_calls: LLMCallRepository  # 调用元数据审计

    def enabled_pairs(self) -> list[tuple[Provider, ProviderModel]]:
        """当前启用的 Provider×模型候选，仓储顺序已稳定。"""

        return [
            (provider, model)
            for provider in self.llm_config.list_providers(enabled_only=True)
            for model in self.llm_config.list_models(provider.id, enabled_only=True)
        ]

    def model_names(self) -> list[str]:
        """当前启用的模型名，用于按模型生成任务幂等键。"""

        return [model.model for _, model in self.enabled_pairs()]

    def first_available(self) -> tuple[Provider, ProviderModel] | None:
        """第一个"启用且至少有一枚可用 Key"的候选组合。"""

        return next((item for item in self.enabled_pairs() if self.keys(item[0])), None)

    def candidates(self, task: Task) -> list[tuple[Provider, str]]:
        """首选组合在入队时快照；修改配置不改变已排队任务的模型名。"""

        preferred_provider = str(task.payload.get("provider_id", ""))
        preferred_model = str(task.payload.get("model", ""))
        rest = sorted(
            (
                (provider, model)
                for provider, model in self.enabled_pairs()
                if not (provider.id == preferred_provider and model.model == preferred_model)
            ),
            key=lambda item: (item[1].priority, item[1].created_at, item[0].id),
        )
        head: list[tuple[Provider, str]] = []
        if preferred_provider and preferred_model:
            provider = next(
                (
                    item
                    for item in self.llm_config.list_providers(enabled_only=True)
                    if item.id == preferred_provider
                ),
                None,
            )
            if provider is not None:
                head = [(provider, preferred_model)]
        return head + [(provider, model.model) for provider, model in rest]

    def keys(self, provider: Provider) -> list[ProviderKey]:
        """仓储按优先级排序，过滤停用、冷却和密钥值为空的 Key。"""

        current = now()
        return [
            key
            for key in self.llm_config.list_keys(provider.id, enabled_only=True)
            if (key.cooldown_until is None or key.cooldown_until <= current) and key.secret.strip()
        ]

    def cooldown_until(self, error: DomainError, cooldown_seconds: int) -> int | None:
        """临时错误让 Key 进入冷却；配置类错误不冷却，避免误停可用密钥。"""

        if error.code in COOLDOWN_CODES:
            return now() + cooldown_seconds
        return None

    def record_call(
        self,
        task_id: str | None,
        provider: Provider,
        model_name: str,
        key: ProviderKey,
        prompt: str,
        input_hash: str,
        duration: int,
        tokens: dict[str, int],
        error: DomainError | None,
    ) -> None:
        """每次模型调用（含连接测试）写入审计；密钥只记录掩码。"""

        self.llm_calls.add(
            LLMCall(
                new_id(),
                task_id,
                provider.id,
                mask_secret(key.secret),
                model_name,
                prompt,
                input_hash,
                duration,
                tokens,
                "failed" if error else "succeeded",
                error.code if error else None,
                now(),
            )
        )

    def execute(
        self,
        task: Task,
        version: MessageVersion,
        prompt: str,
        cooldown_seconds: int,
        invoke: Callable[
            [Provider, str, str, str, str, str, str], tuple[ResultT, dict[str, int], int]
        ],
        persist: Callable[[Provider, str, ProviderKey, ResultT], RecordT],
    ) -> RecordT:
        """逐 Provider×模型×Key 调用模型，成功后交给调用方持久化。

        invoke 的签名与 `LLMProvider` 的方法一致，服务层直接传绑定方法即可。
        每次尝试都写审计并更新 Key 冷却；候选耗尽后抛出最后一个错误，
        由调用方写入失败记录，因此这里不捕获最终异常。
        """
        candidates = self.candidates(task)
        if not candidates:
            raise DomainError("llm_not_configured", "没有启用的模型")
        last_error = DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        for provider, model_name in candidates:
            for key in self.keys(provider):
                started = time.perf_counter()
                try:
                    result, tokens, duration = invoke(
                        provider,
                        model_name,
                        key.secret,
                        version.title,
                        version.summary,
                        version.content,
                        prompt,
                    )
                except Exception as exc:
                    error = _classified(exc)
                    self.record_call(
                        task.id,
                        provider,
                        model_name,
                        key,
                        prompt,
                        _input_hash(version),
                        int((time.perf_counter() - started) * 1000),
                        {},
                        error,
                    )
                    self.llm_config.mark_key(
                        key.id, error.code, self.cooldown_until(error, cooldown_seconds)
                    )
                    if error.code == "task_lease_lost":
                        raise error from None
                    last_error = error
                    continue
                self.record_call(
                    task.id,
                    provider,
                    model_name,
                    key,
                    prompt,
                    _input_hash(version),
                    duration,
                    tokens,
                    None,
                )
                self.llm_config.mark_key(key.id, "succeeded", None)
                return persist(provider, model_name, key, result)
        raise last_error from None
