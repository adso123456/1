"""小助手应用注册表命令行管理工具。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_application_registry import (  # noqa: E402
    ApplicationAlreadyExists,
    ApplicationNotFound,
    AssistantApplicationError,
    AssistantApplicationRegistry,
    AssistantApplicationView,
    InvalidApplicationConfiguration,
    resolve_system_db_path,
)
from backend.data_source_registry import (  # noqa: E402
    build_current_data_source_registry,
)
from backend.embed_access import load_embed_application_config  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理本机 SQLite 小助手应用注册表",
    )
    parser.add_argument(
        "--db-path",
        help="覆盖 WATER_AGENT_SYSTEM_DB_PATH",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init")

    create = subparsers.add_parser("create")
    create.add_argument("--app-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--origin", action="append", default=[])
    create.add_argument("--source-id", action="append", default=[])
    create.add_argument("--ttl", type=int, default=300)
    create.add_argument("--theme", default="#1677ff")
    create.add_argument("--logo-url", default="")
    create.add_argument("--welcome", default="有什么可以帮助你的？")
    create.add_argument(
        "--welcome-description",
        default="用中文自然语言提问，Agent 自动查询数据库并返回图表",
    )
    create.add_argument("--show-history", action="store_true")

    subparsers.add_parser("list")

    show = subparsers.add_parser("show")
    show.add_argument("--app-id", required=True)

    update = subparsers.add_parser("update")
    update.add_argument("--app-id", required=True)
    update.add_argument("--name")
    origin_group = update.add_mutually_exclusive_group()
    origin_group.add_argument("--origin", action="append")
    origin_group.add_argument("--clear-origins", action="store_true")
    source_group = update.add_mutually_exclusive_group()
    source_group.add_argument("--source-id", action="append")
    source_group.add_argument("--clear-source-ids", action="store_true")
    update.add_argument("--ttl", type=int)
    update.add_argument("--theme")
    update.add_argument("--logo-url")
    update.add_argument("--welcome")
    update.add_argument("--welcome-description")
    history_group = update.add_mutually_exclusive_group()
    history_group.add_argument(
        "--show-history",
        action="store_const",
        const=True,
        dest="show_history",
    )
    history_group.add_argument(
        "--hide-history",
        action="store_const",
        const=False,
        dest="show_history",
    )

    for command in ("enable", "disable", "rotate-secret"):
        child = subparsers.add_parser(command)
        child.add_argument("--app-id", required=True)

    subparsers.add_parser("bootstrap-env")
    return parser


def _registry(args: argparse.Namespace) -> AssistantApplicationRegistry:
    data_sources = build_current_data_source_registry()
    db_path = (
        Path(args.db_path).expanduser().resolve()
        if args.db_path
        else resolve_system_db_path()
    )
    return AssistantApplicationRegistry(db_path, data_sources)


def _print_view(view: AssistantApplicationView) -> None:
    values = asdict(view)
    for key, value in values.items():
        print(f"{key}: {value}")


def execute(args: argparse.Namespace) -> int:
    registry = _registry(args)
    if args.command == "init":
        registry.initialize()
        print(f"initialized: {registry.db_path}")
        return 0

    if args.command == "create":
        created = registry.create(
            app_id=args.app_id,
            name=args.name,
            allowed_origins=args.origin,
            allowed_source_ids=args.source_id,
            token_ttl_seconds=args.ttl,
            theme=args.theme,
            logo_url=args.logo_url,
            welcome=args.welcome,
            welcome_description=args.welcome_description,
            show_history=args.show_history,
        )
        print(f"created: {created.application.app_id}")
        print(f"app_secret: {created.app_secret}")
        return 0

    if args.command == "list":
        print("app_id\tname\tenabled\torigins\tsources\tupdated_at")
        for application in registry.list():
            print(
                f"{application.app_id}\t{application.name}\t"
                f"{str(application.enabled).lower()}\t"
                f"{len(application.allowed_origins)}\t"
                f"{len(application.allowed_source_ids)}\t"
                f"{application.updated_at}"
            )
        return 0

    if args.command == "show":
        _print_view(registry.get(args.app_id))
        return 0

    if args.command == "update":
        origins = (
            ()
            if args.clear_origins
            else args.origin
        )
        source_ids = (
            ()
            if args.clear_source_ids
            else args.source_id
        )
        view = registry.update(
            args.app_id,
            name=args.name,
            allowed_origins=origins,
            allowed_source_ids=source_ids,
            token_ttl_seconds=args.ttl,
            theme=args.theme,
            logo_url=args.logo_url,
            welcome=args.welcome,
            welcome_description=args.welcome_description,
            show_history=args.show_history,
        )
        print(f"updated: {view.app_id}")
        return 0

    if args.command == "enable":
        print(f"enabled: {registry.enable(args.app_id).app_id}")
        return 0

    if args.command == "disable":
        print(f"disabled: {registry.disable(args.app_id).app_id}")
        return 0

    if args.command == "rotate-secret":
        rotated = registry.rotate_secret(args.app_id)
        print(f"rotated: {rotated.application.app_id}")
        print(f"app_secret: {rotated.app_secret}")
        return 0

    if args.command == "bootstrap-env":
        config = load_embed_application_config()
        if config is None:
            raise InvalidApplicationConfiguration(
                "未找到旧单应用环境变量"
            )
        registry.create(
            app_id=config.app_id,
            name=config.app_id,
            enabled=config.enabled,
            app_secret=config.app_secret,
            allowed_origins=config.allowed_origins,
            allowed_source_ids=config.allowed_source_ids,
            token_ttl_seconds=config.token_ttl_seconds,
        )
        print(f"bootstrapped: {config.app_id}")
        return 0

    raise AssertionError(f"未知命令: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return execute(parser.parse_args(argv))
    except (
        ApplicationAlreadyExists,
        ApplicationNotFound,
        AssistantApplicationError,
        InvalidApplicationConfiguration,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error:
        print("error: 系统数据库操作失败", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
