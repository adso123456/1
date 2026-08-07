"""阶段 E-2B：SQLGuard 物理表列身份分析回归测试。

覆盖：PG/MySQL 表身份解析、未限定列唯一/歧义、通配符（* / table.* /
alias.* / COUNT(*)）、函数与聚合输入列、别名解析、CTE/子查询/派生表
物理 lineage、CTE 内非法表列、无法解析 lineage 拒绝，以及无 schema
上下文的旧路径兼容。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.mysql_sql_guard import MySQLSQLGuard
from backend.sql_guard import SQLGuard


def _write_index(root: Path, rows) -> Path:
    path = root / "metadata_index.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def _pg_rows():
    return [
        {"schema": "public", "table": "t", "column": "id"},
        {"schema": "public", "table": "t", "column": "value"},
        {"schema": "public", "table": "t", "column": "station_id"},
        {"schema": "public", "table": "u", "column": "id"},
        {"schema": "public", "table": "u", "column": "code"},
        {"schema": "other", "table": "t", "column": "id"},
        {"schema": "other", "table": "t", "column": "name"},
    ]


def _mysql_rows():
    return [
        {"schema": "lzh_monitor", "table": "t", "column": "id"},
        {"schema": "lzh_monitor", "table": "t", "column": "value"},
        {"schema": "lzh_monitor", "table": "u", "column": "id"},
        {"schema": "lzh_monitor", "table": "u", "column": "code"},
    ]


def _pg_guard(root: Path) -> SQLGuard:
    return SQLGuard(
        _write_index(root, _pg_rows()),
        database_type="postgresql",
        default_schema="public",
    )


def _mysql_guard(root: Path) -> MySQLSQLGuard:
    return MySQLSQLGuard(
        _write_index(root, _mysql_rows()),
        database_type="mysql",
        default_schema="lzh_monitor",
    )


def _assert_ok(guard, sql: str):
    result = guard.validate(sql, query="")
    assert result.passed, f"应通过：{sql} -> {result.reason}"
    return result


def _assert_rejected(guard, sql: str, keyword: str = ""):
    result = guard.validate(sql, query="")
    assert not result.passed, f"应拒绝：{sql}"
    if keyword:
        assert keyword in result.reason, (
            f"期望 {keyword!r}，实际 {result.reason}"
        )
    return result


def test_pg_quoted_qualified_table() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-pgq-") as directory:
        result = _assert_ok(
            _pg_guard(Path(directory)),
            'SELECT "id" FROM "public"."t"',
        )
        assert ("public", "t") in result.used_physical_tables
        assert ("public", "t", "id") in result.used_physical_columns


def test_pg_unqualified_table_single_schema() -> None:
    # public 下无同名歧义（other.t 存在，但本用例限定 public 上下文验证多 schema 拒绝在另测）。
    with tempfile.TemporaryDirectory(prefix="sg-pgu-") as directory:
        guard = SQLGuard(
            _write_index(Path(directory), _pg_rows()[:3]),
            database_type="postgresql",
            default_schema="public",
        )
        result = _assert_ok(guard, 'SELECT value FROM "t"')
        assert ("public", "t", "value") in result.used_physical_columns


def test_multi_table_unqualified_unique_column() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-unique-") as directory:
        guard = _pg_guard(Path(directory))
        sql = (
            'SELECT value FROM "public"."t" '
            'JOIN "public"."u" ON "public"."t"."id" = "public"."u"."id"'
        )
        result = _assert_ok(guard, sql)
        assert ("public", "t", "value") in result.used_physical_columns
        assert ("public", "t", "id") in result.used_physical_columns
        assert ("public", "u", "id") in result.used_physical_columns


def test_multi_table_unqualified_ambiguous_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-ambig-") as directory:
        guard = _pg_guard(Path(directory))
        sql = (
            'SELECT id FROM "public"."t" '
            'JOIN "public"."u" ON "public"."t"."id" = "public"."u"."id"'
        )
        result = _assert_rejected(guard, sql, "歧义")
        assert result.ambiguous_columns


def test_pg_unqualified_cross_schema_ambiguous_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-xschema-") as directory:
        guard = _pg_guard(Path(directory))
        result = _assert_rejected(guard, 'SELECT id FROM "t"', "无法解析")
        assert result.unresolved_lineage


def _cross_schema_rows():
    return [
        {"schema": "public", "table": "t", "column": "id"},
        {"schema": "public", "table": "t", "column": "safe"},
        {"schema": "other", "table": "t", "column": "id"},
        {"schema": "other", "table": "t", "column": "secret"},
    ]


def test_cross_schema_alias_columns_bound_to_own_table() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-xs-alias-") as directory:
        guard = SQLGuard(
            _write_index(Path(directory), _cross_schema_rows()),
            database_type="postgresql",
            default_schema="public",
        )
        sql = (
            'SELECT a.safe, b.secret FROM "public"."t" AS a '
            'JOIN "other"."t" AS b ON a.id = b.id'
        )
        result = _assert_ok(guard, sql)
        assert ("public", "t", "safe") in result.used_physical_columns
        assert ("other", "t", "secret") in result.used_physical_columns
        assert ("other", "t", "safe") not in result.used_physical_columns
        assert ("public", "t", "secret") not in result.used_physical_columns
        assert result.used_physical_tables == {
            ("public", "t"),
            ("other", "t"),
        }


def test_cross_schema_second_table_scope_out_column_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-xs-out-") as directory:
        rows = [
            {"schema": "public", "table": "t", "column": "id"},
            {"schema": "public", "table": "t", "column": "safe"},
            {"schema": "other", "table": "t", "column": "id"},
        ]
        guard = SQLGuard(
            _write_index(Path(directory), rows),
            database_type="postgresql",
            default_schema="public",
        )
        sql = (
            'SELECT b.safe FROM "public"."t" AS a '
            'JOIN "other"."t" AS b ON a.id = b.id'
        )
        result = _assert_rejected(guard, sql, "未知字段")
        assert result.unknown_columns


def test_three_part_qualified_column_resolves_schema() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-xs-3part-") as directory:
        guard = SQLGuard(
            _write_index(Path(directory), _cross_schema_rows()),
            database_type="postgresql",
            default_schema="public",
        )
        sql = (
            'SELECT "other"."t"."secret" FROM "public"."t" AS a '
            'JOIN "other"."t" AS b ON a.id = b.id'
        )
        result = _assert_ok(guard, sql)
        assert ("other", "t", "secret") in result.used_physical_columns
        assert ("public", "t", "secret") not in result.used_physical_columns


def test_unqualified_same_name_table_ambiguous_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-xs-ambig-") as directory:
        guard = SQLGuard(
            _write_index(Path(directory), _cross_schema_rows()),
            database_type="postgresql",
            default_schema="public",
        )
        sql = (
            'SELECT t.safe FROM "public"."t" '
            'JOIN "other"."t" ON "public"."t"."id" = "other"."t"."id"'
        )
        result = _assert_rejected(guard, sql, "歧义")
        assert result.ambiguous_columns


def test_mysql_backfills_database_name() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-my-") as directory:
        result = _assert_ok(
            _mysql_guard(Path(directory)),
            "SELECT `id` FROM `t`",
        )
        assert ("lzh_monitor", "t") in result.used_physical_tables
        assert ("lzh_monitor", "t", "id") in result.used_physical_columns


def test_mysql_qualified_same_database_accepted() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-myq-") as directory:
        result = _assert_ok(
            _mysql_guard(Path(directory)),
            "SELECT `id` FROM `lzh_monitor`.`t`",
        )
        assert ("lzh_monitor", "t", "id") in result.used_physical_columns


def test_select_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-star-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT * FROM "public"."t"',
            "通配符",
        )
        assert any(item["expression"] == "*" for item in result.wildcard_references)


def test_table_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-tstar-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT t.* FROM "public"."t"',
            "通配符",
        )
        assert result.wildcard_references


def test_alias_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-astar-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT a.* FROM "public"."t" AS a',
            "通配符",
        )
        assert result.wildcard_references


def test_quoted_table_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-qstar-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT "monitor_data".* FROM "public"."monitor_data"',
            "通配符",
        )
        assert result.wildcard_references


def test_mysql_quoted_table_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-mystar-") as directory:
        result = _assert_rejected(
            _mysql_guard(Path(directory)),
            "SELECT `monitor_data`.* FROM `monitor_data`",
            "通配符",
        )
        assert result.wildcard_references


def test_parenthesized_alias_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-pstar-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT (m).* FROM "public"."t" AS m',
            "通配符",
        )
        assert result.wildcard_references


def test_spaced_alias_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-sstar-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT m . * FROM "public"."t" AS m',
            "通配符",
        )
        assert result.wildcard_references


def test_count_table_star_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-cstar-") as directory:
        result = _assert_rejected(
            _pg_guard(Path(directory)),
            'SELECT COUNT(t.*) FROM "public"."t"',
            "通配符",
        )
        assert result.wildcard_references


def test_count_star_allowed() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-count-") as directory:
        result = _assert_ok(
            _pg_guard(Path(directory)),
            'SELECT COUNT(*) FROM "public"."t"',
        )
        assert not result.wildcard_references


def test_count_star_spaced_allowed() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-csp-") as directory:
        result = _assert_ok(
            _pg_guard(Path(directory)),
            'SELECT COUNT( * ) FROM "public"."t"',
        )
        assert not result.wildcard_references


def test_function_input_columns_tracked() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-fn-") as directory:
        result = _assert_ok(
            _pg_guard(Path(directory)),
            'SELECT SUM(value) FROM "public"."t"',
        )
        assert ("public", "t", "value") in result.used_physical_columns


def test_aggregate_alias_input_columns_tracked() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-agg-") as directory:
        result = _assert_ok(
            _pg_guard(Path(directory)),
            'SELECT AVG(value) AS avg_value FROM "public"."t"',
        )
        assert ("public", "t", "value") in result.used_physical_columns


def test_alias_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-alias-") as directory:
        result = _assert_ok(
            _pg_guard(Path(directory)),
            'SELECT a.value FROM "public"."t" AS a',
        )
        assert ("public", "t", "value") in result.used_physical_columns


def test_cte_physical_lineage() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-cte-") as directory:
        sql = (
            'WITH x AS (SELECT station_id, SUM(value) AS total '
            'FROM "public"."t" GROUP BY station_id) '
            'SELECT station_id, total FROM x'
        )
        result = _assert_ok(_pg_guard(Path(directory)), sql)
        assert ("public", "t", "station_id") in result.used_physical_columns
        assert ("public", "t", "value") in result.used_physical_columns
        assert result.used_physical_tables == {("public", "t")}


def test_subquery_physical_lineage() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-sub-") as directory:
        sql = (
            'SELECT id FROM "public"."t" '
            'WHERE id IN (SELECT id FROM "public"."u")'
        )
        result = _assert_ok(_pg_guard(Path(directory)), sql)
        assert ("public", "t", "id") in result.used_physical_columns
        assert ("public", "u", "id") in result.used_physical_columns


def test_derived_table_alias_lineage() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-derived-") as directory:
        sql = 'SELECT d.value FROM (SELECT id, value FROM "public"."t") AS d'
        result = _assert_ok(_pg_guard(Path(directory)), sql)
        assert ("public", "t", "value") in result.used_physical_columns


def test_cte_invalid_table_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-cte-tbl-") as directory:
        sql = (
            'WITH x AS (SELECT id FROM "public"."nope") '
            'SELECT id FROM x'
        )
        _assert_rejected(_pg_guard(Path(directory)), sql, "未知表")


def test_cte_invalid_column_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-cte-col-") as directory:
        sql = (
            'WITH x AS (SELECT bad_col FROM "public"."t") '
            'SELECT bad_col FROM x'
        )
        _assert_rejected(_pg_guard(Path(directory)), sql, "未知字段")


def test_unresolvable_lineage_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-lineage-") as directory:
        sql = (
            'WITH x AS (SELECT id FROM "public"."t") '
            'SELECT missing FROM x'
        )
        _assert_rejected(_pg_guard(Path(directory)), sql, "未知字段")


def test_legacy_without_schema_context_still_works() -> None:
    with tempfile.TemporaryDirectory(prefix="sg-legacy-") as directory:
        guard = SQLGuard(_write_index(Path(directory), _pg_rows()))
        result = guard.validate('SELECT id FROM "t"', query="")
        assert result.passed
        assert not result.used_physical_tables
        assert "t.id" in result.used_columns


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
