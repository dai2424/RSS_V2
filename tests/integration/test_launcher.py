"""一键启动的真实进程验收，全部数据使用临时目录。"""

import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO

import httpx
import pytest

from rss_v2 import launcher
from rss_v2.settings import Settings


@pytest.mark.parametrize("occupied", [False, True])
def test_child_failure_stops_both_processes_and_worker_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, occupied: bool
) -> None:
    """API、界面、worker 可用；API 退出时启动器回收 worker，不留后台进程。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    if occupied:
        listener.listen()
    else:
        listener.close()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>startup fixture</html>", encoding="utf-8")
    monkeypatch.setenv("RSS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("RSS_DATABASE_PATH", str(tmp_path / "db/rss_v2.db"))
    monkeypatch.setenv("RSS_FRONTEND_DIST", str(frontend))
    monkeypatch.setenv("RSS_API_PORT", str(port))
    monkeypatch.setenv("RSS_API_HOST", "127.0.0.1")
    monkeypatch.setenv("RSS_WORKER_POLL_SECONDS", "0.1")
    monkeypatch.setenv("RSS_CONFIG", str(tmp_path / "missing.toml"))
    spawned: list[subprocess.Popen[bytes]] = []
    spawn = launcher._spawn

    def track(command: str, project: Path, output: BinaryIO, port: int) -> subprocess.Popen[bytes]:
        child = spawn(command, project, output, port)
        spawned.append(child)
        return child

    monkeypatch.setattr(launcher, "_spawn", track)
    settings = launcher._select_port(Settings())
    assert (settings.api_port != port) is occupied
    project = Path(__file__).resolve().parents[2]
    base = f"http://127.0.0.1:{settings.api_port}"
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            verification = pool.submit(_verify_and_end_api, base, settings, spawned)
            with pytest.raises(launcher.StartupError, match="API 已退出"):
                launcher._serve(project, settings, open_browser=False)
            verification.result(timeout=5)
        assert len(spawned) == 2 and all(child.poll() is not None for child in spawned)
        with socket.socket() as connection:
            assert connection.connect_ex(("127.0.0.1", settings.api_port)) != 0
        if occupied:
            with socket.socket() as connection:
                assert connection.connect_ex(("127.0.0.1", port)) == 0
    finally:
        listener.close()


def _verify_and_end_api(
    base: str, settings: Settings, spawned: list[subprocess.Popen[bytes]]
) -> None:
    deadline = time.monotonic() + 15
    try:
        with httpx.Client(base_url=base, timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    categories = client.get("/api/categories").raise_for_status().json()
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise AssertionError("API 启动超时")
            assert "startup fixture" in client.get("/sources").text
            with pytest.raises(launcher.StartupError, match="端口"):
                launcher._check_port(settings)
            source = (
                client.post(
                    "/api/sources",
                    json={
                        "name": "启动验收",
                        "url": base + "/missing.xml",
                        "category_id": categories[0]["id"],
                    },
                )
                .raise_for_status()
                .json()
            )
            run = (
                client.post(
                    "/api/collection/runs",
                    json={"source_ids": [source["id"]], "all_enabled": False},
                )
                .raise_for_status()
                .json()
            )
            while time.monotonic() < deadline:
                task = client.get("/api/tasks/" + run["task_ids"][0]).raise_for_status().json()
                if task["attempts"] >= 1 and task["status"] != "running":
                    return
                time.sleep(0.1)
            raise AssertionError("worker 未领取任务")
    finally:
        # 只结束测试创建的 API；启动器负责发现退出并清理独立 worker。
        if spawned:
            spawned[0].terminate()
