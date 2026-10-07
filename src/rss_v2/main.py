"""API、迁移和 worker 命令入口。"""

from __future__ import annotations

import argparse

from rss_v2.adapters.sqlite.migrations import MigrationRunner
from rss_v2.api.app import create_app
from rss_v2.bootstrap import build_container
from rss_v2.tasks.worker import Worker

app = create_app()


def main() -> None:
    """解析 CLI 命令。"""

    parser = argparse.ArgumentParser(description="RSS v2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="执行数据库迁移")
    worker_parser = subparsers.add_parser("worker", help="运行后台 worker")
    worker_parser.add_argument("--once", action="store_true", help="只执行一个任务")
    args = parser.parse_args()
    container = build_container()
    if args.command == "migrate":
        applied = MigrationRunner(container.database, container.settings.migrations_dir).run()
        print(f"已执行迁移：{', '.join(applied) if applied else '无'}")
    elif args.command == "worker":
        worker = Worker(container)
        if args.once:
            print(f"任务已领取：{worker.run_once()}")
        else:
            worker.run_forever()


if __name__ == "__main__":
    main()
