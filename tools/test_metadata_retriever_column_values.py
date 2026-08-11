from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.metadata_context_enhancer import DeterministicMetadataContextEnhancer
from backend.metadata_retriever import DeterministicMetadataRetriever


def main() -> int:
    rows = [
        {
            "table": "rs_outlet",
            "table_comment": "排污口信息表",
            "column": "area_name",
            "type": "varchar(100)",
            "comment": "行政区名称",
            "typical_values": ["梁子湖风景区", "涂家垴镇"],
        },
        {
            "table": "rs_outlet",
            "table_comment": "排污口信息表",
            "column": "outlet_address",
            "type": "varchar(200)",
            "comment": "排污口地址",
            "typical_values": [],
        },
    ]
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "metadata.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        retriever = DeterministicMetadataRetriever(path)

        question = "梁子湖风景区共有多少个排污口？"
        candidates = retriever.retrieve(question, top_n=10)
        outlet = next(item for item in candidates if item["table_name"] == "rs_outlet")
        area = next(
            item
            for item in outlet["matched_columns"]
            if item["column_name"] == "area_name"
        )
        prompt = DeterministicMetadataContextEnhancer(
            metadata_retriever=retriever
        )._build_metadata_context(question, candidates)

        value_columns = retriever.find_columns(question, top_n=10)
        no_value_match = retriever.find_columns("查询太和镇排污口", top_n=10)
        comment_match = retriever.find_columns("行政区名称", top_n=10)
        checks = [
            outlet["table_name"] == "rs_outlet",
            "column_typical_value" in area["matched_by"],
            area["matched_values"] == ["梁子湖风景区"],
            "rs_outlet.area_name" in prompt,
            "matched_value=梁子湖风景区" in prompt,
            "matched_value=涂家垴镇" not in prompt,
            any(
                item["column_name"] == "area_name"
                and item["matched_values"] == ["梁子湖风景区"]
                for item in value_columns
            ),
            all(
                item["column_name"] != "outlet_address"
                for item in outlet["matched_columns"]
            ),
            all(
                "column_typical_value" not in item["matched_by"]
                for item in no_value_match
            ),
            any(
                item["column_name"] == "area_name"
                and any(
                    method in item["matched_by"]
                    for method in (
                        "exact_column_comment",
                        "column_comment_substring",
                    )
                )
                for item in comment_match
            ),
        ]

    for index, passed in enumerate(checks, start=1):
        print(f"{'PASS' if passed else 'FAIL'}: check_{index}")
    print(f"TOTAL={len(checks)} PASS={sum(checks)} FAIL={len(checks) - sum(checks)}")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
