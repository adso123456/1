"""副本与远程本尊结构差异、语义继承回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_claim_identity import (
    build_schema_diff,
    inherit_compatible_semantics,
)


def main() -> int:
    baseline = [
        {
            "table": "stable_table",
            "column": "id",
            "type": "bigint",
            "domain": "稳定业务",
            "grain": "每行一条记录",
            "time_column": "created_at",
        },
        {
            "table": "stable_table",
            "column": "created_at",
            "type": "timestamp",
        },
        {
            "table": "changed_table",
            "column": "value",
            "type": "integer",
            "domain": "旧业务",
        },
        {
            "table": "removed_table",
            "column": "id",
            "type": "bigint",
        },
    ]
    remote = [
        {
            "schema": "public",
            "table": "stable_table",
            "column": "id",
            "type": "bigint",
        },
        {
            "schema": "public",
            "table": "stable_table",
            "column": "created_at",
            "type": "timestamp",
        },
        {
            "schema": "public",
            "table": "stable_table",
            "column": "new_column",
            "type": "text",
        },
        {
            "schema": "public",
            "table": "changed_table",
            "column": "value",
            "type": "numeric",
        },
        {
            "schema": "public",
            "table": "new_table",
            "column": "id",
            "type": "bigint",
        },
    ]
    diff = build_schema_diff(baseline, remote, default_schema="public")
    assert diff["summary"] == {
        "baseline_table_count": 3,
        "remote_table_count": 3,
        "added_table_count": 1,
        "removed_table_count": 1,
        "added_column_count": 2,
        "removed_column_count": 1,
        "changed_column_count": 1,
        "unchanged_column_count": 2,
    }
    inherited = inherit_compatible_semantics(
        baseline,
        remote,
        diff,
        default_schema="public",
    )
    stable_rows = [item for item in inherited if item["table"] == "stable_table"]
    assert all(item["domain"] == "稳定业务" for item in stable_rows)
    assert all(item["semantic_origin"] == "replica_asset_inherited" for item in stable_rows)
    changed = next(item for item in inherited if item["table"] == "changed_table")
    assert "domain" not in changed and "semantic_origin" not in changed
    new = next(item for item in inherited if item["table"] == "new_table")
    assert "semantic_origin" not in new
    print("builtin claim diff tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
