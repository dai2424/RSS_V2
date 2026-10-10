"""业务使用的仓储端口。"""

from __future__ import annotations

from typing import Any, Protocol

from rss_v2.domain import (
    Category,
    CollectionRun,
    Enrichment,
    HealthCheck,
    KeywordEntry,
    KeywordOverview,
    LLMCall,
    MergePreview,
    MergeRecord,
    Message,
    MessageDeletion,
    MessageVersion,
    Prompt,
    PromptUsage,
    Provider,
    ProviderKey,
    ProviderModel,
    RelatedMessage,
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
        keyword: str | None = None,
        kind: str | None = None,
        since: int | None = None,
        until: int | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Message, MessageVersion | None]]:
        """按全文、来源、关键词与时间范围筛选，默认按发布时间从新到旧。

        keyword 走 enrichment_keywords 的匹配键，kind 只在该条件存在时生效；
        since/until 是 UTC 秒，比较 COALESCE(published_at, collected_at)；state 是处理状态，
        取值 untranslated/translated/unenriched/enriched/enrich_failed/pending。
        """
        ...

    def message_impact(self, message_ids: list[str]) -> MessageDeletion:
        """删除影响面预览：消息、版本、译文、加工结果与任务各有多少。"""
        ...

    def delete_messages(self, message_ids: list[str]) -> MessageDeletion:
        """删除这些消息及其从属数据，返回实际删掉的数量。"""
        ...

    def versions(self, message_id: str) -> list[MessageVersion]: ...

    def count_messages(
        self,
        query: str | None = None,
        source_id: str | None = None,
        keyword: str | None = None,
        kind: str | None = None,
        since: int | None = None,
        until: int | None = None,
        state: str | None = None,
    ) -> int:
        """与 list_messages 同一套过滤条件下的总数，供列表分页使用。"""
        ...

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

    def list(
        self,
        status: str | None = None,
        task_type: str | None = None,
        source_id: str | None = None,
        query: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        """按筛选条件分页查询任务。

        source_id 对采集任务看任务快照里的来源、对翻译与加工任务看消息所属来源；
        query 匹配消息标题或来源名；since/until 比较任务创建时间（UTC 秒）。
        """
        ...

    def count(
        self,
        status: str | None = None,
        task_type: str | None = None,
        source_id: str | None = None,
        query: str | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> int:
        """与 list 同一套过滤条件下的总数，供列表分页使用。"""
        ...

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

    def delete_finished(self, task_ids: list[str]) -> int:
        """删除已结束（成功或失败）的任务，返回实际删除条数。

        排队与运行中的任务不在这里删：正在跑的任务被删掉后，worker 回写时会取不到行。
        """
        ...

    def delete_failed(self) -> int:
        """删除全部失败任务，返回删除条数。"""
        ...

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


class KeywordRepository(Protocol):
    """关键词索引、相关消息与回填候选端口。"""

    def rebuild_index(self) -> int:
        """按已存的关键词快照重建关系表，返回写入行数；不调用模型。"""
        ...

    def related_messages(self, message_id: str, limit: int = 8) -> list[RelatedMessage]:
        """与指定消息共享关键词的其他消息，按共享实体优先、时间新近排序。"""
        ...

    def messages_missing_enrichment(self, source_id: str | None, limit: int) -> list[str]:
        """还没有成功加工结果的消息 id，按发布时间从新到旧。"""
        ...

    def vocabulary(
        self,
        query: str | None,
        kind: str | None,
        min_count: int,
        since: int | None,
        until: int | None,
        limit: int,
        offset: int,
    ) -> list[KeywordEntry]:
        """词表一页：按频次排序的规范词，含覆盖来源数与首末出现时间。

        min_count 用于折叠只出现一次的词；since/until 按最近出现时间过滤。
        """
        ...

    def count_vocabulary(
        self,
        query: str | None,
        kind: str | None,
        min_count: int,
        since: int | None,
        until: int | None,
    ) -> int:
        """与词表同一筛选条件的总数。"""
        ...

    def overview(self, days: int, top_sources: int) -> KeywordOverview:
        """概览：规模、类型构成、长尾、近 N 天趋势、来源分布与覆盖情况。"""
        ...

    def resolve_key(self, key: str) -> str:
        """把匹配键解析到规范词；不是别名时原样返回。"""
        ...

    def merge_preview(self, sources: list[str], target: str) -> MergePreview:
        """合并影响面预览：合并后的词频、受影响消息数与全部写法。"""
        ...

    def merge(self, sources: list[str], target: str) -> MergeRecord:
        """把若干规范词并入目标词，写别名表并留下可撤销的记录。"""
        ...

    def undo_merge(self, merge_id: str) -> int:
        """撤销一次合并，返回恢复的别名行数。"""
        ...

    def merges(self, limit: int = 20) -> list[MergeRecord]:
        """最近的合并记录，包含已撤销的那些。"""
        ...


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
