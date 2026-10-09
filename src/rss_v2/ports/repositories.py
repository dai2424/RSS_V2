"""业务使用的仓储端口。"""

from __future__ import annotations

from typing import Any, Protocol

from rss_v2.domain import (
    Category,
    CollectionRun,
    Enrichment,
    HealthCheck,
    LLMCall,
    Message,
    MessageVersion,
    Prompt,
    PromptUsage,
    Provider,
    ProviderKey,
    ProviderModel,
    Source,
    SourceDeletion,
    Task,
    TaskSetting,
    TaskType,
    Translation,
)


class CategoryRepository(Protocol):
    """行业分类存取端口。"""

    def list(self, include_inactive: bool = False) -> list[Category]: ...

    def get(self, category_id: str) -> Category | None: ...

    def create(self, name: str, slug: str, is_builtin: bool = False) -> Category: ...


class SourceRepository(Protocol):
    """RSS 来源存取端口。"""

    def list(
        self,
        query: str | None = None,
        category_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Source]: ...

    def count(
        self,
        query: str | None = None,
        category_id: str | None = None,
        enabled: bool | None = None,
    ) -> int:
        """按与 list 相同的过滤条件返回来源总数，用于分页展示。"""
        ...

    def delete(self, source_id: str) -> SourceDeletion:
        """删除来源及其消息、版本、译文、健康记录、采集结果与相关任务。

        未完成的采集运行会移除该来源；来源不存在时抛 KeyError。
        """
        ...

    def get(self, source_id: str) -> Source | None: ...

    def create(self, source: Source) -> Source: ...

    def update(self, source_id: str, changes: dict[str, Any]) -> Source: ...

    def save_feed_metadata(self, source_id: str, metadata: dict[str, Any]) -> Source: ...


class HealthRepository(Protocol):
    """来源健康检查存取端口。"""

    def add(self, check: HealthCheck) -> HealthCheck: ...

    def list_for_source(self, source_id: str, limit: int = 20) -> list[HealthCheck]: ...

    def last_success_at(self, source_id: str) -> int | None: ...


class MessageRepository(Protocol):
    """消息和版本存取端口。"""

    def upsert_message(self, message: Message) -> Message: ...

    def get_message(self, message_id: str) -> Message | None: ...

    def find_by_external_id(self, source_id: str, external_id: str) -> Message | None: ...

    def get_version(self, version_id: str) -> MessageVersion | None: ...

    def find_by_url(self, source_id: str, url: str) -> Message | None: ...

    def find_by_hash(self, source_id: str, content_hash: str) -> Message | None: ...

    def latest_version(self, message_id: str) -> MessageVersion | None: ...

    def add_version(self, version: MessageVersion) -> MessageVersion: ...

    def list_messages(
        self,
        query: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Message, MessageVersion | None]]: ...

    def versions(self, message_id: str) -> list[MessageVersion]: ...

    def count_for_source(self, source_id: str) -> int:
        """返回来源下的消息条数，用于删除前的影响说明。"""
        ...


class CollectionRunRepository(Protocol):
    """采集运行存取端口。"""

    def create(self, run: CollectionRun, tasks: list[Task]) -> CollectionRun: ...

    def record_result(self, run_id: str, source_id: str, counts: dict[str, int]) -> None: ...

    def get(self, run_id: str) -> CollectionRun | None: ...

    def latest_for_source(self, source_id: str) -> CollectionRun | None: ...

    def update_counts(self, run_id: str, changes: dict[str, Any]) -> CollectionRun: ...


class TranslationRepository(Protocol):
    """翻译结果存取端口。"""

    def get_for_version(
        self, version_id: str, prompt_version: str, model: str
    ) -> Translation | None: ...

    def list_for_version(self, version_id: str) -> list[Translation]: ...

    def save(self, translation: Translation, lease_token: str | None = None) -> Translation: ...

    def get(self, translation_id: str) -> Translation | None: ...


class TaskRepository(Protocol):
    """后台任务存取端口。"""

    def create(self, task: Task) -> Task: ...

    def get(self, task_id: str) -> Task | None: ...

    def get_by_idempotency(self, key: str) -> Task | None: ...

    def latest_for_version(self, version_id: str, task_type: TaskType) -> Task | None: ...

    def claim_next(self, task_types: list[str], now: int, lease_seconds: int) -> Task | None: ...

    def list(self, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Task]: ...

    def retry(self, task_id: str) -> Task: ...

    def renew(self, task_id: str, lease_token: str, now: int, lease_seconds: int) -> bool: ...

    def complete(
        self, task_id: str, output_version_id: str | None = None, lease_token: str | None = None
    ) -> Task: ...

    def fail(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        lease_token: str | None = None,
        retry_delay_seconds: int = 60,
    ) -> Task: ...

    def reclaim_expired(self, now: int) -> int: ...


class LLMConfigRepository(Protocol):
    """模型 provider、模型候选和 API Key 存取端口。"""

    def list_providers(self, enabled_only: bool = False) -> list[Provider]: ...

    def get_provider(self, provider_id: str) -> Provider | None: ...

    def create_provider(self, provider: Provider) -> Provider: ...

    def update_provider(self, provider_id: str, changes: dict[str, Any]) -> Provider: ...

    def list_models(self, provider_id: str, enabled_only: bool = False) -> list[ProviderModel]: ...

    def get_model(self, model_id: str) -> ProviderModel | None: ...

    def create_model(self, model: ProviderModel) -> ProviderModel: ...

    def update_model(self, model_id: str, changes: dict[str, Any]) -> ProviderModel: ...

    def list_keys(self, provider_id: str, enabled_only: bool = False) -> list[ProviderKey]: ...

    def create_key(self, key: ProviderKey) -> ProviderKey: ...

    def update_key(self, key_id: str, changes: dict[str, Any]) -> ProviderKey: ...

    def mark_key(self, key_id: str, status: str, cooldown_until: int | None) -> None: ...


class LLMCallRepository(Protocol):
    """模型调用审计存取端口。"""

    def add(self, call: LLMCall) -> LLMCall: ...


class EnrichmentRepository(Protocol):
    """内容加工结果存取端口。"""

    def get_for_version(
        self, version_id: str, prompt_version: str, model: str
    ) -> Enrichment | None: ...

    def list_for_version(self, version_id: str) -> list[Enrichment]: ...

    def save(self, enrichment: Enrichment, lease_token: str | None = None) -> Enrichment: ...

    def get(self, enrichment_id: str) -> Enrichment | None: ...


class VersionProcessing(Protocol):
    """版本入库后的自动处理入口：按来源配置和内容特征决定要建哪些任务。"""

    def enqueue(self, version: MessageVersion, source: Source) -> list[str]: ...


class PromptRepository(Protocol):
    """提示词版本存取端口。

    版本串已写进审计、结果与任务幂等键的版本不可变，只新增、启用与归档；
    未被引用过的版本可以就地改文本或删除，判定口径见 `usage`。
    """

    def list(self, task_kind: str | None = None) -> list[Prompt]: ...

    def get(self, prompt_id: str) -> Prompt | None: ...

    def by_version_string(self, version_string: str) -> Prompt | None:
        """按 `{prompt_key}-v{version}` 取版本。

        提示词入库之前的任务快照只带版本串、没有 prompt_id，执行与重试都靠它找回；
        版本串格式不符时返回 None，由调用方给出明确错误。
        """
        ...

    def active_for(self, task_kind: str) -> Prompt | None: ...

    def next_version(self, prompt_key: str) -> int: ...

    def create(self, prompt: Prompt) -> Prompt: ...

    def set_status(self, prompt_id: str, status: str) -> Prompt: ...

    def archive_kind(self, task_kind: str, keep_id: str) -> None: ...

    def usage(self, prompt_id: str, version_string: str) -> PromptUsage:
        """统计该版本在调用审计、结果、任务与任务分配中的引用次数。

        版本串必须精确匹配：`translation-v1` 不能命中 `translation-v10`，
        但试跑写下的 `{版本串}+prompt-test` 算使用，因为审计里已存在该版本串。
        任务快照按 id 统计，入库之前的老快照只有版本串，同样算引用。
        """
        ...

    def update_content(
        self, prompt_id: str, name: str, system_template: str, user_template: str, note: str
    ) -> Prompt:
        """就地改文本；不动状态、业务键与版本号，所以版本串保持稳定。"""
        ...

    def delete(self, prompt_id: str) -> None:
        """物理删除一条版本记录；调用方负责先确认它从未被引用过。"""
        ...


class TaskSettingRepository(Protocol):
    """任务分配存取端口：来源与行业分类对某类任务的覆盖配置。"""

    def list_for(self, scope: str, scope_id: str) -> list[TaskSetting]: ...

    def get(self, scope: str, scope_id: str, task_kind: str) -> TaskSetting | None: ...

    def replace(self, settings: list[TaskSetting]) -> list[TaskSetting]: ...

    def delete_for(self, scope: str, scope_id: str) -> None: ...
