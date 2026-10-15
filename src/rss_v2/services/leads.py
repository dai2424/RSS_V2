"""线索用例：一组同属一件事的消息版本。

这一层只做机械校验：线索名非空、成员是真实存在的消息、成员条数在界面上限之内、
同一条消息不会重复进入同一条线索（成员按版本存，重复加入会被忽略）。
"这些消息是不是同一件事"由人工判断或选题发现判定，服务层不猜。

至少一条消息即可成立：阶段 4 允许单篇有趣报道走完整写作流程，旧的"至少两条"
门槛会挡住单篇选题。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rss_v2.domain import DomainError, Lead, LeadDetail, LeadSummary
from rss_v2.domain.values import new_id, now
from rss_v2.ports import LeadRepository, MessageRepository

#: 线索名长度上限：列表与草稿标题都用它，过长只会在界面上被截断。
MAX_LEAD_TITLE = 80

#: 一条线索最多几条消息：草稿的输入长度与成本随成员数线性增长，超限时让人分批。
MAX_LEAD_MEMBERS = 20


@dataclass(slots=True)
class LeadService:
    """线索的创建、成员增删与查询。"""

    leads: LeadRepository  # 线索本体与成员
    messages: MessageRepository  # 校验消息存在并解析最新版本

    def create(
        self,
        title: str,
        message_ids: Sequence[str],
        note: str | None = None,
        kind: str = "manual",
    ) -> Lead:
        """按消息 id 建线索：成员取各自当前的最新版本，至少一条。"""

        cleaned = self._title(title)
        versions = self._resolve_versions(message_ids)
        if not versions:
            raise DomainError("lead_needs_members", "线索至少要有一条消息")
        if len(versions) > MAX_LEAD_MEMBERS:
            raise DomainError(
                "lead_members_limit",
                f"一条线索最多 {MAX_LEAD_MEMBERS} 条消息，请分批创建或先移除一些成员",
            )
        timestamp = now()
        return self.leads.create(
            Lead(
                id=new_id(),
                title=cleaned,
                note=(note or "").strip() or None,
                created_at=timestamp,
                updated_at=timestamp,
                kind=kind,
            ),
            versions,
        )

    def add_messages(self, lead_id: str, message_ids: Sequence[str]) -> tuple[Lead, int]:
        """把消息的最新版本加入线索；已在线索里的消息跳过，返回更新后的线索与新增条数。"""

        lead = self._require(lead_id)
        current = self.leads.members(lead_id)
        versions = self._resolve_versions(
            message_ids, skip_messages={m.message_id for m in current}
        )
        if len(current) + len(versions) > MAX_LEAD_MEMBERS:
            raise DomainError(
                "lead_members_limit",
                f"一条线索最多 {MAX_LEAD_MEMBERS} 条消息，请分批创建或先移除一些成员",
            )
        if versions:
            self.leads.add_members(lead_id, versions, now())
        return self.leads.get(lead_id) or lead, len(versions)

    def remove_member(self, lead_id: str, version_id: str) -> Lead:
        """移除一个成员；成员不存在时报错，避免界面以为删掉了。"""

        self._require(lead_id)
        if self.leads.remove_member(lead_id, version_id, now()) == 0:
            raise DomainError("lead_member_not_found", "这条消息不在这条线索里")
        updated = self.leads.get(lead_id)
        if updated is None:  # 并发删除：调用方拿到的是"线索已不存在"
            raise DomainError("lead_not_found", "线索不存在")
        return updated

    def list(
        self, limit: int = 50, offset: int = 0, kind: str | None = "manual"
    ) -> tuple[list[LeadSummary], int]:
        """线索列表与总数；默认只看人工线索，选题发现建立的线索另走发现界面。"""

        return self.leads.list(limit, offset, kind)

    def detail(self, lead_id: str) -> LeadDetail:
        """线索详情：本体与成员（按发布时间从早到晚）。"""

        lead = self._require(lead_id)
        return LeadDetail(lead=lead, members=tuple(self.leads.members(lead_id)))

    def delete(self, lead_id: str) -> int:
        """删除线索；成员随外键级联。"""

        if self.leads.delete(lead_id) == 0:
            raise DomainError("lead_not_found", "线索不存在")
        return 1

    def _title(self, title: str) -> str:
        cleaned = title.strip()
        if not cleaned:
            raise DomainError("lead_title_empty", "线索名不能为空")
        if len(cleaned) > MAX_LEAD_TITLE:
            raise DomainError("lead_title_too_long", f"线索名不超过 {MAX_LEAD_TITLE} 个字")
        return cleaned

    def _require(self, lead_id: str) -> Lead:
        lead = self.leads.get(lead_id)
        if lead is None:
            raise DomainError("lead_not_found", "线索不存在")
        return lead

    def _resolve_versions(
        self, message_ids: Sequence[str], skip_messages: set[str] | None = None
    ) -> list[str]:
        """消息 id → 最新版本 id：去重、跳过指定消息、校验消息与版本都存在。"""

        skip = skip_messages or set()
        versions: list[str] = []
        seen: set[str] = set()
        for message_id in message_ids:
            if message_id in seen or message_id in skip:
                continue
            seen.add(message_id)
            message = self.messages.get_message(message_id)
            if message is None:
                raise DomainError("message_not_found", f"消息不存在：{message_id}")
            version = self.messages.latest_version(message.id)
            if version is None:
                raise DomainError("message_empty", f"消息没有可引用的版本：{message_id}")
            versions.append(version.id)
        return versions
