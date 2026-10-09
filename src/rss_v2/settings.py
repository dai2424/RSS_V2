"""运行配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Settings(BaseSettings):
    """RSS v2 的环境配置。"""

    model_config = SettingsConfigDict(env_prefix="RSS_", extra="ignore")

    runtime_dir: Path = Path("runtime")
    database_path: Path = Path("runtime/db/rss_v2.db")
    migrations_dir: Path = Path(__file__).resolve().parents[2] / "migrations"
    frontend_dist: Path = Path("runtime/frontend")
    api_host: Literal["127.0.0.1", "::1", "localhost"] = "127.0.0.1"
    api_port: int = 8000
    rss_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    worker_lease_seconds: int = Field(default=300, gt=10, le=3600)
    worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    # 标题超过该字符数、或摘要与正文超过该字符数时才做内容加工，避免为短消息多花一次调用。
    llm_enrich_title_threshold: int = Field(default=40, ge=1, le=500)
    llm_enrich_text_threshold: int = Field(default=300, ge=1, le=20000)
    llm_retry_cooldown_seconds: int = Field(default=60, gt=0, le=3600)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """显式参数 > RSS_ 环境变量 > RSS_CONFIG 指定的无密钥 TOML。"""
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(
                settings_cls, toml_file=Path(os.getenv("RSS_CONFIG", "config/settings.toml"))
            ),
        )

    @model_validator(mode="after")
    def derive_paths(self) -> Settings:
        """只修改运行目录时，数据库自动跟随；显式数据库路径优先。"""
        if "database_path" not in self.model_fields_set:
            self.database_path = self.runtime_dir / "db" / "rss_v2.db"
        if "frontend_dist" not in self.model_fields_set:
            self.frontend_dist = self.runtime_dir / "frontend"
        return self

    def ensure_runtime_dirs(self) -> None:
        """创建运行时目录，不触碰源码目录。"""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
