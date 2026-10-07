"""API 和数据库测试 fixture。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rss_v2.api.app import create_app
from rss_v2.settings import Settings


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """创建完全隔离的临时应用。"""

    settings = Settings(
        runtime_dir=tmp_path / "runtime",
        database_path=tmp_path / "runtime" / "db.sqlite",
        migrations_dir=Path("migrations"),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
