"""训练数据目录指纹、验证备份和恢复副本演练 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.storage_snapshot import (
    SnapshotError,
    build_directory_manifest,
    create_restore_rehearsal,
    create_verified_copy,
    verify_directory_equivalence,
)


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="纯文件系统训练数据目录指纹与验证复制工具"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest", help="生成确定性目录清单")
    manifest_parser.add_argument("source", help="显式源目录")

    backup_parser = subparsers.add_parser("backup", help="创建仓库外验证副本")
    backup_parser.add_argument("source", help="显式源目录")
    backup_parser.add_argument("destination", help="显式全新目标目录")

    verify_parser = subparsers.add_parser("verify", help="比较两个目录清单")
    verify_parser.add_argument("source", help="显式源目录")
    verify_parser.add_argument("copy", help="显式副本目录")

    restore_parser = subparsers.add_parser(
        "restore-rehearsal", help="从备份复制到全新恢复目录并三方核对"
    )
    restore_parser.add_argument("source", help="显式源目录")
    restore_parser.add_argument("backup", help="显式已验证备份目录")
    restore_parser.add_argument("destination", help="显式全新恢复目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "manifest":
            manifest = build_directory_manifest(args.source)
            _print_json({"result": "VALID", "manifest": manifest.to_dict()})
            return 0

        if args.command == "backup":
            result = create_verified_copy(
                args.source, args.destination, PROJECT_ROOT
            )
            _print_json(
                {
                    "result": "VERIFIED",
                    "source_content_sha256": result.source_before.content_sha256,
                    "destination_content_sha256": result.destination.content_sha256,
                    "source_stable": (
                        result.source_before.content_sha256
                        == result.source_after.content_sha256
                    ),
                }
            )
            return 0

        if args.command == "verify":
            result = verify_directory_equivalence(args.source, args.copy)
            _print_json(
                {
                    "result": "EQUIVALENT" if result.equivalent else "DIFFERENT",
                    "equivalent": result.equivalent,
                    "differences": list(result.differences),
                    "source_content_sha256": result.left.content_sha256,
                    "copy_content_sha256": result.right.content_sha256,
                }
            )
            return 0 if result.equivalent else 1

        if args.command == "restore-rehearsal":
            result = create_restore_rehearsal(
                args.source, args.backup, args.destination, PROJECT_ROOT
            )
            _print_json(
                {
                    "result": "RESTORE_REHEARSAL_VERIFIED",
                    "source_content_sha256": result.source.content_sha256,
                    "backup_content_sha256": result.backup.content_sha256,
                    "restored_content_sha256": result.restored.content_sha256,
                }
            )
            return 0
    except (SnapshotError, OSError) as error:
        print(f"ERROR {type(error).__name__}: {error}")
        return 1
    raise AssertionError(f"未处理的子命令: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
