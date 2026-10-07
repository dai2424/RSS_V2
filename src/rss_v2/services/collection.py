"""RSS 采集和消息版本用例。"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.adapters.sqlite.common import new_id, now
from rss_v2.domain import (
    CollectionRun,
    DomainError,
    HealthCheck,
    Message,
    MessageVersion,
    Task,
    TaskStatus,
    TaskType,
)
from rss_v2.ports import (
    CollectionRunRepository,
    FeedClient,
    HealthRepository,
    MessageRepository,
    SourceRepository,
    TaskRepository,
)


@dataclass(slots=True)
class CollectionService:
    """执行单个来源的 RSS 采集。"""

    sources: SourceRepository
    health: HealthRepository
    messages: MessageRepository
    runs: CollectionRunRepository
    feed_client: FeedClient
    timeout_seconds: float
    tasks: TaskRepository

    def collect(self, source_id: str, run_id: str | None = None) -> dict[str, int]:
        source = self.sources.get(source_id)
        if source is None:
            raise DomainError("source_not_found", "RSS 来源不存在")
        if not source.enabled:
            raise DomainError("source_disabled", "RSS 来源已停用")
        checked_at = now()
        try:
            snapshot = self.feed_client.fetch(source.url, self.timeout_seconds)
            self.sources.save_feed_metadata(
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
            self.health.add(
                HealthCheck(new_id(), source.id, checked_at, 200, None, True, len(snapshot.items))
            )
            counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
            for item in snapshot.items:
                message = self.messages.find_by_external_id(source.id, item.external_id)
                if message is None:
                    timestamp = now()
                    message = self.messages.upsert_message(
                        Message(new_id(), source.id, item.external_id, timestamp, timestamp)
                    )
                latest = self.messages.latest_version(message.id)
                if latest is not None and latest.content_hash == item.content_hash:
                    counts["skipped"] += 1
                    continue
                version = MessageVersion(
                    id=new_id(),
                    message_id=message.id,
                    version_number=(latest.version_number + 1 if latest else 1),
                    title=item.title,
                    summary=item.summary,
                    content=item.content,
                    url=item.url,
                    published_at=item.published_at,
                    collected_at=now(),
                    language=item.language,
                    content_hash=item.content_hash,
                )
                self.messages.add_version(version)
                counts["updated" if latest else "created"] += 1
            if run_id:
                self.runs.update_counts(
                    run_id,
                    {
                        "status": TaskStatus.SUCCEEDED,
                        "completed_at": now(),
                        **{f"{key}_count": value for key, value in counts.items()},
                    },
                )
            return counts
        except Exception as exc:
            self.health.add(
                HealthCheck(
                    new_id(),
                    source.id,
                    checked_at,
                    getattr(exc, "status_code", None),
                    None,
                    False,
                    0,
                    getattr(exc, "code", "collection_failed"),
                    str(exc)[:500],
                )
            )
            if run_id:
                self.runs.update_counts(
                    run_id,
                    {"status": TaskStatus.FAILED, "completed_at": now(), "failed_count": 1},
                )
            raise

    def create_run(self, source_ids: list[str]) -> CollectionRun:
        if not source_ids:
            raise DomainError("no_sources", "至少选择一个 RSS 来源")
        for source_id in source_ids:
            source = self.sources.get(source_id)
            if source is None:
                raise DomainError("source_not_found", f"RSS 来源不存在：{source_id}")
        run = CollectionRun(new_id(), source_ids, TaskStatus.QUEUED, now(), None)
        created_run = self.runs.create(run)
        for source_id in source_ids:
            task = Task(
                id=new_id(),
                task_type=TaskType.COLLECT_SOURCE,
                idempotency_key=f"collect:{run.id}:{source_id}",
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=None,
                output_version_id=None,
                payload={"source_id": source_id, "run_id": run.id},
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
            self.tasks.create(task)
        return created_run

    def task_ids_for_run(self, run: CollectionRun) -> list[str]:
        """返回本次采集运行创建的任务 ID。"""

        task_ids: list[str] = []
        for source_id in run.source_ids:
            task = self.tasks.get_by_idempotency(f"collect:{run.id}:{source_id}")
            if task is not None:
                task_ids.append(task.id)
        return task_ids
