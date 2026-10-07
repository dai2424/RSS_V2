"""来源和行业分类用例。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from rss_v2.adapters.sqlite.common import new_id, now
from rss_v2.domain import Category, DomainError, HealthCheck, Source, SourceLanguage
from rss_v2.ports import CategoryRepository, FeedClient, HealthRepository, SourceRepository


def _slug(value: str) -> str:
    """生成可读的分类 slug。"""

    cleaned = "-".join(value.strip().lower().split())
    return cleaned or new_id().replace("-", "")[:12]


@dataclass(slots=True)
class CategoryService:
    """行业分类用例。"""

    categories: CategoryRepository

    def list(self) -> list[Category]:
        return self.categories.list()

    def create(self, name: str, slug: str | None = None) -> Category:
        normalized_name = name.strip()
        if not normalized_name:
            raise DomainError("invalid_category", "分类名称不能为空")
        return self.categories.create(normalized_name, _slug(slug or normalized_name))


@dataclass(slots=True)
class SourceService:
    """RSS 来源管理和健康测试用例。"""

    sources: SourceRepository
    categories: CategoryRepository
    health: HealthRepository
    feed_client: FeedClient
    timeout_seconds: float

    def list(
        self,
        query: str | None = None,
        category_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Source]:
        return self.sources.list(query, category_id, enabled, limit, offset)

    def get(self, source_id: str) -> Source:
        source = self.sources.get(source_id)
        if source is None:
            raise DomainError("source_not_found", "RSS 来源不存在")
        return source

    def create(
        self,
        name: str,
        url: str,
        platform: str,
        language: SourceLanguage,
        category_id: str,
        time_offset_minutes: int,
    ) -> Source:
        self._validate_url(url)
        if not name.strip():
            raise DomainError("invalid_source", "来源名称不能为空")
        category = self.categories.get(category_id)
        if category is None or not category.is_active:
            raise DomainError("invalid_category", "行业分类不存在或已停用")
        timestamp = now()
        source = Source(
            id=new_id(),
            name=name.strip(),
            url=url.strip(),
            platform=platform.strip(),
            language=language,
            category_id=category_id,
            enabled=True,
            time_offset_minutes=time_offset_minutes,
            created_at=timestamp,
            updated_at=timestamp,
        )
        try:
            return self.sources.create(source)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise DomainError("source_exists", "RSS 地址已经存在") from exc
            raise

    def update(self, source_id: str, changes: dict[str, object]) -> Source:
        source = self.get(source_id)
        if "url" in changes and isinstance(changes["url"], str):
            self._validate_url(changes["url"])
        if "name" in changes and isinstance(changes["name"], str) and not changes["name"].strip():
            raise DomainError("invalid_source", "来源名称不能为空")
        if "category_id" in changes:
            category_id = str(changes["category_id"])
            category = self.categories.get(category_id)
            if category is None or not category.is_active:
                raise DomainError("invalid_category", "行业分类不存在或已停用")
        if "language" in changes and isinstance(changes["language"], str):
            changes["language"] = SourceLanguage(changes["language"])
        if not changes:
            return source
        try:
            return self.sources.update(source_id, changes)
        except KeyError as exc:
            raise DomainError("source_not_found", "RSS 来源不存在") from exc
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise DomainError("source_exists", "RSS 地址已经存在") from exc
            raise

    def test(self, source_id: str) -> tuple[Source, HealthCheck]:
        source = self.get(source_id)
        checked_at = now()
        try:
            snapshot = self.feed_client.fetch(source.url, self.timeout_seconds)
            updated = self.sources.save_feed_metadata(
                source.id,
                {
                    "title": snapshot.title,
                    "link": snapshot.link,
                    "description": snapshot.description,
                    "language": snapshot.language,
                    "author": snapshot.author,
                    "updated_at": snapshot.updated_at,
                    "extra": snapshot.metadata,
                },
            )
            check = HealthCheck(
                id=new_id(),
                source_id=source.id,
                checked_at=checked_at,
                http_status=200,
                latency_ms=None,
                parse_success=True,
                entry_count=len(snapshot.items),
            )
            return updated, self.health.add(check)
        except DomainError:
            raise
        except Exception as exc:
            check = HealthCheck(
                id=new_id(),
                source_id=source.id,
                checked_at=checked_at,
                http_status=getattr(exc, "status_code", None),
                latency_ms=None,
                parse_success=False,
                entry_count=0,
                error_code=getattr(exc, "code", "feed_error"),
                error_message=str(exc)[:500],
            )
            self.health.add(check)
            raise DomainError("feed_unavailable", f"RSS 检查失败：{check.error_message}") from exc

    def health_history(self, source_id: str, limit: int = 20) -> list[HealthCheck]:
        self.get(source_id)
        return self.health.list_for_source(source_id, limit)

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DomainError("invalid_url", "RSS 地址必须是有效的 HTTP(S) 地址")
