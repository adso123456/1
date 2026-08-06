"""阶段 E-2A：结构化候选资产硬校验回归测试。

覆盖：PG/MySQL 正常通过；Metadata/DDL 的表集合、列集合、重复、
DDL 解析与 MySQL schema 规范化等全部失败模式。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_asset_validator import validate_candidate_assets
from backend.data_source_catalog import DataSourceCatalogError


def _quote(database_type: str, name: str) -> str:
    quote = "`" if database_type == "mysql" else '"'
    return quote + name.replace(quote, quote * 2) + quote


def _ddl(
    database_type: str,
    schema: str,
    table: str,
    columns,
    primary=(),
    indexes=(),
) -> str:
    quote = "`" if database_type == "mysql" else '"'
    qualified = (
        f"{_quote(database_type, schema)}."
        if database_type == "postgresql" and schema
        else ""
    ) + _quote(database_type, table)
    definitions = [f"  {_quote(database_type, col)} bigint" for col in columns]
    if primary:
        definitions.append(
            "  PRIMARY KEY ("
            + ", ".join(_quote(database_type, col) for col in primary)
            + ")"
        )
    ddl = f"CREATE TABLE {qualified} (\n" + ",\n".join(definitions) + "\n);"
    for index_name, index_columns in indexes:
        columns_sql = ", ".join(
            _quote(database_type, col) + " ASC" for col in index_columns
        )
        method = (
            " USING btree" if database_type == "postgresql" else ""
        )
        ddl += (
            f"\nCREATE UNIQUE INDEX {_quote(database_type, index_name)} "
            f"ON {qualified}{method} ({columns_sql});"
        )
    return ddl


def _meta_item(schema: str, table: str, column: str) -> dict:
    return {
        "schema": schema,
        "table": table,
        "column": column,
        "type": "bigint",
        "comment": f"{column} 注释",
    }


def _pg_fixtures():
    """PG：water_data(id, value) + station_info(id, name)。"""
    scope = [
        _meta_item("public", "water_data", "id"),
        _meta_item("public", "water_data", "value"),
        _meta_item("public", "station_info", "id"),
        _meta_item("public", "station_info", "name"),
    ]
    allowed = {("public", "water_data"), ("public", "station_info")}
    metadata = list(scope)
    ddls = [
        _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value"],
            primary=["id"],
            indexes=[("uq_water_value", ["value"])],
        ),
        _ddl(
            "postgresql",
            "public",
            "station_info",
            ["id", "name"],
            primary=["id"],
        ),
    ]
    return scope, allowed, metadata, ddls


def _mysql_fixtures():
    """MySQL：schema 一律按数据库名 lzh_monitor 规范化；DDL 不限定 schema。"""
    scope = [
        _meta_item("lzh_monitor", "water_data", "id"),
        _meta_item("lzh_monitor", "water_data", "value"),
        _meta_item("lzh_monitor", "station_info", "id"),
        _meta_item("lzh_monitor", "station_info", "name"),
    ]
    allowed = {("lzh_monitor", "water_data"), ("lzh_monitor", "station_info")}
    metadata = list(scope)
    ddls = [
        _ddl(
            "mysql",
            "",
            "water_data",
            ["id", "value"],
            primary=["id"],
        ),
        _ddl(
            "mysql",
            "",
            "station_info",
            ["id", "name"],
            primary=["id"],
        ),
    ]
    return scope, allowed, metadata, ddls


def _run(
    root: Path,
    *,
    database_type: str,
    database_name: str,
    scope,
    allowed,
    metadata,
    ddls,
):
    metadata_path = root / "column_metadata_index.json"
    ddl_path = root / "ddl_memories.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )
    ddl_path.write_text(
        json.dumps(ddls, ensure_ascii=False),
        encoding="utf-8",
    )
    return validate_candidate_assets(
        database_type=database_type,
        database_name=database_name,
        selected_scope=scope,
        allowed_tables=allowed,
        metadata_path=metadata_path,
        ddl_path=ddl_path,
    )


def _expect_error(
    root: Path,
    *,
    database_type: str,
    database_name: str,
    scope,
    allowed,
    metadata,
    ddls,
    keyword: str,
) -> None:
    try:
        _run(
            root,
            database_type=database_type,
            database_name=database_name,
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
        )
    except DataSourceCatalogError as exc:
        assert keyword in str(exc), f"期望包含 {keyword!r}，实际 {exc}"
    else:
        raise AssertionError(f"应抛 DataSourceCatalogError（{keyword}）")


def test_pg_normal_pass() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-pg-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        result = _run(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
        )
        assert result["metadata_tables"] == 2
        assert result["ddl_columns"] == 4


def test_mysql_normal_pass() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-my-") as directory:
        scope, allowed, metadata, ddls = _mysql_fixtures()
        result = _run(
            Path(directory),
            database_type="mysql",
            database_name="lzh_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
        )
        assert result["metadata_tables"] == 2
        assert result["ddl_tables"] == 2


def test_metadata_extra_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-meta-tbl-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [*metadata, _meta_item("public", "forbidden_table", "id")]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="多出非 allowed_tables",
        )


def test_metadata_missing_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-meta-miss-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [
            item for item in metadata if item["table"] != "station_info"
        ]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="缺少 allowed_tables",
        )


def test_metadata_extra_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-meta-col-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [*metadata, _meta_item("public", "water_data", "secret_new")]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="多出非 selected_scope",
        )


def test_metadata_missing_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-meta-colmiss-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [
            item for item in metadata if item["column"] != "value"
        ]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="缺少 selected_scope",
        )


def test_metadata_duplicate_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-meta-dup-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [*metadata, _meta_item("public", "water_data", "id")]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="重复列",
        )


def test_ddl_extra_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-tbl-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls = [
            *ddls,
            _ddl(
                "postgresql",
                "public",
                "forbidden_table",
                ["id"],
                primary=["id"],
            ),
        ]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="多出非 allowed_tables",
        )


def test_ddl_missing_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-miss-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls = ddls[:1]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="缺少 allowed_tables",
        )


def test_ddl_extra_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-col-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value", "secret_new"],
            primary=["id"],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="多出非 selected_scope",
        )


def test_ddl_missing_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-colmiss-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id"],
            primary=["id"],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="缺少 selected_scope",
        )


def test_ddl_wrong_schema() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-schema-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "wrong_schema",
            "water_data",
            ["id", "value"],
            primary=["id"],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="allowed_tables",
        )


def test_ddl_unparseable() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-bad-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = "THIS IS NOT A DDL"
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 无法解析",
        )


def test_ddl_duplicate_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-duptbl-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls = [
            *ddls,
            _ddl(
                "postgresql",
                "public",
                "water_data",
                ["id", "value"],
                primary=["id"],
            ),
        ]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 重复表",
        )


def test_ddl_duplicate_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-ddl-dupcol-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "id"],
            primary=["id"],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 重复列",
        )


def test_mysql_schema_not_normalized() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-my-schema-") as directory:
        scope, allowed, metadata, ddls = _mysql_fixtures()
        metadata[0] = _meta_item("other_schema", "water_data", "id")
        _expect_error(
            Path(directory),
            database_type="mysql",
            database_name="lzh_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="MySQL Schema 身份未按数据源数据库名规范化",
        )




def test_primary_key_references_nondeclared_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f1a-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value"],
            primary=["secret_new"],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="PRIMARY KEY 引用未声明列",
        )


def test_primary_key_references_unselected_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f1b-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value", "secret_new"],
            primary=["secret_new"],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="selected_scope",
        )


def test_index_references_nondeclared_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f1c-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value"],
            primary=["id"],
            indexes=[("idx_secret", ["secret_new"])],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="索引引用未声明列",
        )


def test_index_references_unselected_column() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f1d-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value", "secret_new"],
            primary=["id"],
            indexes=[("idx_secret", ["secret_new"])],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="selected_scope",
        )


def test_index_mixed_valid_and_invalid_columns() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f1e-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql",
            "public",
            "water_data",
            ["id", "value"],
            primary=["id"],
            indexes=[("idx_mix", ["id", "secret_new"])],
        )
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="索引引用未声明列",
        )


def test_ddl_extra_token_before_table_name() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f2a-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = "CREATE TABLE unexpected_token \"public\".\"water_data\" ("
        ddls[0] += "\n  \"id\" bigint,\n  \"value\" numeric\n);"
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 无法解析",
        )


def test_ddl_extra_token_after_table_name() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f2b-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = "CREATE TABLE \"public\".\"water_data\" junk ("
        ddls[0] += "\n  \"id\" bigint,\n  \"value\" numeric\n);"
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 无法解析",
        )


def test_pg_ddl_uses_backticks_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f2c-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = "CREATE TABLE `public`.`water_data` ("
        ddls[0] += "\n  `id` bigint,\n  `value` numeric\n);"
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 无法解析",
        )


def test_mysql_ddl_uses_double_quotes_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f2d-") as directory:
        scope, allowed, metadata, ddls = _mysql_fixtures()
        ddls[0] = "CREATE TABLE \"water_data\" ("
        ddls[0] += "\n  \"id\" bigint,\n  \"value\" numeric\n);"
        _expect_error(
            Path(directory),
            database_type="mysql",
            database_name="lzh_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 无法解析",
        )


def test_ddl_three_level_qualified_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f2e-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = "CREATE TABLE \"a\".\"b\".\"c\" ("
        ddls[0] += "\n  \"id\" bigint\n);"
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="DDL 无法解析",
        )


def test_ddl_quoted_identifier_with_semicolon_passes() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f3a-") as directory:
        scope = [_meta_item("public", "monitor;archive", "id")]
        allowed = {("public", "monitor;archive")}
        metadata = list(scope)
        ddls = [_ddl(
            "postgresql",
            "public",
            "monitor;archive",
            ["id"],
            primary=["id"],
        )]
        result = _run(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
        )
        assert result["ddl_tables"] == 1


def test_ddl_quoted_identifier_with_parens_passes() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f3b-") as directory:
        scope = [_meta_item("public", "water_data", "value(t)")]
        allowed = {("public", "water_data")}
        metadata = list(scope)
        ddls = [_ddl(
            "postgresql",
            "public",
            "water_data",
            ["value(t)"],
            primary=["value(t)"],
        )]
        result = _run(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
        )
        assert result["ddl_columns"] == 1


def test_metadata_contains_string_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f4a-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [*metadata, "unexpected-content"]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="不是对象",
        )


def test_metadata_contains_null_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f4b-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [*metadata, None]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="不是对象",
        )


def test_metadata_contains_array_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-f4c-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        metadata = [*metadata, [1, 2]]
        _expect_error(
            Path(directory),
            database_type="postgresql",
            database_name="gt_monitor",
            scope=scope,
            allowed=allowed,
            metadata=metadata,
            ddls=ddls,
            keyword="不是对象",
        )

def test_index_illegal_first_then_legal_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-midx1-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql", "public", "water_data", ["id", "value"],
            primary=["id"],
            indexes=[("idx_illegal", ["secret_new"]), ("idx_valid", ["id"])],
        )
        _expect_error(
            Path(directory), database_type="postgresql",
            database_name="gt_monitor", scope=scope, allowed=allowed,
            metadata=metadata, ddls=ddls, keyword="索引引用未声明列",
        )


def test_index_legal_first_then_illegal_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-midx2-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql", "public", "water_data", ["id", "value"],
            primary=["id"],
            indexes=[("idx_valid", ["id"]), ("idx_illegal", ["secret_new"])],
        )
        _expect_error(
            Path(directory), database_type="postgresql",
            database_name="gt_monitor", scope=scope, allowed=allowed,
            metadata=metadata, ddls=ddls, keyword="索引引用未声明列",
        )


def test_two_legal_indexes_pass() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-midx3-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = _ddl(
            "postgresql", "public", "water_data", ["id", "value"],
            primary=["id"],
            indexes=[("idx_id", ["id"]), ("idx_value", ["value"])],
        )
        result = _run(
            Path(directory), database_type="postgresql",
            database_name="gt_monitor", scope=scope, allowed=allowed,
            metadata=metadata, ddls=ddls,
        )
        assert result["ddl_tables"] == 2


def test_duplicate_primary_key_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-pkdup-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = (
            'CREATE TABLE "public"."water_data" (\n'
            '  "id" bigint,\n'
            '  "value" numeric,\n'
            '  PRIMARY KEY ("id"),\n'
            '  PRIMARY KEY ("value")\n'
            ');'
        )
        _expect_error(
            Path(directory), database_type="postgresql",
            database_name="gt_monitor", scope=scope, allowed=allowed,
            metadata=metadata, ddls=ddls, keyword="重复 PRIMARY KEY",
        )


def test_first_illegal_pk_then_legal_pk_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-pkmix-") as directory:
        scope, allowed, metadata, ddls = _pg_fixtures()
        ddls[0] = (
            'CREATE TABLE "public"."water_data" (\n'
            '  "id" bigint,\n'
            '  "value" numeric,\n'
            '  PRIMARY KEY ("secret_new"),\n'
            '  PRIMARY KEY ("id")\n'
            ');'
        )
        _expect_error(
            Path(directory), database_type="postgresql",
            database_name="gt_monitor", scope=scope, allowed=allowed,
            metadata=metadata, ddls=ddls, keyword="重复 PRIMARY KEY",
        )


def test_mysql_two_segment_qualified_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2a-mydot-") as directory:
        scope, allowed, metadata, ddls = _mysql_fixtures()
        ddls[0] = (
            'CREATE TABLE `lzh_monitor`.`water_data` (\n'
            '  `id` bigint,\n'
            '  `value` numeric\n'
            ');'
        )
        _expect_error(
            Path(directory), database_type="mysql",
            database_name="lzh_monitor", scope=scope, allowed=allowed,
            metadata=metadata, ddls=ddls, keyword="DDL 无法解析",
        )

if __name__ == "__main__":
    import traceback

    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(
        f"\n{len([1 for n in globals() if n.startswith('test_')]) - failed}/"
        f"{len([1 for n in globals() if n.startswith('test_')])} passed"
    )
    raise SystemExit(1 if failed else 0)
