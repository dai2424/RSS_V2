"""运行配置。"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """RSS v2 的环境配置。"""

    model_config = SettingsConfigDict(env_prefix="RSS_", extra="ignore")

    runtime_dir: Path = Path("runtime")
    database_path: Path = Path("runtime/db/rss_v2.db")
    migrations_dir: Path = Path("migrations")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    rss_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    worker_lease_seconds: int = Field(default=300, gt=10, le=3600)
    worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    llm_default_prompt_version: str = "translation-v1"
    llm_retry_cooldown_seconds: int = Field(default=60, gt=0, le=3600)

    def ensure_runtime_dirs(self) -> None:
        """创建运行时目录，不触碰源码目录。"""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
