"""本机一键启动：准备前端，管理 API 与独立 worker，不执行业务任务。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import ExitStack, chdir
from pathlib import Path
from types import FrameType
from typing import BinaryIO

import httpx

from rss_v2.bootstrap import build_container
from rss_v2.settings import Settings


class StartupError(Exception):
    """启动条件不满足或子进程提前退出，消息可直接展示给本机用户。"""


def start(*, open_browser: bool = True) -> int:
    """启动整个工作台；返回退出码，退出时清理本次启动的子进程。"""
    project = Path(__file__).resolve().parents[2]
    try:
        with chdir(project):
            settings = Settings()
            _check_port(settings)
            _prepare_frontend(project, settings)
            build_container(settings)
            return _serve(project, settings, open_browser)
    except KeyboardInterrupt:
        print("\n工作台已停止。", flush=True)
        return 0
    except (StartupError, OSError, subprocess.CalledProcessError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr, flush=True)
        return 1


def _fingerprint(files: list[Path], root: Path) -> str:
    """文件名与内容一起计算指纹，新增、删除或修改文件均使缓存失效。"""
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(str(file.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _cached(stamp: Path, expected: str) -> bool:
    return stamp.is_file() and stamp.read_text(encoding="utf-8") == expected


def _dependencies_installed(frontend: Path) -> bool:
    """复用已有且与锁文件一致的完整安装，避免首次启动无谓清空 node_modules。"""
    installed_lock = frontend / "node_modules/.package-lock.json"
    if not installed_lock.is_file():
        return False
    try:
        expected = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
        installed = json.loads(installed_lock.read_text(encoding="utf-8"))["packages"]
        manifest = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
        for group in ("dependencies", "devDependencies", "optionalDependencies"):
            if manifest.get(group, {}) != expected["packages"][""].get(group, {}):
                return False
        for path, package in expected["packages"].items():
            # 不同系统的可选原生包由 npm 挑选，无须安装其它平台版本。
            if not path or package.get("optional"):
                continue
            actual = installed.get(path, {})
            if actual.get("version") != package.get("version"):
                return False
            if actual.get("integrity") != package.get("integrity"):
                return False
            if not (frontend / path / "package.json").is_file():
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        # 缺损的安装元数据不可信，交给 npm ci 按锁文件恢复。
        return False


def _prepare_frontend(project: Path, settings: Settings) -> None:
    """依赖或源码变化时安装、构建前端；指纹只在成功后写入运行目录。"""
    frontend = project / "frontend"
    npm = shutil.which("npm")
    if npm is None:
        raise StartupError("未找到 npm，请安装 Node.js 22 后重试。")
    version = subprocess.check_output([npm, "--version"], text=True).strip()
    print(f"检查前端环境（npm {version}）…", flush=True)
    dependency_files = [frontend / "package.json", frontend / "package-lock.json"]
    dependencies = _fingerprint(dependency_files, frontend)
    dependency_stamp = frontend / "node_modules/.rss-v2-dependencies"
    if not dependency_stamp.exists() and _dependencies_installed(frontend):
        dependency_stamp.write_text(dependencies, encoding="utf-8")
    if not _cached(dependency_stamp, dependencies):
        print("首次准备或依赖已更新，正在安装前端依赖…", flush=True)
        subprocess.run([npm, "ci"], cwd=frontend, check=True)
        dependency_stamp.write_text(dependencies, encoding="utf-8")
    files = [file for file in (frontend / "src").rglob("*") if file.is_file()]
    files.extend(file for file in frontend.iterdir() if file.suffix in {".ts", ".js", ".json"})
    files.append(frontend / "index.html")
    build = _fingerprint(files, frontend)
    dist = settings.frontend_dist.resolve()
    stamp = dist / ".rss-v2-build"
    if not (dist / "index.html").is_file() or not _cached(stamp, build):
        print("正在构建工作台页面…", flush=True)
        environment = {**os.environ, "RSS_FRONTEND_DIST": str(dist)}
        subprocess.run([npm, "run", "build"], cwd=frontend, env=environment, check=True)
        stamp.write_text(build, encoding="utf-8")
    else:
        print("前端已就绪，无需重新构建。", flush=True)


def _check_port(settings: Settings) -> None:
    family = socket.AF_INET6 if ":" in settings.api_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((settings.api_host, settings.api_port))
        except OSError as exc:
            raise StartupError(
                f"端口 {settings.api_port} 不可用，请关闭已有启动窗口或设置 RSS_API_PORT。"
            ) from exc


def _spawn(command: str, project: Path, output: BinaryIO) -> subprocess.Popen[bytes]:
    """API 和 worker 是独立进程，继承同一环境，输出分别落在日志文件。"""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "rss_v2.main", command],
        cwd=project,
        env={**os.environ, "PYTHONUTF8": "1"},
        stdout=output,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )


def _check_children(children: list[subprocess.Popen[bytes]], logs: Path) -> None:
    for name, child in zip(("API", "worker"), children, strict=True):
        if child.poll() is not None:
            raise StartupError(f"{name} 已退出，请查看 {logs} 下的对应日志。")


def _wait_ready(url: str, children: list[subprocess.Popen[bytes]], logs: Path) -> None:
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            _check_children(children, logs)
            try:
                if client.get(url + "/api/categories").status_code == 200:
                    return
            except httpx.HTTPError:
                # 启动阶段短暂连接失败是预期状态；超时后统一报告启动错误。
                pass
            time.sleep(0.2)
    raise StartupError(f"API 未在 30 秒内就绪，请查看 {logs} 下的 api.log。")


def _stop(children: list[subprocess.Popen[bytes]]) -> None:
    """只结束本启动器拥有的子进程，不触碰其它 API 或 worker。"""
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def _interrupt(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def _serve(project: Path, settings: Settings, open_browser: bool) -> int:
    logs = (settings.runtime_dir / "logs").resolve()
    logs.mkdir(parents=True, exist_ok=True)
    host = f"[{settings.api_host}]" if ":" in settings.api_host else settings.api_host
    url = f"http://{host}:{settings.api_port}"
    children: list[subprocess.Popen[bytes]] = []
    with ExitStack() as stack:
        if sys.platform == "win32":
            previous = signal.signal(signal.SIGBREAK, _interrupt)
            stack.callback(signal.signal, signal.SIGBREAK, previous)
        try:
            for command in ("api", "worker"):
                output = stack.enter_context((logs / f"{command}.log").open("ab"))
                children.append(_spawn(command, project, output))
            _wait_ready(url, children, logs)
            print(f"工作台已就绪：{url}/sources\n按 Ctrl+C 停止 API 和 worker。", flush=True)
            if open_browser and not webbrowser.open(url + "/sources"):
                print("浏览器未自动打开，请打开上面的地址。", flush=True)
            while True:
                _check_children(children, logs)
                time.sleep(0.5)
        finally:
            _stop(children)
