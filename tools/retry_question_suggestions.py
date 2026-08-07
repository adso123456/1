"""E-3：手工重试推荐问题同步。

只登记当前 revision 的同步任务，不接受调用方指定任意旧 revision，
不允许传入资产路径，不在请求内同步执行全部生成。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.question_suggestion_sync import retry_question_suggestions
from config.settings import AGENT_DATA_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="手工重试数据源推荐问题同步（只登记当前 revision）",
    )
    parser.add_argument("--source-id", required=True, help="数据源 source_id")
    parser.add_argument(
        "--catalog",
        default=None,
        help="catalog.sqlite3 路径（默认 agent_data/data_sources/catalog.sqlite3）",
    )
    args = parser.parse_args(argv)
    path = (
        Path(args.catalog).expanduser().resolve()
        if args.catalog
        else (Path(AGENT_DATA_DIR).resolve() / "data_sources" / "catalog.sqlite3")
    )
    cipher = None
    key = os.getenv("DATA_SOURCE_CREDENTIAL_KEY", "").strip()
    if key:
        cipher = CredentialCipher(key)
    catalog = DataSourceCatalog(path, cipher=cipher)
    job = retry_question_suggestions(catalog, args.source_id)
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
