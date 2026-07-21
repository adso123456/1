"""F1 全表 DDL 训练纯逻辑测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import train_step3


def make_records(table_count: int = 115) -> list[dict[str, str]]:
    return [
        {
            "table": f"table_{index:03d}",
            "table_comment": f"测试业务表{index:03d}",
            "column": "id",
            "type": "bigint",
            "comment": "主键",
        }
        for index in range(table_count)
    ]


class FullSchemaTrainingTests(unittest.TestCase):
    def test_load_metadata_index(self) -> None:
        records = make_records()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metadata.json"
            path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(train_step3.load_metadata_index(path), records)

    def test_group_and_build_all_ddls(self) -> None:
        records = list(reversed(make_records()))
        records.extend(
            [
                {
                    "table": "table_000",
                    "table_comment": "测试业务表000",
                    "column": "name",
                    "type": "character varying(64)",
                    "comment": "名称",
                },
                {
                    "table": "table_000",
                    "table_comment": "测试业务表000",
                    "column": "geom",
                    "type": "geometry(Point,4326)",
                    "comment": "空间位置",
                },
            ]
        )

        tables = train_step3.group_tables(records)
        generated, geometry_count = train_step3.build_all_table_ddls(tables)

        self.assertEqual(len(tables), 115)
        self.assertEqual(len(generated), 115)
        self.assertEqual([table["table"] for table in tables], sorted(table["table"] for table in tables))
        self.assertEqual(geometry_count, 1)
        ddl = generated[0]["ddl"]
        self.assertIn("[DDL_MEMORY]", ddl)
        self.assertIn("检索词：表结构 table_000", ddl)
        self.assertEqual(ddl.count("表结构 table_000"), 25)
        self.assertEqual(generated[-1]["ddl"].count("表结构 table_114"), 50)
        self.assertIn("表名：table_000", ddl)
        self.assertIn("表说明：测试业务表000", ddl)
        self.assertIn('CREATE TABLE "table_000"', ddl)
        self.assertIn('"id" bigint, -- 主键', ddl)
        self.assertIn('"name" character varying(64) -- 名称', ddl)
        self.assertNotIn('"geom"', ddl)
        self.assertLess(ddl.index('"id"'), ddl.index('"name"'))
        self.assertEqual(
            train_step3.build_all_table_ddls(tables),
            train_step3.build_all_table_ddls(tables),
        )

    def test_duplicate_table_fields_keep_input_order(self) -> None:
        records = make_records()
        records.extend(
            [
                {
                    "table": "table_050",
                    "table_comment": "测试业务表050",
                    "column": "second_column",
                    "type": "text",
                    "comment": "第二字段",
                },
                {
                    "table": "table_050",
                    "table_comment": "测试业务表050",
                    "column": "third_column",
                    "type": "integer",
                    "comment": "第三字段",
                },
            ]
        )
        table = next(
            item for item in train_step3.group_tables(records) if item["table"] == "table_050"
        )
        self.assertEqual(
            [column["column"] for column in table["columns"]],
            ["id", "second_column", "third_column"],
        )

    def test_comment_samples_are_stable_and_cover_prefixes_first(self) -> None:
        records = make_records()
        records[0]["table"] = "alpha_table"
        records[1]["table"] = "beta_table"
        records[2]["table"] = "gamma_table"
        tables = train_step3.group_tables(records)
        first = train_step3.select_comment_retrieval_samples(tables)
        second = train_step3.select_comment_retrieval_samples(tables)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        selected_names = {table["table"] for table in first}
        self.assertTrue({"alpha_table", "beta_table", "gamma_table"} <= selected_names)

    def test_empty_table_name_is_rejected(self) -> None:
        records = make_records()
        records[0]["table"] = "  "
        with self.assertRaisesRegex(ValueError, "表名为空"):
            train_step3.group_tables(records)

    def test_empty_column_name_is_rejected(self) -> None:
        records = make_records()
        records[0]["column"] = ""
        with self.assertRaisesRegex(ValueError, "字段名为空"):
            train_step3.group_tables(records)

    def test_wrong_table_count_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "唯一表数必须为 115"):
            train_step3.group_tables(make_records(114))

    def test_real_metadata_index_has_115_tables(self) -> None:
        records = train_step3.load_metadata_index()
        tables = train_step3.group_tables(records)
        self.assertEqual(len(tables), 115)

    def test_table_without_non_geometry_column_is_rejected(self) -> None:
        records = make_records()
        records[0]["type"] = "geometry(Point,4326)"
        with self.assertRaisesRegex(ValueError, "没有有效的非 geometry 字段"):
            train_step3.group_tables(records)

    def test_formal_chroma_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "禁止指向正式 Chroma"):
            train_step3.validate_target_data_dir(
                {"VANNA_DATA_DIR": str(train_step3.FORMAL_CHROMA_DIR)}
            )

    def test_explicit_non_formal_directory_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            actual = train_step3.validate_target_data_dir(
                {"VANNA_DATA_DIR": temp_dir}
            )
            self.assertEqual(actual, Path(temp_dir).resolve())

    def test_missing_target_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须显式设置"):
            train_step3.validate_target_data_dir({})

    def test_import_has_no_runtime_side_effect_dependencies(self) -> None:
        source = (PROJECT_ROOT / "train_step3.py").read_text(encoding="utf-8")
        self.assertNotIn("PostgresRunner", source)
        self.assertNotIn("from backend.memory", source.split("async def _run_training", 1)[0])
        self.assertNotIn("backend.memory", sys.modules)

    def test_server_prompt_no_longer_claims_six_tables(self) -> None:
        source = (PROJECT_ROOT / "backend" / "prompts.py").read_text(encoding="utf-8")
        self.assertNotIn("all 6 available tables", source)
        self.assertIn("metadata index and retrieved Text Memory context", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
