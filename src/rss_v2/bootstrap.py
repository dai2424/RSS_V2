"""生产依赖组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rss_v2.adapters.llm.anthropic_messages import AnthropicMessagesProvider
from rss_v2.adapters.llm.openai_compatible import OpenAICompatibleProvider
from rss_v2.adapters.llm.router import ProtocolRouter
from rss_v2.adapters.rss.feedparser_client import HTTPXFeedClient
from rss_v2.adapters.sqlite.collections import SQLiteCollectionRunRepository
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.enrichments import SQLiteEnrichmentRepository
from rss_v2.adapters.sqlite.keywords import SQLiteKeywordRepository
from rss_v2.adapters.sqlite.llm import SQLiteLLMCallRepository, SQLiteLLMConfigRepository
from rss_v2.adapters.sqlite.messages import SQLiteMessageRepository
from rss_v2.adapters.sqlite.migrations import MigrationRunner
from rss_v2.adapters.sqlite.prompts import (
    SQLitePromptRepository,
    SQLiteTaskSettingRepository,
)
from rss_v2.adapters.sqlite.repositories import (
    SQLiteCategoryRepository,
    SQLiteHealthRepository,
    SQLiteSourceRepository,
    SQLiteTaskRepository,
)
from rss_v2.adapters.sqlite.translations import SQLiteTranslationRepository
from rss_v2.adapters.sqlite.v1_import import SQLiteV1Importer
from rss_v2.domain import LLMProtocol, Provider
from rss_v2.services.collection import CollectionService
from rss_v2.services.enrichment import EnrichmentService
from rss_v2.services.keywords import KeywordService
from rss_v2.services.llm_failover import LLMFailover
from rss_v2.services.messages import MessageService
from rss_v2.services.processing import ProcessingService
from rss_v2.services.prompt_resolver import PromptResolver
from rss_v2.services.prompts import PromptService
from rss_v2.services.providers import ProviderService
from rss_v2.services.sources import CategoryService, SourceService
from rss_v2.services.task_settings import TaskSettingService
from rss_v2.services.tasks import TaskService
from rss_v2.services.translation import TranslationService
from rss_v2.settings import Settings


@dataclass(slots=True)
class Container:
    """API、worker 和 CLI 共用的具体依赖容器。"""

    settings: Settings
    database: SQLiteDatabase
    categories: SQLiteCategoryRepository
    sources: SQLiteSourceRepository
    health: SQLiteHealthRepository
    messages: SQLiteMessageRepository
    runs: SQLiteCollectionRunRepository
    tasks: SQLiteTaskRepository
    translations: SQLiteTranslationRepository
    enrichments: SQLiteEnrichmentRepository
    keywords: SQLiteKeywordRepository
    llm_config: SQLiteLLMConfigRepository
    llm_calls: SQLiteLLMCallRepository
    prompts: SQLitePromptRepository
    task_settings: SQLiteTaskSettingRepository
    category_service: CategoryService
    source_service: SourceService
    collection_service: CollectionService
    translation_service: TranslationService
    enrichment_service: EnrichmentService
    processing_service: ProcessingService
    prompt_service: PromptService
    task_setting_service: TaskSettingService
    message_service: MessageService
    keyword_service: KeywordService
    task_service: TaskService
    provider_service: ProviderService

    def migrate(self) -> list[str]:
        """CLI 通过组合根执行迁移。"""
        return MigrationRunner(self.database, self.settings.migrations_dir).run()

    def backup(self, destination: Path) -> None:
        """CLI 通过组合根执行可验证的在线备份。"""
        self.database.backup(destination)

    def import_v1(self, source_path: Path, apply: bool = False) -> dict[str, Any]:
        """CLI 通过组合根预览或应用 v1 RSS 导入；写入前自动备份。"""
        return SQLiteV1Importer(
            self.database, self.settings.migrations_dir, self.settings.runtime_dir
        ).run(source_path, apply)


def build_container(settings: Settings | None = None, migrate: bool = True) -> Container:
    """创建完整依赖图，业务模块不自行实例化具体适配器。"""

    actual_settings = settings or Settings()
    actual_settings.ensure_runtime_dirs()
    database = SQLiteDatabase(actual_settings.database_path)
    if migrate:
        MigrationRunner(database, actual_settings.migrations_dir).run()
    categories = SQLiteCategoryRepository(database)
    sources = SQLiteSourceRepository(database)
    health = SQLiteHealthRepository(database)
    messages = SQLiteMessageRepository(database)
    runs = SQLiteCollectionRunRepository(database)
    tasks = SQLiteTaskRepository(database)
    translations = SQLiteTranslationRepository(database)
    enrichments = SQLiteEnrichmentRepository(database)
    keywords = SQLiteKeywordRepository(database)
    llm_config = SQLiteLLMConfigRepository(database)
    llm_calls = SQLiteLLMCallRepository(database)
    prompts = SQLitePromptRepository(database)
    task_settings = SQLiteTaskSettingRepository(database)
    feed_client = HTTPXFeedClient()
    # 协议实现只在组合根注册；服务层拿到的始终是 LLMProvider 端口。
    llm_provider = ProtocolRouter(
        {
            LLMProtocol.CHAT_COMPLETIONS.value: OpenAICompatibleProvider(),
            LLMProtocol.ANTHROPIC_MESSAGES.value: AnthropicMessagesProvider(),
        }
    )
    failover = LLMFailover(llm_config, llm_calls, tasks)
    resolver = PromptResolver(prompts, task_settings, sources)
    category_service = CategoryService(categories)
    source_service = SourceService(
        sources, categories, health, feed_client, actual_settings.rss_timeout_seconds
    )
    translation_service = TranslationService(
        messages,
        translations,
        tasks,
        llm_config,
        llm_provider,
        failover,
        resolver,
        actual_settings.llm_retry_cooldown_seconds,
    )
    enrichment_service = EnrichmentService(
        messages,
        enrichments,
        tasks,
        llm_provider,
        failover,
        resolver,
        actual_settings.llm_retry_cooldown_seconds,
    )
    processing_service = ProcessingService(
        translation_service.create_task_for_version,
        enrichment_service.create_task_for_version,
        resolver,
        actual_settings.llm_enrich_title_threshold,
        actual_settings.llm_enrich_text_threshold,
    )
    collection_service = CollectionService(
        sources,
        health,
        messages,
        runs,
        feed_client,
        actual_settings.rss_timeout_seconds,
        tasks,
        processing_service,
    )
    prompt_service = PromptService(
        prompts, llm_provider, failover, actual_settings.llm_retry_cooldown_seconds
    )
    task_setting_service = TaskSettingService(task_settings, resolver)
    message_service = MessageService(messages, translations, enrichments)
    keyword_service = KeywordService(keywords, enrichment_service.create_task)
    task_service = TaskService(tasks, messages, sources)
    provider_service = ProviderService(llm_config)
    return Container(
        actual_settings,
        database,
        categories,
        sources,
        health,
        messages,
        runs,
        tasks,
        translations,
        enrichments,
        keywords,
        llm_config,
        llm_calls,
        prompts,
        task_settings,
        category_service,
        source_service,
        collection_service,
        translation_service,
        enrichment_service,
        processing_service,
        prompt_service,
        task_setting_service,
        message_service,
        keyword_service,
        task_service,
        provider_service,
    )


def ensure_provider(container: Container, provider: Provider) -> Provider:
    """为 CLI 或开发环境写入一个非敏感 provider 配置。"""

    existing = container.llm_config.get_provider(provider.id)
    if existing is not None:
        return existing
    return container.llm_config.create_provider(provider)
