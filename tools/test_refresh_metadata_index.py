from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.refresh_metadata_index import (
    MetadataValidationError,
    ReadOnlyGateError,
    build_metadata_manifest,
    diff_metadata_indexes,
    extract_postgresql_metadata,
    normalize_metadata_rows,
    serialize_metadata_index,
    validate_output_directory,
    write_audit_package,
)


def row(
    table: str,
    column: str,
    column_type: str = "integer",
    comment: str = "字段注释",
    table_comment: str = "表注释",
    ordinal_position: int | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "table": table,
        "table_comment": table_comment,
        "column": column,
        "type": column_type,
        "comment": comment,
    }
    if ordinal_position is not None:
        value["ordinal_position"] = ordinal_position
        value["object_type"] = "table"
    return value


class RefreshMetadataIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = [
            row("alpha", "id", ordinal_position=1),
            row("alpha", "name", "character varying(20)", ordinal_position=2),
            row("beta", "id", "bigint", ordinal_position=1),
        ]

    def test_different_input_order_produces_identical_index_and_sha(self) -> None:
        forward = serialize_metadata_index(self.baseline)
        reverse = serialize_metadata_index(list(reversed(self.baseline)))
        self.assertEqual(forward, reverse)
        self.assertEqual(hashlib.sha256(forward).hexdigest(), hashlib.sha256(reverse).hexdigest())

    def test_serialization_is_fixed_utf8_json_with_trailing_newline(self) -> None:
        content = serialize_metadata_index(self.baseline)
        self.assertTrue(content.endswith(b"\n"))
        self.assertNotIn(b"\r\n", content)
        parsed = json.loads(content.decode("utf-8"))
        self.assertEqual(list(parsed[0]), ["table", "table_comment", "column", "type", "comment"])
        self.assertNotIn("ordinal_position", parsed[0])
        self.assertNotIn("object_type", parsed[0])

    def test_no_change_diff(self) -> None:
        diff = diff_metadata_indexes(self.baseline, deepcopy(self.baseline))
        for key in (
            "added_tables",
            "removed_tables",
            "added_columns",
            "removed_columns",
            "changed_column_types",
            "changed_column_comments",
            "changed_table_comments",
        ):
            self.assertEqual(diff[key], [])
        self.assertEqual(diff["unchanged_table_count"], 2)
        self.assertEqual(diff["unchanged_column_count"], 3)

    def test_added_table(self) -> None:
        current = self.baseline + [row("gamma", "id")]
        self.assertEqual(len(diff_metadata_indexes(self.baseline, current)["added_tables"]), 1)

    def test_removed_table(self) -> None:
        current = [item for item in self.baseline if item["table"] != "beta"]
        self.assertEqual(len(diff_metadata_indexes(self.baseline, current)["removed_tables"]), 1)

    def test_added_column(self) -> None:
        current = self.baseline + [row("alpha", "created_at", "timestamp", ordinal_position=3)]
        self.assertEqual(len(diff_metadata_indexes(self.baseline, current)["added_columns"]), 1)

    def test_removed_column(self) -> None:
        current = [item for item in self.baseline if item["column"] != "name"]
        self.assertEqual(len(diff_metadata_indexes(self.baseline, current)["removed_columns"]), 1)

    def test_changed_column_type(self) -> None:
        current = deepcopy(self.baseline)
        current[0]["type"] = "bigint"
        change = diff_metadata_indexes(self.baseline, current)["changed_column_types"][0]
        self.assertEqual((change["old_type"], change["new_type"]), ("integer", "bigint"))

    def test_changed_column_comment(self) -> None:
        current = deepcopy(self.baseline)
        current[0]["comment"] = "新字段注释"
        change = diff_metadata_indexes(self.baseline, current)["changed_column_comments"][0]
        self.assertEqual((change["old_comment"], change["new_comment"]), ("字段注释", "新字段注释"))

    def test_changed_table_comment(self) -> None:
        current = deepcopy(self.baseline)
        for item in current:
            if item["table"] == "alpha":
                item["table_comment"] = "新表注释"
        change = diff_metadata_indexes(self.baseline, current)["changed_table_comments"][0]
        self.assertEqual(change["table"], "alpha")

    def test_duplicate_column_is_rejected(self) -> None:
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows(self.baseline + [deepcopy(self.baseline[0])])

    def test_conflicting_table_comment_is_rejected(self) -> None:
        current = deepcopy(self.baseline)
        current[1]["table_comment"] = "冲突注释"
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows(current)

    def test_empty_table_name_is_rejected(self) -> None:
        current = deepcopy(self.baseline)
        current[0]["table"] = " "
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows(current)

    def test_empty_column_name_is_rejected(self) -> None:
        current = deepcopy(self.baseline)
        current[0]["column"] = ""
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows(current)

    def test_invalid_root_missing_field_and_invalid_type_are_rejected(self) -> None:
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows({})
        missing = deepcopy(self.baseline)
        del missing[0]["comment"]
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows(missing)
        invalid = deepcopy(self.baseline)
        invalid[0]["type"] = 42
        with self.assertRaises(MetadataValidationError):
            normalize_metadata_rows(invalid)

    def test_manifest_contains_datasource_and_dialect(self) -> None:
        content = serialize_metadata_index(self.baseline)
        manifest = build_metadata_manifest(
            self.baseline,
            content,
            b"baseline\n",
            datasource_id="postgresql-main",
            dialect="postgresql",
            schema="public",
            database_read_only=True,
        )
        self.assertEqual(manifest["datasource_id"], "postgresql-main")
        self.assertEqual(manifest["dialect"], "postgresql")
        self.assertTrue(manifest["database_read_only"])
        self.assertEqual(manifest["object_types"], ["table"])

    def test_read_only_gate_stops_before_catalog_query(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def __enter__(self) -> "FakeCursor":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def execute(self, statement: str, params: object = None) -> None:
                self.statements.append(statement.strip())

            def fetchone(self) -> tuple[str]:
                return ("off",)

        class FakeConnection:
            def __init__(self) -> None:
                self.fake_cursor = FakeCursor()

            def cursor(self) -> FakeCursor:
                return self.fake_cursor

        connection = FakeConnection()
        with self.assertRaises(ReadOnlyGateError):
            extract_postgresql_metadata(connection)
        self.assertEqual(connection.fake_cursor.statements, ["SHOW transaction_read_only"])

    def test_output_directory_inside_project_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_output_directory(PROJECT_ROOT / "forbidden-audit-output")

    def test_baseline_is_not_modified_and_nonempty_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT.parent) as temp_root:
            root = Path(temp_root)
            baseline_path = root / "baseline.json"
            baseline_content = serialize_metadata_index(self.baseline)
            baseline_path.write_bytes(baseline_content)
            before = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

            output_dir = root / "audit"
            write_audit_package(
                output_dir,
                self.baseline,
                baseline_content,
                self.baseline,
                datasource_id="postgresql-main",
                dialect="postgresql",
                schema="public",
                database_read_only=True,
            )
            after = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                [
                    "column_metadata_index.new.json",
                    "metadata_index_diff.json",
                    "metadata_index_summary.md",
                    "metadata_manifest.json",
                ],
            )

            with self.assertRaises(ValueError):
                write_audit_package(
                    output_dir,
                    self.baseline,
                    baseline_content,
                    self.baseline,
                    datasource_id="postgresql-main",
                    dialect="postgresql",
                    schema="public",
                    database_read_only=True,
                )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RefreshMetadataIndexTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
