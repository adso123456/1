from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_retriever import DeterministicMetadataRetriever


TABLES = (
    ("rs_pollutant_hour_records", "污染源小时监测记录"),
    ("rs_pollutant_day_records", "污染源日监测记录"),
    ("rs_pollutant_month_records", "污染源月度监测记录"),
)


def _write_fixture(path: Path) -> None:
    rows = []
    for table_name, table_comment in TABLES:
        rows.extend(
            [
                {
                    "table": table_name,
                    "table_comment": table_comment,
                    "column": "pollutant_id",
                    "type": "varchar(64)",
                    "comment": "污染源 ID",
                },
                {
                    "table": table_name,
                    "table_comment": table_comment,
                    "column": "monitor_time",
                    "type": "datetime",
                    "comment": "监测时间",
                },
            ]
        )
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _check_routing(
    retriever: DeterministicMetadataRetriever,
    query: str,
    expected_table: str,
) -> tuple[bool, str]:
    candidates = retriever.retrieve(query, top_n=10)
    scored_tables = {
        table_name: retriever._score_table(query, retriever.tables[table_name])
        for table_name, _ in TABLES
    }
    target = scored_tables[expected_table]
    sibling_scores = [
        item["score"]
        for table_name, item in scored_tables.items()
        if table_name != expected_table
    ]
    passed = bool(
        candidates
        and candidates[0]["table_name"] == expected_table
        and "pollutant_record_granularity" in target["matched_by"]
        and all(target["score"] > score for score in sibling_scores)
    )
    detail = ", ".join(
        f"{table_name}={item['score']}"
        for table_name, item in scored_tables.items()
    )
    return passed, detail


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary_directory:
        metadata_path = Path(temporary_directory) / "metadata.json"
        _write_fixture(metadata_path)
        retriever = DeterministicMetadataRetriever(metadata_path)

        cases = [
            (
                "小时粒度路由",
                "查询某污染源 2026-07-01 至 2026-07-07 的小时监测记录",
                "rs_pollutant_hour_records",
            ),
            (
                "日粒度路由",
                "查询某污染源 2026-07-01 至 2026-07-07 的每日监测记录",
                "rs_pollutant_day_records",
            ),
            (
                "月粒度路由",
                "查询某污染源 2026 年 7 月的月度监测记录",
                "rs_pollutant_month_records",
            ),
        ]
        results = [
            (name, *_check_routing(retriever, query, expected_table))
            for name, query, expected_table in cases
        ]

    failed = [name for name, passed, _ in results if not passed]
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}: {name}: {detail}")
    print(f"TOTAL={len(results)} PASS={len(results) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
