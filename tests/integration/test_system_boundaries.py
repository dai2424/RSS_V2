"""运行配置、在线备份、前端刷新与本机访问边界。"""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rss_v2.api.app import create_app
from rss_v2.bootstrap import build_container
from rss_v2.settings import Settings


def test_backup_restores_committed_wal_data(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/categories", json={"name": "备份保留"})
    container = client.app.state.container
    backup = tmp_path / "backup.db"
    container.backup(backup)
    client.post("/api/categories", json={"name": "备份之后"})
    restored = build_container(Settings(runtime_dir=tmp_path / "restore", database_path=backup))
    names = [item.name for item in restored.category_service.list()]
    assert "备份保留" in names and "备份之后" not in names
    assert restored.migrate() == []


def test_configuration_priority_and_runtime_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "settings.toml"
    config.write_text("api_port = 8088\nrss_timeout_seconds = 12.0\n", encoding="utf-8")
    monkeypatch.setenv("RSS_CONFIG", str(config))
    monkeypatch.setenv("RSS_API_PORT", "9000")
    settings = Settings(runtime_dir=tmp_path, api_port=9001)
    assert settings.api_port == 9001 and settings.rss_timeout_seconds == 12.0
    assert settings.database_path == tmp_path / "db/rss_v2.db"
    assert settings.frontend_dist == tmp_path / "frontend"


@pytest.mark.parametrize(
    "path",
    ["/sources/new", "/sources/source-id", "/messages/message-id", "/tasks", "/settings/providers"],
)
def test_frontend_deep_links_reload(tmp_path: Path, path: str) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>app shell</html>", encoding="utf-8")
    settings = Settings(runtime_dir=tmp_path, frontend_dist=frontend)
    with TestClient(create_app(settings)) as client:
        assert "app shell" in client.get(path).text
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/missing").status_code == 404


def test_local_only_rejects_lan_and_external_origins(tmp_path: Path) -> None:
    settings = Settings(runtime_dir=tmp_path)
    with TestClient(create_app(settings), client=("192.168.1.9", 12345)) as client:
        assert client.get("/api/categories").status_code == 403
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/categories",
                json={"name": "blocked"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert client.get("/api/categories", headers={"Host": "evil.example"}).status_code == 403
    with pytest.raises(ValueError):
        Settings(api_host="0.0.0.0")


def test_business_layers_obey_import_boundaries() -> None:
    """防止未来再次把具体 SQLite 或 HTTP 实现引入业务层。"""
    for directory in ("domain", "services", "ports"):
        for path in Path("src/rss_v2", directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                assert not any(
                    module.startswith(("sqlite3", "httpx", "rss_v2.adapters", "fastapi"))
                    for module in modules
                ), path
