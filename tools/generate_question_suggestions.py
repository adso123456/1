"""推荐问题资产生成 CLI（薄封装）。

核心逻辑在 backend.question_suggestion_generator，这里只做参数解析与结果输出。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.question_suggestion_generator import generate_for_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成单一数据源的专属推荐问题资产",
    )
    parser.add_argument("--source-id", required=True, help="数据源 source_id")
    parser.add_argument(
        "--root",
        default=None,
        help="问题资产根目录（默认 <AGENT_DATA_DIR>/question_suggestions）",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help="catalog.sqlite3 路径（默认 agent_data/data_sources/catalog.sqlite3）",
    )
    parser.add_argument(
        "--materials-dir",
        default=None,
        help="已批准 SQL 示例目录（覆盖默认按 source_id 解析）",
    )
    parser.add_argument(
        "--metadata-path",
        default=None,
        help="已发布 Metadata 路径（默认取 catalog 记录）",
    )
    parser.add_argument(
        "--no-db-verify",
        action="store_true",
        help="跳过真实数据库只读验证（问题不启用，仅做管线校验）",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=100,
        help="最多处理问题数（默认 100，硬上限 200）",
    )
    parser.add_argument("--asset-version", default="v1", help="资产版本号")
    args = parser.parse_args(argv)

    summary = generate_for_source(
        source_id=args.source_id,
        root=Path(args.root) if args.root else None,
        catalog_path=args.catalog,
        materials_dir=args.materials_dir,
        metadata_path=Path(args.metadata_path) if args.metadata_path else None,
        no_db_verify=args.no_db_verify,
        max_questions=args.max_questions,
        asset_version=args.asset_version,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
