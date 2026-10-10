"""删除操作的影响面与结果。

结果对象只描述"删了什么"，不承载业务规则；规则在服务与适配器里。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MessageDeletion:
    """一次消息删除的影响面：删掉多少条消息，以及随之清理的从属数据。"""

    messages: int  # 删除的消息数
    versions: int  # 随之删除的版本数
    translations: int  # 随之删除的译文数
    enrichments: int  # 随之删除的加工结果数（关键词随加工结果级联删除）
    tasks: int  # 一并删除的翻译与加工任务数


@dataclass(frozen=True, slots=True)
class TaskDeletion:
    """清理任务的结果：命中多少条、实际删掉多少条。"""

    candidates: int  # 命中的任务数（单条删除恒为 1）
    deleted: int  # 实际删除的任务数；状态守卫可能让并发场景下少删
