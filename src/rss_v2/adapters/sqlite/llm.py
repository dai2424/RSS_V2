"""LLM provider、模型候选、密钥引用和调用审计 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite.common import dumps, loads, now
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import LLMCall, Provider, ProviderKey, ProviderModel


def _provider(row: sqlite3.Row) -> Provider:
    return Provider(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        enabled=bool(row["enabled"]),
        timeout_seconds=row["timeout_seconds"],
        session_header_name=row["session_header_name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        extra_headers={
            str(key): str(value) for key, value in loads(row["extra_headers_json"]).items()
        },
    )


def _model(row: sqlite3.Row) -> ProviderModel:
    return ProviderModel(
        id=row["id"],
        provider_id=row["provider_id"],
        model=row["model"],
        enabled=bool(row["enabled"]),
        priority=row["priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _key(row: sqlite3.Row) -> ProviderKey:
    return ProviderKey(
        id=row["id"],
        provider_id=row["provider_id"],
        secret=row["secret"],
        priority=row["priority"],
        enabled=bool(row["enabled"]),
        cooldown_until=row["cooldown_until"],
        last_status=row["last_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SQLiteLLMConfigRepository:
    """模型配置仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def list_providers(self, enabled_only: bool = False) -> list[Provider]:
        connection = self.database.connect()
        try:
            where = " WHERE enabled = 1" if enabled_only else ""
            rows = connection.execute(
                f"SELECT * FROM llm_providers{where} ORDER BY created_at, id"
            ).fetchall()
            return [_provider(row) for row in rows]
        finally:
            connection.close()

    def get_provider(self, provider_id: str) -> Provider | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
            ).fetchone()
            return _provider(row) if row else None
        finally:
            connection.close()

    def create_provider(self, provider: Provider) -> Provider:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO llm_providers(id,name,base_url,enabled,timeout_seconds,session_header_name,created_at,updated_at,extra_headers_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    provider.id,
                    provider.name,
                    provider.base_url,
                    int(provider.enabled),
                    provider.timeout_seconds,
                    provider.session_header_name,
                    provider.created_at,
                    provider.updated_at,
                    dumps(provider.extra_headers),
                ),
            )
        return provider

    def update_provider(self, provider_id: str, changes: dict[str, Any]) -> Provider:
        allowed = {
            "name",
            "base_url",
            "enabled",
            "timeout_seconds",
            "session_header_name",
        }
        if "extra_headers" in changes:
            changes = {**changes, "extra_headers_json": dumps(changes["extra_headers"])}
            allowed.add("extra_headers_json")
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            result = self.get_provider(provider_id)
            if result is None:
                raise KeyError(provider_id)
            return result
        values["updated_at"] = now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE llm_providers SET {assignments} WHERE id=?",
                (*values.values(), provider_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(provider_id)
        result = self.get_provider(provider_id)
        if result is None:
            raise KeyError(provider_id)
        return result

    def list_models(self, provider_id: str, enabled_only: bool = False) -> list[ProviderModel]:
        connection = self.database.connect()
        try:
            where = " AND enabled=1" if enabled_only else ""
            rows = connection.execute(
                f"SELECT * FROM llm_provider_models WHERE provider_id=?{where} ORDER BY priority, model",
                (provider_id,),
            ).fetchall()
            return [_model(row) for row in rows]
        finally:
            connection.close()

    def get_model(self, model_id: str) -> ProviderModel | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM llm_provider_models WHERE id=?", (model_id,)
            ).fetchone()
            return _model(row) if row else None
        finally:
            connection.close()

    def create_model(self, model: ProviderModel) -> ProviderModel:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO llm_provider_models(id,provider_id,model,enabled,priority,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    model.id,
                    model.provider_id,
                    model.model,
                    int(model.enabled),
                    model.priority,
                    model.created_at,
                    model.updated_at,
                ),
            )
        return model

    def update_model(self, model_id: str, changes: dict[str, Any]) -> ProviderModel:
        allowed = {"model", "enabled", "priority"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            result = self.get_model(model_id)
            if result is None:
                raise KeyError(model_id)
            return result
        values["updated_at"] = now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE llm_provider_models SET {assignments} WHERE id=?",
                (*values.values(), model_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(model_id)
        result = self.get_model(model_id)
        if result is None:
            raise KeyError(model_id)
        return result

    def list_keys(self, provider_id: str, enabled_only: bool = False) -> list[ProviderKey]:
        connection = self.database.connect()
        try:
            where = " AND enabled=1" if enabled_only else ""
            rows = connection.execute(
                f"SELECT * FROM llm_provider_keys WHERE provider_id=?{where} ORDER BY priority, created_at, id",
                (provider_id,),
            ).fetchall()
            return [_key(row) for row in rows]
        finally:
            connection.close()

    def create_key(self, key: ProviderKey) -> ProviderKey:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO llm_provider_keys(id,provider_id,secret,priority,enabled,cooldown_until,last_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    key.id,
                    key.provider_id,
                    key.secret,
                    key.priority,
                    int(key.enabled),
                    key.cooldown_until,
                    key.last_status,
                    key.created_at,
                    key.updated_at,
                ),
            )
        return key

    def update_key(self, key_id: str, changes: dict[str, Any]) -> ProviderKey:
        allowed = {"secret", "priority", "enabled", "cooldown_until", "last_status"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            result = self._get_key(key_id)
            if result is None:
                raise KeyError(key_id)
            return result
        values["updated_at"] = now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE llm_provider_keys SET {assignments} WHERE id=?", (*values.values(), key_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(key_id)
        result = self._get_key(key_id)
        if result is None:
            raise KeyError(key_id)
        return result

    def _get_key(self, key_id: str) -> ProviderKey | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM llm_provider_keys WHERE id=?", (key_id,)
            ).fetchone()
            return _key(row) if row else None
        finally:
            connection.close()

    def mark_key(self, key_id: str, status: str, cooldown_until: int | None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE llm_provider_keys SET last_status=?, cooldown_until=?, updated_at=? WHERE id=?",
                (status, cooldown_until, now(), key_id),
            )


class SQLiteLLMCallRepository:
    """模型调用审计仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def add(self, call: LLMCall) -> LLMCall:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO llm_calls(id,task_id,provider_id,key_masked,model,prompt_version,input_hash,duration_ms,token_usage_json,status,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    call.id,
                    call.task_id,
                    call.provider_id,
                    call.key_masked,
                    call.model,
                    call.prompt_version,
                    call.input_hash,
                    call.duration_ms,
                    dumps(call.token_usage),
                    call.status,
                    call.error_code,
                    call.created_at,
                ),
            )
        return call
