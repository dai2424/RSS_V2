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
    # 加工前注入词表的条数上限；0 表示关闭注入（仍按原提示词加工，不做复用判定）。
    llm_vocabulary_limit: int = Field(default=150, ge=0, le=1000)
    # "近期新增"一段的时间窗：列出最近这些天首次出现、且没进 Top N 的写法。
    llm_vocabulary_recent_days: int = Field(default=7, ge=0, le=90)
    # 热词口径：只有落在最近这些天内、且出现次数达到门槛的词才算"热词"，用于注入的热词段、
    # 词表页默认视图与常驻推荐；常驻词与注入的"近期新增"段不受这两个限制。
    # 合并建议仍按全量滚动扫描。
    keyword_hot_days: int = Field(default=7, ge=1, le=90)
    keyword_hot_min_mentions: int = Field(default=5, ge=1, le=100)
    # 合并建议每批交给模型的词数。实测每词约 1.4 秒、250 输出 token（含模型思考），
    # 因此批量受"Provider 超时"与"单次输出上限"约束，而不是输入上下文：默认 20 适配
    # 默认的 60 秒超时；把 Provider 超时调大后可以加大到 60。改这个值会让已生成的批次
    # 与词表偏移对不上，需要从头再走一遍（已建议过的组仍按组指纹去重）。
    llm_suggestion_batch_size: int = Field(default=20, ge=2, le=100)

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
