"""RSS 采集和消息版本用例。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rss_v2.domain import (
    CollectionRun,
    DomainError,
    FeedItem,
    HealthCheck,
    Message,
    MessageVersion,
    Source,
    Task,
    TaskStatus,
    TaskType,
)
from rss_v2.domain.values import new_id, normalize_url, now
from rss_v2.ports import (
    CollectionRunRepository,
    FeedClient,
    HealthRepository,
    MessageRepository,
    SourceRepository,
    TaskRepository,
    VersionProcessing,
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
    processing: VersionProcessing  # 新版本入库后的自动翻译与内容加工

    def collect(self, source_id: str, run_id: str | None = None) -> dict[str, int]:
        source = self.sources.get(source_id)
        if source is None:
            raise DomainError("source_not_found", "RSS 来源不存在")
        if not source.enabled:
            if run_id:
                self.runs.record_result(
                    run_id, source.id, {"created": 0, "updated": 0, "skipped": 0, "failed": 1}
                )
            raise DomainError("source_disabled", "RSS 来源已停用")
        checked_at = now()
        started = time.perf_counter()
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
                HealthCheck(
                    new_id(),
                    source.id,
                    checked_at,
                    int(snapshot.metadata.get("http_status", 200)),
                    int(
                        snapshot.metadata.get("duration_ms", (time.perf_counter() - started) * 1000)
                    ),
                    True,
                    len(snapshot.items),
                )
            )
            counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
            for item in snapshot.items:
                counts[self._store_item(source, item)] += 1
            if run_id:
                self.runs.record_result(run_id, source.id, counts)
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
                    exc.message if isinstance(exc, DomainError) else "采集发生内部错误",
                )
            )
            if run_id:
                self.runs.record_result(
                    run_id, source.id, {"created": 0, "updated": 0, "skipped": 0, "failed": 1}
                )
            raise

    def _store_item(self, source: Source, item: FeedItem) -> str:
        """三级精确去重，返回新增、更新或未变化的计数类别。"""
        item_url = normalize_url(item.url) if item.url else ""
        message = self.messages.find_by_external_id(source.id, item.external_id)
        if message is None and item_url:
            message = self.messages.find_by_url(source.id, item_url)
        if message is None:
            message = self.messages.find_by_hash(source.id, item.content_hash)
        if message is None:
            timestamp = now()
            message = self.messages.upsert_message(
                Message(new_id(), source.id, item.external_id, timestamp, timestamp)
            )
        latest = self.messages.latest_version(message.id)
        if latest is not None and latest.content_hash == item.content_hash:
            return "skipped"
        version = MessageVersion(
            id=new_id(),
            message_id=message.id,
            version_number=(latest.version_number + 1 if latest else 1),
            title=item.title,
            summary=item.summary,
            content=item.content,
            url=item_url,
            published_at=item.published_at + source.time_offset_minutes * 60
            if item.published_at is not None
            else None,
            collected_at=now(),
            language=source.language if source.language.value != "auto" else item.language,
            content_hash=item.content_hash,
        )
        self.messages.add_version(version)
        self.messages.upsert_message(
            Message(message.id, message.source_id, message.external_id, message.created_at, now())
        )
        # 只有新版本才需要处理；同内容重复采集已在上面按指纹跳过。
        self.processing.enqueue(version)
        return "updated" if latest else "created"

    def create_run(self, source_ids: list[str]) -> CollectionRun:
        if not source_ids:
            raise DomainError("no_sources", "至少选择一个 RSS 来源")
        source_ids = list(dict.fromkeys(source_ids))
        for source_id in source_ids:
            source = self.sources.get(source_id)
            if source is None:
                raise DomainError("source_not_found", f"RSS 来源不存在：{source_id}")
        run = CollectionRun(new_id(), source_ids, TaskStatus.QUEUED, now(), None)
        pending: list[Task] = []
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
            pending.append(task)
        return self.runs.create(run, pending)

    def task_ids_for_run(self, run: CollectionRun) -> list[str]:
        """返回本次采集运行创建的任务 ID。"""

        task_ids: list[str] = []
        for source_id in run.source_ids:
            task = self.tasks.get_by_idempotency(f"collect:{run.id}:{source_id}")
            if task is not None:
                task_ids.append(task.id)
        return task_ids

    def get_run(self, run_id: str) -> CollectionRun:
        """读取采集运行。"""
        run = self.runs.get(run_id)
        if run is None:
            raise DomainError("run_not_found", "采集运行不存在")
        return run

    def latest_for_source(self, source_id: str) -> CollectionRun | None:
        """查询来源最近的运行统计。"""
        return self.runs.latest_for_source(source_id)

    def refresh_run(self, run_id: str) -> None:
        """任务最终状态决定运行是否完成；自动重试期间仍为 running。"""
        run = self.get_run(run_id)
        tasks = [self.tasks.get(task_id) for task_id in self.task_ids_for_run(run)]
        done = len(tasks) == len(run.source_ids) and all(
            task is not None and task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}
            for task in tasks
        )
        status = (
            (TaskStatus.FAILED if run.failed_count else TaskStatus.SUCCEEDED)
            if done
            else TaskStatus.RUNNING
        )
        self.runs.update_counts(run_id, {"status": status, "completed_at": now() if done else None})
