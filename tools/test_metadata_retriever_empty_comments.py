from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_retriever import DeterministicMetadataRetriever


def _write_fixture(path: Path) -> None:
    rows = [
        {
            "table": "rs_outlet",
            "table_comment": "排污口",
            "column": "outlet_name",
            "type": "varchar(255)",
            "comment": "排污口名称",
        },
        {
            "table": "model_hydro_gaoqiaohe_2026_3",
            "table_comment": "",
            "column": "id",
            "type": "bigint",
            "comment": "",
        },
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary_directory:
        metadata_path = Path(temporary_directory) / "metadata.json"
        _write_fixture(metadata_path)
        retriever = DeterministicMetadataRetriever(metadata_path)

        accident_query = "查询排污口相关问题"
        outlet = retriever._score_table(
            accident_query, retriever.tables["rs_outlet"]
        )
        hydro = retriever._score_table(
            accident_query,
            retriever.tables["model_hydro_gaoqiaohe_2026_3"],
        )
        ranked = retriever.retrieve(accident_query, top_n=10)

        checks = [
            (
                "空表注释不产生 substring 分数",
                "table_comment_substring" not in hydro["matched_by"],
            ),
            (
                "空字段注释不产生 substring 分数",
                "column_comment_substring" not in hydro["matched_by"],
            ),
            ("无注释模型表事故分数归零", hydro["score"] == 0),
            (
                "排污口业务表显著高于无注释模型表",
                outlet["score"] > hydro["score"]
                and ranked[0]["table_name"] == "rs_outlet",
            ),
            (
                "非空中文表注释仍保留 substring 加分",
                "table_comment_substring" in outlet["matched_by"],
            ),
        ]

        column_query = "请查询排污口名称"
        outlet_column = retriever._score_table(
            column_query, retriever.tables["rs_outlet"]
        )
        hydro_column = retriever._score_table(
            column_query,
            retriever.tables["model_hydro_gaoqiaohe_2026_3"],
        )
        found_columns = retriever.find_columns(column_query, top_n=10)
        checks.extend(
            [
                (
                    "非空中文字段注释仍保留 substring 加分",
                    "column_comment_substring" in outlet_column["matched_by"],
                ),
                (
                    "空字段注释在候选评分中保持零贡献",
                    "column_comment_substring" not in hydro_column["matched_by"],
                ),
                (
                    "find_columns 不返回空注释伪匹配",
                    any(
                        item["table_name"] == "rs_outlet"
                        and item["column_name"] == "outlet_name"
                        for item in found_columns
                    )
                    and all(
                        item["table_name"] != "model_hydro_gaoqiaohe_2026_3"
                        for item in found_columns
                    ),
                ),
            ]
        )

    failed = [name for name, passed in checks if not passed]
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(
        "SCORES: "
        f"rs_outlet={outlet['score']} "
        f"model_hydro_gaoqiaohe_2026_3={hydro['score']}"
    )
    print(f"TOTAL={len(checks)} PASS={len(checks) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
