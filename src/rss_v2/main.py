"""API、迁移和 worker 命令入口。"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import uvicorn

from rss_v2.api.app import create_app
from rss_v2.bootstrap import build_container
from rss_v2.domain import DomainError
from rss_v2.settings import Settings
from rss_v2.tasks.worker import Worker

app = create_app()


def _parser() -> argparse.ArgumentParser:
    """集中定义命令参数；具体执行仍复用组合根。"""
    parser = argparse.ArgumentParser(description="RSS v2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="执行数据库迁移")
    subparsers.add_parser("api", help="启动本机 API 和已构建的前端")
    start_parser = subparsers.add_parser("start", help="一键准备并启动 API、worker 和工作台")
    start_parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    backup_parser = subparsers.add_parser("backup", help="在线备份并检查完整性")
    backup_parser.add_argument("destination", type=Path)
    import_parser = subparsers.add_parser("import-v1", help="预览 v1 RSS 导入，--apply 才写入")
    import_parser.add_argument("source", type=Path, help="v1 SQLite 数据库路径")
    import_parser.add_argument("--apply", action="store_true", help="备份 v2 并应用导入")
    schema_parser = subparsers.add_parser("schema", help="导出 OpenAPI，不创建数据库")
    schema_parser.add_argument("--output", type=Path, default=Path("frontend/openapi.json"))
    worker_parser = subparsers.add_parser("worker", help="运行后台 worker")
    worker_parser.add_argument("--once", action="store_true", help="只执行一个任务")
    return parser


def main() -> None:
    """解析并执行 API、迁移、worker 或一键启动命令。"""

    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "start":
        from rss_v2.launcher import start

        raise SystemExit(start(open_browser=not args.no_browser))
    if args.command == "schema":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(create_app().openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return
    if args.command == "api":
        settings = Settings()
        uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
        return
    if args.command == "import-v1":
        try:
            report = build_container(migrate=False).import_v1(args.source, args.apply)
        except DomainError as exc:
            parser.exit(2, f"导入失败 [{exc.code}]：{exc.message}\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    container = build_container(migrate=args.command != "migrate")
    if args.command == "migrate":
        applied = container.migrate()
        print(f"已执行迁移：{', '.join(applied) if applied else '无'}")
    elif args.command == "worker":
        worker = Worker(container)
        if args.once:
            print(f"任务已领取：{worker.run_once()}")
        else:
            worker.run_forever()
    elif args.command == "backup":
        container.backup(args.destination)
        print("备份已完成并通过完整性检查")


if __name__ == "__main__":
    main()
