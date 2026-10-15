"""SQLite 仓储组合。"""

from rss_v2.adapters.sqlite.catalog import (
    SQLiteCategoryRepository,
    SQLiteHealthRepository,
    SQLiteSourceRepository,
)
from rss_v2.adapters.sqlite.collections import SQLiteCollectionRunRepository
from rss_v2.adapters.sqlite.discovery import (
    SQLiteDiscoveryRepository,
    SQLiteDiscoveryScopeReader,
    SQLiteWritingProjectRepository,
)
from rss_v2.adapters.sqlite.leads import SQLiteLeadRepository
from rss_v2.adapters.sqlite.llm import SQLiteLLMCallRepository, SQLiteLLMConfigRepository
from rss_v2.adapters.sqlite.messages import SQLiteMessageRepository
from rss_v2.adapters.sqlite.tasks import SQLiteTaskRepository
from rss_v2.adapters.sqlite.translations import SQLiteTranslationRepository

__all__ = [
    "SQLiteLeadRepository",
    "SQLiteCategoryRepository",
    "SQLiteCollectionRunRepository",
    "SQLiteDiscoveryRepository",
    "SQLiteDiscoveryScopeReader",
    "SQLiteHealthRepository",
    "SQLiteLLMCallRepository",
    "SQLiteLLMConfigRepository",
    "SQLiteMessageRepository",
    "SQLiteSourceRepository",
    "SQLiteTaskRepository",
    "SQLiteTranslationRepository",
    "SQLiteWritingProjectRepository",
]
