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
    # 选题发现（阶段 4）：默认窗口、分批与上限。窗口内消息超过上限时返回明确错误并
    # 提示缩小范围，不做静默截断；分批大小决定单次模型调用的输入长度与调用次数。
    # 实测参考：7 天窗口当前 89 条 → 3 批；上限 400 条约 10 批。
    discovery_window_days: int = Field(default=7, ge=1, le=90)
    discovery_max_window_days: int = Field(default=90, ge=1, le=365)
    discovery_max_window_messages: int = Field(default=400, ge=10, le=5000)
    discovery_batch_size: int = Field(default=40, ge=5, le=200)
    discovery_batch_chars: int = Field(default=60000, ge=5000, le=200000)
    discovery_max_members: int = Field(default=20, ge=1, le=100)
    # 单次发现的候选上限。实测 7 天窗口（89 条消息）模型给出约 65 个候选，
    # 默认 50 保留大部分可选题材；被截断的数量在批次上显示，不静默丢弃。
    discovery_max_candidates: int = Field(default=50, ge=1, le=200)
    # 单次模型调用的输出上限：写不下时会自动把这一批对半拆开重试（最多两层）。
    discovery_max_output_tokens: int = Field(default=8192, ge=1024, le=65536)
    # 资料准备（阶段 4 P3a）：检索服务地址、超时、读取体积与每项目预算。
    # 地址为空时资料任务明确失败（search_unconfigured），不用假数据代替真实检索。
    search_base_url: str = ""
    search_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    search_result_limit: int = Field(default=8, ge=1, le=20)
    article_read_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    article_max_bytes: int = Field(default=2_000_000, ge=50_000, le=10_000_000)
    materials_max_queries: int = Field(default=3, ge=1, le=10)
    materials_max_reads: int = Field(default=12, ge=1, le=50)
    # 资料整理（阶段 4 P3b）：输入长度与事实条数上限。正文按单份截断后再拼接，
    # 总长度受 materials_digest_max_input_chars 约束；超限不静默丢资料（截断处可追溯）。
    materials_digest_max_input_chars: int = Field(default=80000, ge=10000, le=400000)
    digest_material_chars: int = Field(default=4000, ge=500, le=20000)
    digest_max_facts: int = Field(default=40, ge=1, le=200)
    # 每次整理调用处理几份资料：太多会让模型写不完（真实环境实测被输出上限截断）。
    digest_batch_size: int = Field(default=3, ge=1, le=30)
    # 单次调用的输出上限；被截断时会自动把这一批对半拆开重试。
    digest_max_output_tokens: int = Field(default=16384, ge=1024, le=65536)
    digest_max_evidence_per_fact: int = Field(default=4, ge=1, le=10)
    # 单批事实与缺口条数上限：写进提示词，同时作为服务端截断兜底。真实环境实测不设上限时
    # 模型每批会写 1–2 万 token 的事实与缺口（凑数、编造、每批例行补缺口），既慢又噪声大。
    digest_facts_per_batch: int = Field(default=8, ge=1, le=40)
    digest_gaps_per_batch: int = Field(default=2, ge=0, le=20)
    # 提纲生成（阶段 4 P4）：输入是选题与已核对事实的清单，一次调用只生成一份提纲，
    # 因此不做分批——输出被截断直接失败，调大这里的上限或改提示词才是解法。
    outline_max_input_chars: int = Field(default=80000, ge=10000, le=400000)
    # 实测（43 份资料 / 50 条事实的选题，deepseek-v4.1-flash）：一次提纲输出 13.9k token，
    # 8192 会被 max_tokens 截断（llm_output_truncated）；该模型的思考 token 也计入输出，
    # 因此默认按实测值给到 32768。
    outline_max_output_tokens: int = Field(default=32768, ge=1024, le=65536)
    # 全文生成（阶段 4 P5）：输入是已确认提纲与事实清单，一次调用生成整篇，
    # 与提纲一样不分批——输出被截断直接失败。
    article_max_input_chars: int = Field(default=80000, ge=10000, le=400000)
    # P4 实测一次提纲输出就有 13.9k token（含模型思考），全文更长，默认按 32768 给；
    # 上限放宽到 65536，真实跑一篇之后再按实测调整。
    article_max_output_tokens: int = Field(default=32768, ge=1024, le=65536)
    # 允许读取私网/本机地址：仅本机夹具（preview）或内网资料源使用；默认关闭，
    # 生产环境必须保持拒绝，避免检索结果把应用当成内网代理。
    article_allow_private_hosts: bool = False

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
