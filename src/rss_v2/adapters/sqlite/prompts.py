"""llm_prompts 与 task_settings SQLite 仓储。"""

from __future__ import annotations

import sqlite3

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import Prompt, PromptUsage, TaskSetting
from rss_v2.domain.values import now


def _prompt(row: sqlite3.Row) -> Prompt:
    return Prompt(
        id=row["id"],
        task_kind=row["task_kind"],
        prompt_key=row["prompt_key"],
        version=row["version"],
        name=row["name"],
        status=row["status"],
        system_template=row["system_template"],
        user_template=row["user_template"],
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _setting(row: sqlite3.Row) -> TaskSetting:
    enabled = row["enabled"]
    return TaskSetting(
        scope=row["scope"],
        scope_id=row["scope_id"],
        task_kind=row["task_kind"],
        enabled=None if enabled is None else bool(enabled),
        prompt_id=row["prompt_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _count(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    """单值引用计数。"""

    row = connection.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


class SQLitePromptRepository:
    """提示词版本仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def list(self, task_kind: str | None = None) -> list[Prompt]:
        connection = self.database.connect()
        try:
            if task_kind is None:
                rows = connection.execute(
                    "SELECT * FROM llm_prompts ORDER BY task_kind, prompt_key, version DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM llm_prompts WHERE task_kind=? ORDER BY prompt_key, version DESC",
                    (task_kind,),
                ).fetchall()
            return [_prompt(row) for row in rows]
        finally:
            connection.close()

    def get(self, prompt_id: str) -> Prompt | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM llm_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
            return _prompt(row) if row else None
        finally:
            connection.close()

    def by_version_string(self, version_string: str) -> Prompt | None:
        """按版本串取版本；格式不符直接返回 None，不做猜测。"""

        parsed = Prompt.split_version_string(version_string)
        if parsed is None:
            return None
        prompt_key, version = parsed
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM llm_prompts WHERE prompt_key=? AND version=?",
                (prompt_key, version),
            ).fetchone()
            return _prompt(row) if row else None
        finally:
            connection.close()

    def active_for(self, task_kind: str) -> Prompt | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM llm_prompts WHERE task_kind=? AND status='active'", (task_kind,)
            ).fetchone()
            return _prompt(row) if row else None
        finally:
            connection.close()

    def next_version(self, prompt_key: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT coalesce(max(version),0)+1 FROM llm_prompts WHERE prompt_key=?",
                (prompt_key,),
            ).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def create(self, prompt: Prompt) -> Prompt:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO llm_prompts(id,task_kind,prompt_key,version,name,status,
                    system_template,user_template,note,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    prompt.id,
                    prompt.task_kind,
                    prompt.prompt_key,
                    prompt.version,
                    prompt.name,
                    prompt.status,
                    prompt.system_template,
                    prompt.user_template,
                    prompt.note,
                    prompt.created_at,
                    prompt.updated_at,
                ),
            )
        return prompt

    def set_status(self, prompt_id: str, status: str) -> Prompt:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE llm_prompts SET status=?, updated_at=? WHERE id=?",
                (status, now(), prompt_id),
            )
        result = self.get(prompt_id)
        if result is None:
            raise RuntimeError("提示词状态更新后无法读取")
        return result

    def archive_kind(self, task_kind: str, keep_id: str) -> None:
        """启用某个版本时，同任务类型的其它启用版本让位。"""

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE llm_prompts SET status='archived', updated_at=?"
                " WHERE task_kind=? AND status='active' AND id<>?",
                (now(), task_kind, keep_id),
            )

    def usage(self, prompt_id: str, version_string: str) -> PromptUsage:
        """统计该版本在调用审计、结果、任务与任务分配中的引用次数。

        版本串用等值比较而不是 LIKE 前缀：`translation-v1` 加通配符会误命中 `translation-v10`。
        试跑写下的 `{版本串}+prompt-test` 也算使用，因为审计里已经存在该版本串。
        """

        connection = self.database.connect()
        try:
            calls = _count(
                connection,
                "SELECT count(*) FROM llm_calls"
                " WHERE prompt_version=? OR prompt_version=? || '+prompt-test'",
                (version_string, version_string),
            )
            results = _count(
                connection,
                "SELECT count(*) FROM translations WHERE prompt_version=?",
                (version_string,),
            ) + _count(
                connection,
                "SELECT count(*) FROM message_enrichments WHERE prompt_version=?",
                (version_string,),
            )
            # 提示词入库之前的任务快照没有 prompt_id，只有版本串；那种引用同样
            # 说明这条记录被用过，既影响能否删除，也影响升级后能否继续执行。
            tasks = _count(
                connection,
                "SELECT count(*) FROM tasks"
                " WHERE json_extract(payload_json,'$.prompt_id')=?"
                " OR (json_extract(payload_json,'$.prompt_id') IS NULL"
                " AND json_extract(payload_json,'$.prompt_version')=?)",
                (prompt_id, version_string),
            )
            bindings = _count(
                connection, "SELECT count(*) FROM task_settings WHERE prompt_id=?", (prompt_id,)
            )
        finally:
            connection.close()
        return PromptUsage(calls=calls, results=results, tasks=tasks, bindings=bindings)

    def update_content(
        self, prompt_id: str, name: str, system_template: str, user_template: str, note: str
    ) -> Prompt:
        """就地改文本；状态、业务键与版本号保持原样，版本串因此不会漂移。"""

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE llm_prompts SET name=?, system_template=?, user_template=?, note=?,"
                " updated_at=? WHERE id=?",
                (name, system_template, user_template, note, now(), prompt_id),
            )
        result = self.get(prompt_id)
        if result is None:
            raise RuntimeError("提示词内容更新后无法读取")
        return result

    def delete(self, prompt_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM llm_prompts WHERE id=?", (prompt_id,))


class SQLiteTaskSettingRepository:
    """任务分配仓储；enabled 与 prompt_id 为空表示继承上一层。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def list_for(self, scope: str, scope_id: str) -> list[TaskSetting]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM task_settings WHERE scope=? AND scope_id=? ORDER BY task_kind",
                (scope, scope_id),
            ).fetchall()
            return [_setting(row) for row in rows]
        finally:
            connection.close()

    def get(self, scope: str, scope_id: str, task_kind: str) -> TaskSetting | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM task_settings WHERE scope=? AND scope_id=? AND task_kind=?",
                (scope, scope_id, task_kind),
            ).fetchone()
            return _setting(row) if row else None
        finally:
            connection.close()

    def replace(self, settings: list[TaskSetting]) -> list[TaskSetting]:
        """整组覆盖同一作用域的任务分配；空值表示回到继承。"""

        if not settings:
            return []
        scope, scope_id = settings[0].scope, settings[0].scope_id
        for item in settings:
            if item.scope != scope or item.scope_id != scope_id:
                raise ValueError("一次只能替换同一作用域的任务分配")
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM task_settings WHERE scope=? AND scope_id=?", (scope, scope_id)
            )
            for item in settings:
                connection.execute(
                    """
                    INSERT INTO task_settings(scope,scope_id,task_kind,enabled,prompt_id,
                        created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        item.scope,
                        item.scope_id,
                        item.task_kind,
                        None if item.enabled is None else int(item.enabled),
                        item.prompt_id,
                        item.created_at,
                        item.updated_at,
                    ),
                )
        return self.list_for(scope, scope_id)

    def delete_for(self, scope: str, scope_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM task_settings WHERE scope=? AND scope_id=?", (scope, scope_id)
            )
