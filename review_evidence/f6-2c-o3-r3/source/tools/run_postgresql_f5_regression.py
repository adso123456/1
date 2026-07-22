from __future__ import annotations

import argparse
import asyncio
import ast
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SUITE = PROJECT_ROOT / "training" / "regression" / "postgresql_f5_regression_v1.json"
FORMAL_RUNTIME = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
EXPECTED_FORMAL_RECORD_COUNT = 198
EXPECTED_FORMAL_SHA256 = "d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992"
EXPECTED_SUITE_SHA256 = "6e8e3e7fcfc57f7fd1b815dd0fec7263245c2439c4f97fb895b5248d2cc84e6a"
EARLY_MEMORY_MODULES = ("backend.memory", "step4_server")
REQUIRED_CASE_FIELDS = {
    "case_id",
    "category",
    "question",
    "expected_tables",
    "limit_max",
    "expected_columns",
    "sql_constraints",
    "result_policy",
}
REQUIRED_CONSTRAINT_FIELDS = {
    "require_join",
    "forbid_join",
    "require_left_join",
    "require_group_by",
    "require_order_desc",
    "forbidden_tables",
    "forbidden_columns",
    "forbid_select_star",
    "forbid_distinct",
    "forbid_where",
    "forbid_null_filter",
    "expected_chart_types",
    "require_chart_title",
    "require_chart_fields",
}
REQUIRED_POLICY_FIELDS = {"exact_rows", "minimum_rows", "columns_mode", "require_answer"}
REQUIRED_MEMORY_CASE_FIELDS = {
    "case_id",
    "question",
    "expected_sample_id",
    "expected_training_level",
    "expectation",
    "search_top_k",
    "enhancer_top_k",
    "max_rank",
}


class FormalRuntimeChanged(RuntimeError):
    pass


def assert_no_early_memory_modules(modules: dict[str, Any] | None = None) -> None:
    loaded = sys.modules if modules is None else modules
    found = [name for name in EARLY_MEMORY_MODULES if name in loaded]
    if found:
        raise RuntimeError(f"EARLY_MEMORY_MODULE_IMPORT: {', '.join(found)}")


def validate_isolation_paths(data_dir: Path, formal_runtime: Path = FORMAL_RUNTIME) -> tuple[Path, Path]:
    validation = data_dir.resolve()
    formal = formal_runtime.resolve()
    if validation == formal:
        raise RuntimeError("FORMAL_DATA_DIR_FORBIDDEN")
    return validation, formal


def configure_parent_environment(data_dir: Path, agent_dir: Path) -> None:
    os.environ["VANNA_DATA_DIR"] = str(data_dir)
    os.environ["AGENT_DATA_DIR"] = str(agent_dir)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["VANNA_DISABLE_LEGACY_SQL_EXAMPLES"] = "0"
    if Path(os.environ["VANNA_DATA_DIR"]).resolve() != data_dir.resolve():
        raise RuntimeError("PARENT_DATA_DIR_ASSERTION_FAILED")


def directory_state(path: Path) -> dict[str, Any]:
    from training.sop.storage_snapshot import build_directory_manifest

    manifest = build_directory_manifest(path)
    uri = path.joinpath("chroma.sqlite3").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        record_count = int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
    return {
        "path": str(path),
        "record_count": record_count,
        "sha256": manifest.content_sha256,
        "file_count": manifest.file_count,
    }


def resolve_formal_monitor_baseline(
    expected_record_count: int | None, expected_sha256: str | None
) -> tuple[int, str]:
    if (expected_record_count is None) != (expected_sha256 is None):
        raise ValueError(
            "--expected-formal-record-count 和 --expected-formal-sha256 必须同时提供"
        )
    if expected_record_count is None:
        return EXPECTED_FORMAL_RECORD_COUNT, EXPECTED_FORMAL_SHA256
    if expected_record_count < 0:
        raise ValueError("--expected-formal-record-count 不能为负数")
    if re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256)) is None:
        raise ValueError("--expected-formal-sha256 必须是64位小写十六进制")
    return expected_record_count, str(expected_sha256)


def formal_checkpoint(
    label: str,
    checkpoints: list[dict[str, Any]],
    evidence_dir: Path,
    *,
    expected_record_count: int,
    expected_sha256: str,
    state_reader: Callable[[Path], dict[str, Any]] = directory_state,
) -> None:
    state = state_reader(FORMAL_RUNTIME)
    passed = (
        state["record_count"] == expected_record_count
        and state["sha256"] == expected_sha256
    )
    checkpoints.append(
        {
            "label": label,
            "checked_at": datetime.now().astimezone().isoformat(),
            **state,
            "passed": passed,
        }
    )
    write_json(
        evidence_dir / "formal-monitor-checkpoints.json",
        {
            "expected_record_count": expected_record_count,
            "expected_sha256": expected_sha256,
            "formal_runtime_fail_fast_triggered": not passed,
            "checkpoints": checkpoints,
        },
    )
    if not passed:
        raise FormalRuntimeChanged(f"FORMAL_RUNTIME_FAIL_FAST: {label}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行版本化的 PostgreSQL F5 回归基线")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--agent-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--expected-formal-record-count", type=int)
    parser.add_argument("--expected-formal-sha256")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_and_validate_suite(path: Path) -> tuple[dict[str, Any], str]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    cases = suite.get("cases")
    memory_cases = suite.get("memory_cases")
    errors: list[str] = []
    if suite.get("schema_version") != "1.0":
        errors.append("schema_version 必须为 1.0")
    if not isinstance(suite.get("suite_id"), str) or not suite["suite_id"].strip():
        errors.append("suite_id 缺失")
    if not isinstance(cases, list):
        errors.append("cases 必须为数组")
        cases = []
    if suite.get("case_count") != len(cases):
        errors.append("case_count 与 cases 长度不一致")
    ids: list[str] = []
    for index, case in enumerate(cases):
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            errors.append(f"cases[{index}] 缺少字段: {sorted(missing)}")
            continue
        ids.append(str(case["case_id"]))
        if case["category"] not in {"f2_fixed", "batch_target"}:
            errors.append(f"{case['case_id']} category 非法")
        if not case["question"] or not case["expected_tables"] or not case["expected_columns"]:
            errors.append(f"{case['case_id']} 问题、表或字段为空")
        if not isinstance(case["limit_max"], int) or case["limit_max"] <= 0:
            errors.append(f"{case['case_id']} limit_max 非法")
        missing_constraints = REQUIRED_CONSTRAINT_FIELDS - set(case["sql_constraints"])
        missing_policy = REQUIRED_POLICY_FIELDS - set(case["result_policy"])
        if missing_constraints:
            errors.append(f"{case['case_id']} sql_constraints 缺少: {sorted(missing_constraints)}")
        if missing_policy:
            errors.append(f"{case['case_id']} result_policy 缺少: {sorted(missing_policy)}")
        if case["result_policy"].get("columns_mode") not in {"contains", "exact"}:
            errors.append(f"{case['case_id']} columns_mode 非法")
    if len(ids) != len(set(ids)):
        errors.append("case_id 不唯一")
    if not isinstance(memory_cases, list):
        errors.append("memory_cases 必须为数组")
        memory_cases = []
    if suite.get("memory_case_count") != len(memory_cases):
        errors.append("memory_case_count 与 memory_cases 长度不一致")
    memory_ids: list[str] = []
    for index, case in enumerate(memory_cases):
        missing = REQUIRED_MEMORY_CASE_FIELDS - set(case)
        if missing:
            errors.append(f"memory_cases[{index}] 缺少字段: {sorted(missing)}")
            continue
        memory_ids.append(str(case["case_id"]))
        if not str(case["expected_sample_id"]).strip():
            errors.append(f"memory_cases[{index}] expected_sample_id 为空")
        if not str(case["question"]).strip():
            errors.append(f"memory_cases[{index}] question 为空")
        if case["expectation"] not in {"present_and_injected", "absent"}:
            errors.append(f"memory_cases[{index}] expectation 非法")
        for field in ("search_top_k", "enhancer_top_k", "max_rank"):
            value = case[field]
            if type(value) is not int or value <= 0:
                errors.append(f"memory_cases[{index}] {field} 必须为正整数")
        if (
            type(case["max_rank"]) is int
            and type(case["search_top_k"]) is int
            and case["max_rank"] > case["search_top_k"]
        ):
            errors.append(f"memory_cases[{index}] max_rank 不得大于 search_top_k")
    if len(memory_ids) != len(set(memory_ids)):
        errors.append("memory case_id 不唯一")
    if errors:
        raise ValueError("; ".join(errors))
    return suite, canonical_sha256(suite)


def _regex(pattern: str, sql: str) -> bool:
    return bool(re.search(pattern, sql, flags=re.I | re.S))


def validate_case(case: dict[str, Any], case_result: dict[str, Any]) -> dict[str, Any]:
    from backend.sql_guard import SQLGuard

    result = case_result.get("result") or {}
    sql = str(result.get("sql") or "")
    columns = list(result.get("columns") or [])
    row_count = int(result.get("row_count") or 0)
    constraints = case["sql_constraints"]
    policy = case["result_policy"]
    guard = SQLGuard().validate(
        sql=sql,
        query=case["question"],
        deterministic_candidate_tables=case["expected_tables"],
    ) if sql else None
    used_tables = list(guard.used_tables) if guard else []
    used_columns = list(guard.used_columns) if guard else []
    statements = [part for part in sql.split(";") if part.strip()]
    checks: dict[str, bool] = {
        "http_200": result.get("http_status") == 200,
        "no_sse_error": not result.get("errors"),
        "sql_present": bool(sql),
        "sql_guard_pass": bool(guard and guard.passed),
        "used_tables_exact": set(used_tables) == set(case["expected_tables"]),
        "single_select_only": bool(
            _regex(r"^\s*select\b", sql)
            and len(statements) == 1
            and not _regex(r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge)\b", sql)
        ),
        "execution_success": result.get("execution_success") is True,
        "result_nonempty": row_count >= int(policy["minimum_rows"]),
        "answer_present": not policy["require_answer"] or bool(str(result.get("answer") or "").strip()),
    }
    limit = re.search(r"\blimit\s+(\d+)", sql, flags=re.I)
    checks["limit_at_most"] = bool(limit and int(limit.group(1)) <= int(case["limit_max"]))
    if policy["exact_rows"] is not None:
        checks["result_row_count_exact"] = row_count == int(policy["exact_rows"])
    if policy["columns_mode"] == "exact":
        checks["expected_columns"] = columns == list(case["expected_columns"])
    else:
        checks["expected_columns"] = set(case["expected_columns"]).issubset(columns)

    join_present = _regex(r"\bjoin\b[\s\S]*\bon\b", sql)
    checks["require_join"] = not constraints["require_join"] or join_present
    checks["forbid_join"] = not constraints["forbid_join"] or not _regex(r"\bjoin\b", sql)
    checks["require_left_join"] = not constraints["require_left_join"] or _regex(r"\bleft\s+(?:outer\s+)?join\b", sql)
    checks["require_group_by"] = not constraints["require_group_by"] or _regex(r"\bgroup\s+by\b", sql)
    checks["require_order_desc"] = not constraints["require_order_desc"] or _regex(r"\border\s+by\b[\s\S]*\bdesc\b", sql)
    checks["forbidden_tables"] = not (set(used_tables) & set(constraints["forbidden_tables"]))
    checks["forbidden_columns"] = not any(
        used.rsplit(".", 1)[-1].lower() in {str(item).lower() for item in constraints["forbidden_columns"]}
        for used in used_columns
    )
    checks["forbid_select_star"] = not constraints["forbid_select_star"] or not _regex(r"\bselect\s+(?:\w+\.)?\*", sql)
    checks["forbid_distinct"] = not constraints["forbid_distinct"] or not _regex(r"\bdistinct\b", sql)
    checks["forbid_where"] = not constraints["forbid_where"] or not _regex(r"\bwhere\b", sql)
    checks["forbid_null_filter"] = not constraints["forbid_null_filter"] or not _regex(r"\bis\s+(?:not\s+)?null\b", sql)

    chart_specs = list(result.get("chart_specs") or [])
    expected_chart_types = set(constraints["expected_chart_types"])
    matching_charts = [item for item in chart_specs if item.get("type") in expected_chart_types]
    checks["expected_chart_types"] = not expected_chart_types or bool(matching_charts)
    checks["require_chart_title"] = not constraints["require_chart_title"] or any(
        str(item.get("title") or "").strip() for item in matching_charts
    )
    checks["require_chart_fields"] = not constraints["require_chart_fields"] or any(
        item.get("xField") in columns
        and isinstance(item.get("yFields"), list)
        and bool(item["yFields"])
        and set(item["yFields"]).issubset(columns)
        for item in matching_charts
    )
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "question": case["question"],
        "sql": sql,
        "used_tables": used_tables,
        "used_columns": used_columns,
        "columns": columns,
        "row_count": row_count,
        "checks": checks,
        "failed_checks": failed,
        "accepted": not failed,
    }


def run_http_case(
    case: dict[str, Any],
    agent_dir: Path,
    *,
    post_sse: Any,
    query_result_files: Any,
    read_csv: Any,
    extract_chart_specs: Any,
) -> dict[str, Any]:
    before = query_result_files(agent_dir)
    response = post_sse(case["question"])
    after = query_result_files(agent_dir)
    new_csv = sorted(after - before)
    response["csv_files"] = new_csv
    dataframe_events = list(response.get("dataframe_events") or [])
    primary = dataframe_events[0] if dataframe_events else {}
    response["sql"] = str(primary.get("sql") or "")
    response["columns"] = list(primary.get("columns") or [])
    response["row_count"] = int(primary.get("row_count") or 0)
    response["execution_success"] = primary.get("execution_success") is True
    output_file = str(primary.get("output_file") or "")
    matching_csv = [item for item in new_csv if Path(item).name == output_file]
    response["csv_file"] = matching_csv[0] if matching_csv else ""
    response["chart_specs"] = extract_chart_specs(str(response.get("answer") or ""))
    return {
        "id": case["case_id"],
        "query": case["question"],
        "parent_memory_diagnostics_executed": False,
        "result": response,
    }


def sanitized_case_result(case_result: dict[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(case_result, ensure_ascii=False))
    result = output.get("result") or {}
    answer = str(result.get("answer") or "")
    result["answer"] = {"redacted": True, "nonempty": bool(answer.strip()), "character_count": len(answer)}
    rows = result.get("event_rows") or []
    result["event_rows"] = {"redacted": True, "row_count_in_stream": len(rows) if isinstance(rows, list) else 0}
    for event in result.get("dataframe_events") or []:
        event_rows = event.get("rows") or []
        event["rows"] = {
            "redacted": True,
            "row_count_in_stream": len(event_rows)
            if isinstance(event_rows, list)
            else 0,
        }
    result.pop("preview_first3_anonymized", None)
    output["result"] = result
    return output


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def self_test(suite_path: Path, evidence_dir: Path | None) -> int:
    suite, suite_sha = load_and_validate_suite(suite_path)
    synthetic_case = {
        "case_id": "synthetic_declarative_case",
        "category": "batch_target",
        "question": "查询测试表编码、名称和说明，最多返回10条",
        "expected_tables": ["ad_dict"],
        "limit_max": 10,
        "expected_columns": ["list_type", "list_type_desc", "item_code"],
        "sql_constraints": {
            "require_join": False,
            "forbid_join": True,
            "require_left_join": False,
            "require_group_by": False,
            "require_order_desc": False,
            "forbidden_tables": [],
            "forbidden_columns": ["geom"],
            "forbid_select_star": True,
            "forbid_distinct": True,
            "forbid_where": True,
            "forbid_null_filter": True,
            "expected_chart_types": [],
            "require_chart_title": False,
            "require_chart_fields": False,
        },
        "result_policy": {"exact_rows": 1, "minimum_rows": 1, "columns_mode": "contains", "require_answer": True},
    }
    accepted_result = {
        "result": {
            "http_status": 200,
            "errors": [],
            "sql": "SELECT list_type, list_type_desc, item_code FROM ad_dict LIMIT 10",
            "csv_file": "synthetic.csv",
            "execution_success": True,
            "columns": ["list_type", "list_type_desc", "item_code"],
            "row_count": 1,
            "answer": "ok",
            "chart_specs": [],
        }
    }
    rejected_result = json.loads(json.dumps(accepted_result))
    rejected_result["result"]["sql"] = "SELECT * FROM ad_dict LIMIT 10"
    accepted = validate_case(synthetic_case, accepted_result)["accepted"]
    rejected = not validate_case(synthetic_case, rejected_result)["accepted"]
    zero_row_case = next(
        case
        for case in suite["cases"]
        if case["case_id"] == "level3_p0_annual_ph_ranking"
    )
    zero_row_result = {
        "result": {
            "http_status": 200,
            "errors": [],
            "sql": (
                "SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, "
                "water_quality_level FROM wm_waterquality_year_records "
                "WHERE monitor_year = 2025 AND m2_value IS NOT NULL "
                "GROUP BY station_id, monitor_year, water_quality_level "
                "ORDER BY AVG(m2_value) DESC LIMIT 20"
            ),
            "csv_file": "",
            "execution_success": True,
            "columns": [
                "station_id",
                "monitor_year",
                "avg_ph",
                "water_quality_level",
            ],
            "row_count": 0,
            "answer": "当前年度 pH 数据为空。",
            "chart_specs": [],
        }
    }
    zero_row_policy_test = validate_case(zero_row_case, zero_row_result)["accepted"]
    zero_row_missing_columns = json.loads(json.dumps(zero_row_result))
    zero_row_missing_columns["result"]["columns"] = []
    zero_row_missing_schema_rejected = not validate_case(
        zero_row_case, zero_row_missing_columns
    )["accepted"]
    zero_row_missing_execution = json.loads(json.dumps(zero_row_result))
    zero_row_missing_execution["result"].pop("execution_success")
    zero_row_missing_execution_success_rejected = not validate_case(
        zero_row_case, zero_row_missing_execution
    )["accepted"]

    from backend.schema_preserving_sql import (
        RunSqlToolArgs,
        SchemaPreservingPostgresRunner,
        SchemaPreservingRunSqlTool,
        pd,
    )
    from tools.regression_service_harness import parse_dataframe_event

    class FakeDescription:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeCursor:
        description = [
            FakeDescription("station_id"),
            FakeDescription("monitor_year"),
            FakeDescription("avg_ph"),
            FakeDescription("water_quality_level"),
        ]
        rowcount = 0

        def execute(self, _sql: str) -> None:
            return None

        def fetchall(self) -> list[Any]:
            return []

        def close(self) -> None:
            return None

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_instance = FakeCursor()

        def cursor(self, **_kwargs: Any) -> FakeCursor:
            return self.cursor_instance

        def commit(self) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeExtras:
        RealDictCursor = object

    class FakePsycopg:
        extras = FakeExtras()

        def __init__(self) -> None:
            self.connection = FakeConnection()

        def connect(self, *_args: Any, **_kwargs: Any) -> FakeConnection:
            return self.connection

    fake_psycopg = FakePsycopg()
    schema_runner = SchemaPreservingPostgresRunner.__new__(
        SchemaPreservingPostgresRunner
    )
    schema_runner.connection_string = "synthetic"
    schema_runner.connection_params = None
    schema_runner.psycopg2 = fake_psycopg
    empty_runner_frame = asyncio.run(
        schema_runner.run_sql(
            RunSqlToolArgs(sql=zero_row_result["result"]["sql"]), None
        )
    )
    empty_result_runner_columns_test = (
        empty_runner_frame.empty
        and empty_runner_frame.columns.tolist()
        == zero_row_case["expected_columns"]
    )

    class FakeEmptyRunner:
        async def run_sql(self, _args: Any, _context: Any) -> Any:
            return pd.DataFrame(columns=zero_row_case["expected_columns"])

    class FakeNonemptyRunner:
        async def run_sql(self, _args: Any, _context: Any) -> Any:
            return pd.DataFrame(
                [{"list_type": "A", "list_type_desc": "B", "item_code": "C"}]
            )

    class FakeFileSystem:
        def __init__(self) -> None:
            self.writes: list[str] = []

        async def write_file(
            self,
            filename: str,
            _content: str,
            _context: Any,
            overwrite: bool = False,
        ) -> None:
            if overwrite:
                self.writes.append(filename)

    empty_files = FakeFileSystem()
    empty_tool = SchemaPreservingRunSqlTool(
        sql_runner=FakeEmptyRunner(), file_system=empty_files
    )
    empty_tool_result = asyncio.run(
        empty_tool.execute(
            None, RunSqlToolArgs(sql=zero_row_result["result"]["sql"])
        )
    )
    empty_component = empty_tool_result.ui_component.rich_component
    empty_result_tool_component_test = (
        empty_tool_result.success
        and empty_component.rows == []
        and empty_component.columns == zero_row_case["expected_columns"]
        and empty_component.data.get("execution_success") is True
        and empty_files.writes == []
    )

    first_sql = zero_row_result["result"]["sql"]
    second_sql = first_sql.replace(
        ", water_quality_level", ""
    ).replace(
        ", water_quality_level GROUP BY", " GROUP BY"
    )

    def synthetic_dataframe_sse(
        sql: str, columns: list[str], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "rich": {
                "type": "dataframe",
                "data": {
                    "sql": sql,
                    "columns": columns,
                    "data": rows,
                    "row_count": len(rows),
                    "description": "synthetic",
                    "execution_success": True,
                },
            },
            "simple": {"type": "text", "text": "synthetic"},
        }

    parsed_events = [
        parse_dataframe_event(
            synthetic_dataframe_sse(
                first_sql, zero_row_case["expected_columns"], []
            ),
            1,
        ),
        parse_dataframe_event(
            synthetic_dataframe_sse(
                second_sql, zero_row_case["expected_columns"][:-1], []
            ),
            2,
        ),
    ]
    dataframe_events = [event for event in parsed_events if event is not None]
    multi_response = {
        "http_status": 200,
        "errors": [],
        "answer": "当前年度 pH 数据为空。",
        "dataframe_events": dataframe_events,
    }
    multi_execution = run_http_case(
        zero_row_case,
        Path("synthetic-agent-data"),
        post_sse=lambda _query: json.loads(json.dumps(multi_response)),
        query_result_files=lambda _path: set(),
        read_csv=lambda _path: ([], []),
        extract_chart_specs=lambda _answer: [],
    )
    first_dataframe_event_selected_test = (
        multi_execution["result"]["sql"] == first_sql
        and multi_execution["result"]["columns"]
        == zero_row_case["expected_columns"]
    )
    later_sql_does_not_overwrite_test = (
        multi_execution["result"]["sql"] != second_sql
    )
    dataframe_event_count = len(multi_execution["result"]["dataframe_events"])

    nonempty_rows = [
        {"list_type": "A", "list_type_desc": "B", "item_code": "C"}
    ]
    nonempty_event = parse_dataframe_event(
        synthetic_dataframe_sse(
            accepted_result["result"]["sql"],
            synthetic_case["expected_columns"],
            nonempty_rows,
        ),
        1,
    )
    nonempty_response = {
        "http_status": 200,
        "errors": [],
        "answer": "ok",
        "dataframe_events": [nonempty_event],
    }
    nonempty_execution = run_http_case(
        synthetic_case,
        Path("synthetic-agent-data"),
        post_sse=lambda _query: json.loads(json.dumps(nonempty_response)),
        query_result_files=lambda _path: set(),
        read_csv=lambda _path: ([], []),
        extract_chart_specs=lambda _answer: [],
    )
    nonempty_dataframe_compatibility_test = validate_case(
        synthetic_case, nonempty_execution
    )["accepted"]

    nonempty_files = FakeFileSystem()
    nonempty_tool = SchemaPreservingRunSqlTool(
        sql_runner=FakeNonemptyRunner(), file_system=nonempty_files
    )
    nonempty_tool_result = asyncio.run(
        nonempty_tool.execute(
            None, RunSqlToolArgs(sql=accepted_result["result"]["sql"])
        )
    )
    nonempty_dataframe_compatibility_test = (
        nonempty_dataframe_compatibility_test
        and nonempty_tool_result.success
        and len(nonempty_files.writes) == 1
    )
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    called_names = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    forbidden_imports = sorted(
        name
        for name in imported_modules
        if name in {"backend.memory", "step4_server", "chromadb", "vanna"}
        or name.startswith("chromadb.")
        or name.startswith("vanna.")
    )
    forbidden_calls = sorted(called_names & {"run_case", "context_diagnostics", "create_memory"})
    same_path_rejected = False
    try:
        validate_isolation_paths(FORMAL_RUNTIME, FORMAL_RUNTIME)
    except RuntimeError as error:
        same_path_rejected = str(error) == "FORMAL_DATA_DIR_FORBIDDEN"
    early_import_rejected = False
    try:
        assert_no_early_memory_modules({"backend.memory": object()})
    except RuntimeError as error:
        early_import_rejected = str(error).startswith("EARLY_MEMORY_MODULE_IMPORT")
    memory_schema_valid = (
        len(suite["memory_cases"]) == suite["memory_case_count"] == 6
    )
    invalid_memory_expectation_rejected = False
    with tempfile.TemporaryDirectory(prefix="f5-invalid-memory-suite-") as temp_name:
        invalid_suite = json.loads(json.dumps(suite))
        invalid_suite["memory_cases"][0]["expectation"] = "invalid"
        invalid_path = Path(temp_name) / "invalid-suite.json"
        invalid_path.write_text(
            json.dumps(invalid_suite, ensure_ascii=False), encoding="utf-8"
        )
        try:
            load_and_validate_suite(invalid_path)
        except ValueError:
            invalid_memory_expectation_rejected = True
    suite_valid = (
        len(suite["cases"]) == suite["case_count"] == 20
        and memory_schema_valid
        and suite_sha == EXPECTED_SUITE_SHA256
    )
    formal_baseline_constants_valid = (
        EXPECTED_FORMAL_RECORD_COUNT == 198
        and EXPECTED_FORMAL_SHA256
        == "d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992"
    )
    legacy_default_monitor_preserved = resolve_formal_monitor_baseline(None, None) == (
        EXPECTED_FORMAL_RECORD_COUNT,
        EXPECTED_FORMAL_SHA256,
    )
    one_dynamic_parameter_rejected = False
    try:
        resolve_formal_monitor_baseline(198, None)
    except ValueError:
        one_dynamic_parameter_rejected = True
    invalid_dynamic_sha_rejected = False
    try:
        resolve_formal_monitor_baseline(198, "A" * 64)
    except ValueError:
        invalid_dynamic_sha_rejected = True
    dynamic_sha = "a" * 64
    dynamic_baseline_passed = False
    dynamic_change_detected = False
    with tempfile.TemporaryDirectory(prefix="f5-runner-self-test-") as temp_name:
        checkpoint_evidence = Path(temp_name)
        checkpoints: list[dict[str, Any]] = []
        matching_state = {
            "path": "synthetic-formal-runtime",
            "record_count": 198,
            "sha256": dynamic_sha,
            "file_count": 2,
        }
        formal_checkpoint(
            "before_service_start",
            checkpoints,
            checkpoint_evidence,
            expected_record_count=198,
            expected_sha256=dynamic_sha,
            state_reader=lambda _path: dict(matching_state),
        )
        dynamic_baseline_passed = checkpoints[-1]["passed"] is True
        try:
            formal_checkpoint(
                "after_case_01",
                checkpoints,
                checkpoint_evidence,
                expected_record_count=198,
                expected_sha256=dynamic_sha,
                state_reader=lambda _path: {
                    **matching_state,
                    "sha256": "b" * 64,
                },
            )
        except FormalRuntimeChanged:
            dynamic_change_detected = checkpoints[-1]["passed"] is False

    from backend.prompts import OptimizedSystemPromptBuilder
    from backend.query_context import (
        OriginalQuestionLifecycleHook,
        get_original_question,
    )
    from backend.request_diagnostics import (
        clear_request_diagnostics,
        get_request_diagnostics,
        initialize_request_diagnostics,
        write_trace_json,
    )
    from backend.run_sql_requirement import (
        FORCED_RUN_SQL_TOOL_CHOICE,
        REQUIREMENT_REASON,
        RunSqlRequirementError,
        clear_run_sql_requirement,
        get_run_sql_requirement,
        initialize_run_sql_requirement,
        record_injected_sql_examples,
        record_successful_run_sql_result,
    )
    from backend.sql_example_context_enhancer import (
        SqlExampleContextEnhancer,
        SqlExampleContextStats,
    )
    from backend.tracing_llm_service import TracingOpenAILlmService
    import backend.tracing_llm_service as tracing_models
    import tools.regression_service_harness as service_harness

    original_trace_enabled = os.environ.get("VANNA_REQUEST_TRACE_ENABLED")
    original_trace_dir = os.environ.get("VANNA_REQUEST_TRACE_DIR")
    with tempfile.TemporaryDirectory(prefix="request-trace-self-test-") as trace_name:
        trace_root = Path(trace_name).resolve()
        os.environ["VANNA_REQUEST_TRACE_ENABLED"] = "1"
        os.environ["VANNA_REQUEST_TRACE_DIR"] = str(trace_root)

        async def trace_worker(question: str) -> tuple[str, bool]:
            hook = OriginalQuestionLifecycleHook()
            await hook.before_message(None, question)
            context = get_request_diagnostics()
            assert context is not None
            trace_id = context.trace_id
            await asyncio.sleep(0)
            write_trace_json("worker.json", {"question": question})
            isolated = (
                get_request_diagnostics() is not None
                and get_request_diagnostics().trace_id == trace_id
            )
            await hook.after_message(None)
            return trace_id, isolated and get_request_diagnostics() is None

        async def run_trace_workers() -> list[tuple[str, bool]]:
            return list(
                await asyncio.gather(
                    trace_worker("并发问题一"), trace_worker("并发问题二")
                )
            )

        trace_workers = asyncio.run(run_trace_workers())
        trace_ids = [item[0] for item in trace_workers]
        request_trace_context_test = (
            len(set(trace_ids)) == 2
            and all((trace_root / trace_id / "worker.json").is_file() for trace_id in trace_ids)
        )
        trace_context_cleanup_test = all(item[1] for item in trace_workers)

        existing_directories = set(trace_root.iterdir())
        os.environ["VANNA_REQUEST_TRACE_ENABLED"] = "0"

        async def run_disabled_trace() -> tuple[bool, bool]:
            disabled_hook = OriginalQuestionLifecycleHook()
            await disabled_hook.before_message(None, "诊断关闭")
            disabled_context = get_request_diagnostics()
            disabled_without_directory = (
                disabled_context is not None
                and disabled_context.trace_directory is None
            )
            await disabled_hook.after_message(None)
            return disabled_without_directory, get_request_diagnostics() is None

        disabled_without_directory, disabled_cleaned = asyncio.run(
            run_disabled_trace()
        )
        trace_disabled_no_write_test = (
            disabled_without_directory
            and disabled_cleaned
            and set(trace_root.iterdir()) == existing_directories
        )

        os.environ["VANNA_REQUEST_TRACE_ENABLED"] = "1"
        capture_context = initialize_request_diagnostics("查询诊断样例")
        assert capture_context is not None and capture_context.trace_directory is not None
        capture_dir = capture_context.trace_directory
        initialize_run_sql_requirement()

        class FakeBaseEnhancer:
            async def enhance_system_prompt(self, prompt: str, _message: str, _user: Any) -> str:
                return prompt + "\nBASE_CONTEXT"

            async def enhance_user_messages(self, messages: list[Any], _user: Any) -> list[Any]:
                return messages

        class FakeSqlExampleEnhancer(SqlExampleContextEnhancer):
            async def _retrieve_examples(self, _message: str) -> list[dict[str, Any]]:
                self.last_stats = SqlExampleContextStats(
                    search_similar_usage_called=True,
                    tool_name_filter="run_sql",
                    top_k=2,
                    returned_count=3,
                    injected_count=2,
                    filtered=[{"sample_id": "DROP", "reason": "synthetic"}],
                )
                return [
                    {
                        "sample_id": "SAMPLE_001",
                        "question": "问题一",
                        "sql": "SELECT id FROM table_one LIMIT 1",
                        "tables": ["table_one"],
                    },
                    {
                        "sample_id": "SAMPLE_002",
                        "question": "问题二",
                        "sql": "SELECT id FROM table_two LIMIT 1",
                        "tables": ["table_two"],
                    },
                ]

        sql_enhancer = FakeSqlExampleEnhancer(base_enhancer=FakeBaseEnhancer(), top_k=2)
        final_prompt = asyncio.run(
            sql_enhancer.enhance_system_prompt("BASE_PROMPT", "查询诊断样例", None)
        )
        context_capture = json.loads(
            (capture_dir / "context-enhancer.json").read_text(encoding="utf-8")
        )
        final_system_prompt_capture_test = (
            (capture_dir / "final-system-prompt.txt").read_text(encoding="utf-8")
            == final_prompt
            and final_prompt.endswith("SELECT id FROM table_two LIMIT 1")
        )
        injected_sql_examples_capture_test = [
            item["sample_id"] for item in context_capture["injected_examples"]
        ] == ["SAMPLE_001", "SAMPLE_002"]
        requirement_state = get_run_sql_requirement()
        sql_example_injected_requires_run_sql_test = (
            requirement_state is not None
            and requirement_state.requires_run_sql
            and requirement_state.sql_example_injected_count == 2
            and REQUIREMENT_REASON in requirement_state.requirement_reasons
        )

        class FakeToolSchema:
            name = "run_sql"
            description = "Execute a synthetic SELECT"
            parameters = {
                "title": "RunSqlToolArgs",
                "type": "object",
                "properties": {"sql": {"type": "string"}},
            }

        asyncio.run(
            OptimizedSystemPromptBuilder().build_system_prompt(
                object(), [FakeToolSchema()]
            )
        )
        captured_tools = json.loads(
            (capture_dir / "tool-definitions.json").read_text(encoding="utf-8")
        )
        tool_schema_capture_test = (
            captured_tools[0]["name"] == "run_sql"
            and captured_tools[0]["parameter_model_name"] == "RunSqlToolArgs"
            and captured_tools[0]["parameters"]["properties"]["sql"]["type"]
            == "string"
        )

        class FakeClient:
            base_url = "https://example.invalid/v1"

        class FakeTracingService(TracingOpenAILlmService):
            async def _send_parent_request(self, request: Any) -> Any:
                self.parent_call_count += 1
                self.parent_payloads.append(self._build_payload(request))
                return tracing_models.LlmResponse(
                    content="synthetic response", finish_reason="stop"
                )

        fake_service = FakeTracingService.__new__(FakeTracingService)
        fake_service.model = "synthetic-model"
        fake_service._client = FakeClient()
        fake_service.parent_call_count = 0
        fake_service.parent_payloads = []
        user_class = tracing_models.LlmRequest.model_fields["user"].annotation
        fake_request = tracing_models.LlmRequest(
            messages=[
                tracing_models.LlmMessage(
                    role="user", content="Authorization: Bearer [REDACTED_SYNTHETIC_TOKEN]"
                )
            ],
            tools=[FakeToolSchema()],
            user=user_class(id="self-test", username="self-test"),
            system_prompt="Synthetic system prompt",
            metadata={"api_key": "[REDACTED_SYNTHETIC_TOKEN]"},
        )
        original_request_dump = fake_request.model_dump(mode="python")
        asyncio.run(fake_service.send_request(fake_request))
        asyncio.run(fake_service.send_request(fake_request))
        first_llm_call_forces_run_sql_test = (
            fake_service.parent_payloads[0].get("tool_choice")
            == FORCED_RUN_SQL_TOOL_CHOICE
        )
        second_llm_call_returns_to_auto_test = (
            fake_service.parent_payloads[1].get("tool_choice") == "auto"
        )
        non_streaming_force_test = first_llm_call_forces_run_sql_test
        request_object_not_mutated_test = (
            fake_request.model_dump(mode="python") == original_request_dump
        )
        multi_llm_call_capture_test = all(
            (capture_dir / name).is_file()
            for name in (
                "llm-call-001-request.json",
                "llm-call-001-response.json",
                "llm-call-002-request.json",
                "llm-call-002-response.json",
            )
        )
        trace_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in capture_dir.iterdir()
            if path.is_file()
        )
        secret_redaction_test = "synthetic-secret" not in trace_text
        requirement_trace = json.loads(
            (capture_dir / "run-sql-requirement.json").read_text(encoding="utf-8")
        )
        first_request_trace = json.loads(
            (capture_dir / "llm-call-001-request.json").read_text(encoding="utf-8")
        )
        second_request_trace = json.loads(
            (capture_dir / "llm-call-002-request.json").read_text(encoding="utf-8")
        )
        trace_records_effective_tool_choice_test = (
            requirement_trace["calls"][0]["original_tool_choice"] == "auto"
            and requirement_trace["calls"][0]["effective_tool_choice"]
            == FORCED_RUN_SQL_TOOL_CHOICE
            and requirement_trace["calls"][1]["effective_tool_choice"] == "auto"
            and first_request_trace["original_tool_choice"] == "auto"
            and first_request_trace["effective_tool_choice"]
            == FORCED_RUN_SQL_TOOL_CHOICE
            and second_request_trace["effective_tool_choice"] == "auto"
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        initialize_request_diagnostics("no SQL example")
        initialize_run_sql_requirement()
        record_injected_sql_examples(0)
        no_example_service = FakeTracingService.__new__(FakeTracingService)
        no_example_service.model = "synthetic-model"
        no_example_service._client = FakeClient()
        no_example_service.parent_call_count = 0
        no_example_service.parent_payloads = []
        asyncio.run(no_example_service.send_request(fake_request))
        no_example_state = get_run_sql_requirement()
        no_sql_example_no_force_test = (
            no_example_service.parent_payloads[0].get("tool_choice") == "auto"
            and no_example_state is not None
            and not no_example_state.requires_run_sql
            and not no_example_state.forced_tool_choice_applied
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        class AllFilteredEnhancer(SqlExampleContextEnhancer):
            async def _retrieve_examples(self, _message: str) -> list[dict[str, Any]]:
                self.last_stats = SqlExampleContextStats(
                    search_similar_usage_called=True,
                    tool_name_filter="run_sql",
                    top_k=1,
                    returned_count=1,
                    injected_count=0,
                    filtered=[{"sample_id": "FILTERED", "reason": "synthetic"}],
                )
                return []

        initialize_request_diagnostics("filtered SQL example")
        initialize_run_sql_requirement()
        filtered_enhancer = AllFilteredEnhancer(
            base_enhancer=FakeBaseEnhancer(), top_k=1
        )
        asyncio.run(
            filtered_enhancer.enhance_system_prompt(
                "BASE_PROMPT", "filtered SQL example", None
            )
        )
        filtered_state = get_run_sql_requirement()
        filtered_examples_do_not_force_test = (
            filtered_state is not None
            and not filtered_state.requires_run_sql
            and filtered_state.sql_example_injected_count == 0
            and filtered_state.requirement_reasons == []
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        class FakeStreamingService(TracingOpenAILlmService):
            async def _stream_parent_request(self, request: Any) -> Any:
                self.parent_payloads.append(self._build_payload(request))
                yield tracing_models.LlmStreamChunk(
                    content="synthetic stream", finish_reason="stop"
                )

        initialize_request_diagnostics("streaming force")
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        streaming_service = FakeStreamingService.__new__(FakeStreamingService)
        streaming_service.model = "synthetic-model"
        streaming_service._client = FakeClient()
        streaming_service.parent_payloads = []

        async def consume_stream() -> list[Any]:
            return [chunk async for chunk in streaming_service.stream_request(fake_request)]

        asyncio.run(consume_stream())
        streaming_force_test = (
            streaming_service.parent_payloads[0].get("tool_choice")
            == FORCED_RUN_SQL_TOOL_CHOICE
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        class MissingToolSchema:
            name = "other_tool"
            description = "Synthetic non-SQL tool"
            parameters = {"type": "object", "properties": {}}

        missing_tool_request = fake_request.model_copy(
            deep=True, update={"tools": [MissingToolSchema()]}
        )
        missing_context = initialize_request_diagnostics("missing run_sql")
        assert missing_context is not None and missing_context.trace_directory is not None
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        missing_service = FakeTracingService.__new__(FakeTracingService)
        missing_service.model = "synthetic-model"
        missing_service._client = FakeClient()
        missing_service.parent_call_count = 0
        missing_service.parent_payloads = []
        missing_error = None
        try:
            asyncio.run(missing_service.send_request(missing_tool_request))
        except RunSqlRequirementError as error:
            missing_error = error
        missing_trace = json.loads(
            (missing_context.trace_directory / "run-sql-requirement.json").read_text(
                encoding="utf-8"
            )
        )
        missing_run_sql_schema_fails_closed_test = (
            missing_error is not None
            and missing_service.parent_call_count == 0
            and missing_trace["error_type"] == "RunSqlRequirementError"
            and not missing_trace["run_sql_schema_present"]
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        async def requirement_worker(
            question: str, injected_count: int
        ) -> tuple[Any, bool]:
            hook = OriginalQuestionLifecycleHook()
            await hook.before_message(None, question)
            record_injected_sql_examples(injected_count)
            await asyncio.sleep(0)
            worker_service = FakeTracingService.__new__(FakeTracingService)
            worker_service.model = "synthetic-model"
            worker_service._client = FakeClient()
            worker_service.parent_call_count = 0
            worker_service.parent_payloads = []
            await worker_service.send_request(fake_request)
            tool_choice = worker_service.parent_payloads[0].get("tool_choice")
            await hook.after_message(None)
            cleaned = (
                get_run_sql_requirement() is None
                and get_request_diagnostics() is None
            )
            return tool_choice, cleaned

        async def run_requirement_workers() -> list[tuple[Any, bool]]:
            return list(
                await asyncio.gather(
                    requirement_worker("requires run_sql", 1),
                    requirement_worker("does not require run_sql", 0),
                )
            )

        requirement_workers = asyncio.run(run_requirement_workers())
        concurrent_requirement_isolation_test = (
            requirement_workers[0][0] == FORCED_RUN_SQL_TOOL_CHOICE
            and requirement_workers[1][0] == "auto"
            and all(item[1] for item in requirement_workers)
        )

        async def lifecycle_requirement_check() -> tuple[bool, bool]:
            hook = OriginalQuestionLifecycleHook()
            await hook.before_message(None, "lifecycle requirement")
            record_injected_sql_examples(1)
            present_before_cleanup = get_run_sql_requirement() is not None
            await hook.after_message(None)
            cleaned_after = (
                get_run_sql_requirement() is None
                and get_request_diagnostics() is None
            )
            return present_before_cleanup, cleaned_after

        lifecycle_present, lifecycle_cleaned = asyncio.run(
            lifecycle_requirement_check()
        )
        lifecycle_requirement_cleanup_test = lifecycle_present and lifecycle_cleaned

        class DeepSeekClient:
            base_url = "https://api.deepseek.com/v1"

        class DeepSeekPolicyService(TracingOpenAILlmService):
            def _build_original_payload(self, request: Any) -> dict[str, Any]:
                payload = super()._build_original_payload(request)
                payload["extra_body"] = {
                    "vendor_flag": "preserved",
                    "thinking": {"type": "enabled"},
                }
                payload["reasoning_effort"] = "high"
                return payload

            async def _send_parent_request(self, request: Any) -> Any:
                self.parent_call_count += 1
                self.parent_payloads.append(self._build_payload(request))
                return tracing_models.LlmResponse(
                    content="deepseek synthetic response", finish_reason="stop"
                )

        deep_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
        deep_service.model = "deepseek-v4-pro"
        deep_service._client = DeepSeekClient()
        deep_service.parent_call_count = 0
        deep_service.parent_payloads = []
        deep_request_before = fake_request.model_dump(mode="python")

        async def run_deepseek_turn_and_next_request() -> tuple[bool, Any, Any]:
            hook = OriginalQuestionLifecycleHook()
            await hook.before_message(None, "deepseek policy")
            record_injected_sql_examples(1)
            await deep_service.send_request(fake_request)
            await deep_service.send_request(fake_request)
            await deep_service.send_request(fake_request)
            active_state = get_run_sql_requirement()
            active_before_cleanup = bool(
                active_state
                and active_state.deepseek_non_thinking_tool_turn_active
            )
            turn_decisions = list(active_state.decisions)
            await hook.after_message(None)
            success_cleaned = (
                get_run_sql_requirement() is None
                and get_request_diagnostics() is None
                and get_original_question() is None
            )

            await hook.before_message(None, "next deepseek request")
            next_state_before_call = get_run_sql_requirement()
            await deep_service.send_request(fake_request)
            next_decision = get_run_sql_requirement().decisions[-1]
            await hook.after_message(None)
            return (
                active_before_cleanup and success_cleaned,
                (next_state_before_call, next_decision),
                turn_decisions,
            )

        (
            success_cleanup_resets_non_thinking_turn_test,
            (next_state_before_call, next_decision),
            turn_decisions,
        ) = asyncio.run(run_deepseek_turn_and_next_request())
        (
            deep_first_payload,
            deep_second_payload,
            deep_third_payload,
            deep_next_request_payload,
        ) = deep_service.parent_payloads
        deepseek_first_call_named_run_sql_non_thinking_test = (
            deep_first_payload.get("tool_choice") == FORCED_RUN_SQL_TOOL_CHOICE
            and deep_first_payload.get("extra_body", {}).get("thinking")
            == {"type": "disabled"}
            and "reasoning_effort" not in deep_first_payload
        )
        deepseek_second_call_auto_non_thinking_test = (
            deep_second_payload.get("tool_choice") == "auto"
            and deep_second_payload.get("extra_body", {}).get("thinking")
            == {"type": "disabled"}
            and turn_decisions[1]["provider_strategy"]
            == "deepseek_non_thinking_tool_continuation"
            and turn_decisions[1]["non_thinking_continuation_applied"]
            and not turn_decisions[1]["forced_tool_choice_applied"]
            and turn_decisions[1]["forced_tool_name"] == ""
        )
        deepseek_third_call_auto_non_thinking_test = (
            deep_third_payload.get("tool_choice") == "auto"
            and deep_third_payload.get("extra_body", {}).get("thinking")
            == {"type": "disabled"}
            and turn_decisions[2]["provider_strategy"]
            == "deepseek_non_thinking_tool_continuation"
            and turn_decisions[2]["non_thinking_turn_started_on_call"] == 1
            and not turn_decisions[2]["synthetic_reasoning_content_injected"]
        )
        deepseek_continuation_reasoning_effort_removed_test = (
            "reasoning_effort" not in deep_second_payload
            and "reasoning_effort" not in deep_third_payload
        )
        deepseek_continuation_extra_body_preserved_test = all(
            payload.get("extra_body", {}).get("vendor_flag") == "preserved"
            for payload in (
                deep_first_payload,
                deep_second_payload,
                deep_third_payload,
            )
        )
        deepseek_next_user_request_thinking_restored_test = (
            deep_next_request_payload.get("extra_body", {}).get("thinking")
            == {"type": "enabled"}
            and next_state_before_call is not None
            and not next_state_before_call.deepseek_non_thinking_tool_turn_active
            and next_decision["provider_strategy"] == "original_request"
        )
        deepseek_next_user_request_auto_restored_test = (
            deep_next_request_payload.get("tool_choice") == "auto"
        )

        def contains_reasoning_content(value: Any) -> bool:
            if isinstance(value, dict):
                return "reasoning_content" in value or any(
                    contains_reasoning_content(item) for item in value.values()
                )
            if isinstance(value, list):
                return any(contains_reasoning_content(item) for item in value)
            return False

        no_synthetic_reasoning_content_test = not any(
            contains_reasoning_content(payload)
            for payload in deep_service.parent_payloads
        )
        deepseek_thinking_first_call_disabled_test = (
            deep_first_payload.get("extra_body", {}).get("thinking")
            == {"type": "disabled"}
        )
        deepseek_first_call_named_run_sql_test = (
            deep_first_payload.get("tool_choice") == FORCED_RUN_SQL_TOOL_CHOICE
        )
        deepseek_first_call_reasoning_effort_removed_test = (
            "reasoning_effort" not in deep_first_payload
        )
        deepseek_extra_body_fields_preserved_test = (
            deep_first_payload.get("extra_body", {}).get("vendor_flag")
            == "preserved"
            and deep_second_payload.get("extra_body", {}).get("vendor_flag")
            == "preserved"
        )
        request_object_not_mutated_test = (
            request_object_not_mutated_test
            and fake_request.model_dump(mode="python") == deep_request_before
        )
        non_streaming_policy_test = (
            deep_service.parent_call_count == 4
            and deepseek_thinking_first_call_disabled_test
            and deepseek_first_call_named_run_sql_test
            and deepseek_second_call_auto_non_thinking_test
            and deepseek_third_call_auto_non_thinking_test
        )

        non_deep_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
        non_deep_service.model = "synthetic-model"
        non_deep_service._client = FakeClient()
        non_deep_service.parent_call_count = 0
        non_deep_service.parent_payloads = []
        initialize_request_diagnostics("non-deepseek policy")
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        asyncio.run(non_deep_service.send_request(fake_request))
        non_deep_payload = non_deep_service.parent_payloads[0]
        non_deepseek_policy_unchanged_test = (
            non_deep_payload.get("tool_choice") == FORCED_RUN_SQL_TOOL_CHOICE
            and non_deep_payload.get("extra_body", {}).get("thinking")
            == {"type": "enabled"}
            and non_deep_payload.get("reasoning_effort") == "high"
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        class InvalidExtraBodyService(DeepSeekPolicyService):
            def _build_original_payload(self, request: Any) -> dict[str, Any]:
                payload = super(DeepSeekPolicyService, self)._build_original_payload(
                    request
                )
                payload["extra_body"] = "invalid"
                return payload

        invalid_service = InvalidExtraBodyService.__new__(InvalidExtraBodyService)
        invalid_service.model = "deepseek-v4-pro"
        invalid_service._client = DeepSeekClient()
        invalid_service.parent_call_count = 0
        invalid_service.parent_payloads = []
        initialize_request_diagnostics("invalid thinking override")
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        invalid_error = None
        try:
            asyncio.run(invalid_service.send_request(fake_request))
        except RunSqlRequirementError as error:
            invalid_error = error
        thinking_override_failure_fails_closed_test = (
            invalid_error is not None and invalid_service.parent_call_count == 0
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        class InvalidContinuationExtraBodyService(DeepSeekPolicyService):
            def _build_original_payload(self, request: Any) -> dict[str, Any]:
                payload = super()._build_original_payload(request)
                state = get_run_sql_requirement()
                if state and state.deepseek_non_thinking_tool_turn_active:
                    payload["extra_body"] = "invalid"
                return payload

        invalid_continuation_service = InvalidContinuationExtraBodyService.__new__(
            InvalidContinuationExtraBodyService
        )
        invalid_continuation_service.model = "deepseek-v4-pro"
        invalid_continuation_service._client = DeepSeekClient()
        invalid_continuation_service.parent_call_count = 0
        invalid_continuation_service.parent_payloads = []
        initialize_request_diagnostics("invalid continuation thinking override")
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        asyncio.run(invalid_continuation_service.send_request(fake_request))
        invalid_continuation_error = None
        try:
            asyncio.run(invalid_continuation_service.send_request(fake_request))
        except RunSqlRequirementError as error:
            invalid_continuation_error = error
        thinking_override_failure_fails_closed_test = (
            thinking_override_failure_fails_closed_test
            and invalid_continuation_error is not None
            and invalid_continuation_service.parent_call_count == 1
        )
        clear_run_sql_requirement()
        clear_request_diagnostics()

        class DeepSeekStreamingPolicyService(DeepSeekPolicyService):
            async def _stream_parent_request(self, request: Any) -> Any:
                self.parent_call_count += 1
                self.parent_payloads.append(self._build_payload(request))
                yield tracing_models.LlmStreamChunk(
                    content="deepseek stream", finish_reason="stop"
                )

        stream_policy_service = DeepSeekStreamingPolicyService.__new__(
            DeepSeekStreamingPolicyService
        )
        stream_policy_service.model = "deepseek-v4-pro"
        stream_policy_service._client = DeepSeekClient()
        stream_policy_service.parent_call_count = 0
        stream_policy_service.parent_payloads = []
        initialize_request_diagnostics("deepseek streaming policy")
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)

        async def consume_policy_stream() -> list[Any]:
            chunks = [
                chunk
                async for chunk in stream_policy_service.stream_request(fake_request)
            ]
            chunks.extend(
                [
                    chunk
                    async for chunk in stream_policy_service.stream_request(
                        fake_request
                    )
                ]
            )
            return chunks

        asyncio.run(consume_policy_stream())
        streaming_policy_test = (
            stream_policy_service.parent_call_count == 2
            and stream_policy_service.parent_payloads[0].get("tool_choice")
            == FORCED_RUN_SQL_TOOL_CHOICE
            and stream_policy_service.parent_payloads[0]
            .get("extra_body", {})
            .get("thinking")
            == {"type": "disabled"}
            and "reasoning_effort" not in stream_policy_service.parent_payloads[0]
            and stream_policy_service.parent_payloads[1].get("tool_choice") == "auto"
            and stream_policy_service.parent_payloads[1]
            .get("extra_body", {})
            .get("thinking")
            == {"type": "disabled"}
            and "reasoning_effort" not in stream_policy_service.parent_payloads[1]
        )
        streaming_continuation_policy_test = streaming_policy_test
        non_streaming_continuation_policy_test = non_streaming_policy_test
        clear_run_sql_requirement()
        clear_request_diagnostics()

        async def non_thinking_isolation_worker(
            question: str, injected_count: int, call_count: int
        ) -> tuple[list[dict[str, Any]], bool]:
            hook = OriginalQuestionLifecycleHook()
            await hook.before_message(None, question)
            record_injected_sql_examples(injected_count)
            worker_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
            worker_service.model = "deepseek-v4-pro"
            worker_service._client = DeepSeekClient()
            worker_service.parent_call_count = 0
            worker_service.parent_payloads = []
            for _ in range(call_count):
                await worker_service.send_request(fake_request)
                await asyncio.sleep(0)
            await hook.after_message(None)
            cleaned = (
                get_run_sql_requirement() is None
                and get_request_diagnostics() is None
                and get_original_question() is None
            )
            return worker_service.parent_payloads, cleaned

        async def run_non_thinking_isolation_workers():
            return await asyncio.gather(
                non_thinking_isolation_worker("non-thinking A", 1, 2),
                non_thinking_isolation_worker("original B", 0, 1),
            )

        isolation_a, isolation_b = asyncio.run(
            run_non_thinking_isolation_workers()
        )
        concurrent_non_thinking_turn_isolation_test = (
            isolation_a[0][1].get("tool_choice") == "auto"
            and isolation_a[0][1].get("extra_body", {}).get("thinking")
            == {"type": "disabled"}
            and isolation_b[0][0].get("tool_choice") == "auto"
            and isolation_b[0][0].get("extra_body", {}).get("thinking")
            == {"type": "enabled"}
            and isolation_a[1]
            and isolation_b[1]
        )

        successful_columns = [
            "station_id",
            "monitor_year",
            "avg_ph",
            "water_quality_level",
        ]

        class SyntheticToolResult:
            def __init__(
                self, *, success: bool, result_for_llm: str, metadata: dict[str, Any]
            ) -> None:
                self.success = success
                self.result_for_llm = result_for_llm
                self.metadata = metadata

        def synthetic_sql_result(
            *, success: bool, row_count: int = 0, query_type: str = "SELECT"
        ) -> SyntheticToolResult:
            return SyntheticToolResult(
                success=success,
                result_for_llm="synthetic SQL result",
                metadata={
                    "query_type": query_type,
                    "row_count": row_count,
                    "columns": successful_columns,
                },
            )

        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        zero_row_recorded = record_successful_run_sql_result(
            synthetic_sql_result(success=True)
        )
        zero_row_state = get_run_sql_requirement()
        approved_example_zero_row_success_closes_tool_phase_test = bool(
            zero_row_recorded
            and zero_row_state
            and zero_row_state.successful_run_sql_completed
            and zero_row_state.successful_run_sql_count == 1
            and zero_row_state.successful_run_sql_row_count == 0
            and zero_row_state.successful_run_sql_columns == successful_columns
            and zero_row_state.tool_phase_closed
            and zero_row_state.tool_phase_close_reason
            == "first_successful_run_sql_for_approved_example"
        )
        clear_run_sql_requirement()

        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        nonempty_recorded = record_successful_run_sql_result(
            synthetic_sql_result(success=True, row_count=3)
        )
        nonempty_state = get_run_sql_requirement()
        approved_example_nonempty_success_closes_tool_phase_test = bool(
            nonempty_recorded
            and nonempty_state
            and nonempty_state.tool_phase_closed
            and nonempty_state.successful_run_sql_row_count == 3
        )
        first_success_evidence = (
            nonempty_state.successful_run_sql_row_count,
            list(nonempty_state.successful_run_sql_columns),
            nonempty_state.tool_phase_close_reason,
        )
        record_successful_run_sql_result(
            SyntheticToolResult(
                success=True,
                result_for_llm="repeat",
                metadata={
                    "query_type": "SELECT",
                    "row_count": 9,
                    "columns": ["changed"],
                },
            )
        )
        approved_example_nonempty_success_closes_tool_phase_test = (
            approved_example_nonempty_success_closes_tool_phase_test
            and first_success_evidence
            == (
                nonempty_state.successful_run_sql_row_count,
                nonempty_state.successful_run_sql_columns,
                nonempty_state.tool_phase_close_reason,
            )
        )
        clear_run_sql_requirement()

        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        failed_recorded = record_successful_run_sql_result(
            synthetic_sql_result(success=False)
        )
        failed_state = get_run_sql_requirement()
        failed_sql_does_not_close_tool_phase_test = bool(
            not failed_recorded and failed_state and not failed_state.tool_phase_closed
        )
        guard_blocked_recorded = record_successful_run_sql_result(
            SyntheticToolResult(
                success=False,
                result_for_llm="blocked",
                metadata={
                    "query_type": "SELECT",
                    "blocked_by_sql_guard": True,
                    "row_count": 0,
                    "columns": successful_columns,
                },
            )
        )
        sql_guard_block_does_not_close_tool_phase_test = bool(
            not guard_blocked_recorded
            and failed_state
            and not failed_state.tool_phase_closed
            and failed_state.successful_run_sql_count == 0
        )
        clear_run_sql_requirement()

        initialize_run_sql_requirement()
        request_without_approved_example_unchanged_test = (
            not record_successful_run_sql_result(synthetic_sql_result(success=True))
            and not get_run_sql_requirement().tool_phase_closed
        )
        clear_run_sql_requirement()

        answer_only_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
        answer_only_service.model = "deepseek-v4-pro"
        answer_only_service._client = DeepSeekClient()
        answer_only_service.parent_call_count = 0
        answer_only_service.parent_payloads = []
        answer_only_request_before = fake_request.model_dump(mode="python")
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        first_answer_response = asyncio.run(answer_only_service.send_request(fake_request))
        record_successful_run_sql_result(synthetic_sql_result(success=True))
        second_answer_response = asyncio.run(answer_only_service.send_request(fake_request))
        answer_only_state = get_run_sql_requirement()
        first_answer_payload, second_answer_payload = answer_only_service.parent_payloads
        second_call_tools_removed_test = "tools" not in second_answer_payload
        second_call_tool_choice_removed_test = "tool_choice" not in second_answer_payload
        second_call_thinking_disabled_test = (
            second_answer_payload.get("extra_body", {}).get("thinking")
            == {"type": "disabled"}
        )
        second_call_reasoning_effort_removed_test = (
            "reasoning_effort" not in second_answer_payload
        )
        second_call_final_text_only_test = (
            second_answer_response.content == "deepseek synthetic response"
            and not second_answer_response.tool_calls
            and second_answer_response.finish_reason == "stop"
        )
        non_streaming_answer_only_policy_test = all(
            (
                first_answer_payload.get("tool_choice")
                == FORCED_RUN_SQL_TOOL_CHOICE,
                second_call_tools_removed_test,
                second_call_tool_choice_removed_test,
                second_call_thinking_disabled_test,
                second_call_reasoning_effort_removed_test,
                answer_only_service.parent_call_count == 2,
                bool(answer_only_state and answer_only_state.tool_phase_closed),
            )
        )
        request_object_not_mutated_test = (
            request_object_not_mutated_test
            and fake_request.model_dump(mode="python") == answer_only_request_before
        )
        clear_run_sql_requirement()

        third_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
        third_service.model = "deepseek-v4-pro"
        third_service._client = DeepSeekClient()
        third_service.parent_call_count = 0
        third_service.parent_payloads = []
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        asyncio.run(third_service.send_request(fake_request))
        record_successful_run_sql_result(synthetic_sql_result(success=True))
        asyncio.run(third_service.send_request(fake_request))
        asyncio.run(third_service.send_request(fake_request))
        third_call_remains_answer_only_test = all(
            "tools" not in payload and "tool_choice" not in payload
            for payload in third_service.parent_payloads[1:]
        )
        clear_run_sql_requirement()

        initialize_run_sql_requirement()
        next_request_state = get_run_sql_requirement()
        next_request_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
        next_request_service.model = "deepseek-v4-pro"
        next_request_service._client = DeepSeekClient()
        next_request_service.parent_call_count = 0
        next_request_service.parent_payloads = []
        asyncio.run(next_request_service.send_request(fake_request))
        next_payload = next_request_service.parent_payloads[0]
        next_user_request_tools_restored_test = "tools" in next_payload
        next_user_request_thinking_restored_test = (
            next_payload.get("extra_body", {}).get("thinking")
            == {"type": "enabled"}
            and not next_request_state.tool_phase_closed
        )
        clear_run_sql_requirement()

        stream_answer_service = DeepSeekStreamingPolicyService.__new__(
            DeepSeekStreamingPolicyService
        )
        stream_answer_service.model = "deepseek-v4-pro"
        stream_answer_service._client = DeepSeekClient()
        stream_answer_service.parent_call_count = 0
        stream_answer_service.parent_payloads = []
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)

        async def consume_answer_only_stream() -> None:
            async for _ in stream_answer_service.stream_request(fake_request):
                pass
            record_successful_run_sql_result(synthetic_sql_result(success=True))
            async for _ in stream_answer_service.stream_request(fake_request):
                pass

        asyncio.run(consume_answer_only_stream())
        streaming_answer_only_policy_test = (
            stream_answer_service.parent_call_count == 2
            and "tools" not in stream_answer_service.parent_payloads[1]
            and "tool_choice" not in stream_answer_service.parent_payloads[1]
        )
        clear_run_sql_requirement()

        async def tool_phase_isolation_worker(
            approved: bool,
        ) -> tuple[list[dict[str, Any]], bool]:
            initialize_run_sql_requirement()
            record_injected_sql_examples(1 if approved else 0)
            service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
            service.model = "deepseek-v4-pro"
            service._client = DeepSeekClient()
            service.parent_call_count = 0
            service.parent_payloads = []
            await service.send_request(fake_request)
            if approved:
                record_successful_run_sql_result(synthetic_sql_result(success=True))
                await service.send_request(fake_request)
            await asyncio.sleep(0)
            closed = bool(get_run_sql_requirement().tool_phase_closed)
            clear_run_sql_requirement()
            return service.parent_payloads, closed

        async def run_tool_phase_isolation_workers():
            return await asyncio.gather(
                tool_phase_isolation_worker(True),
                tool_phase_isolation_worker(False),
            )

        tool_phase_a, tool_phase_b = asyncio.run(run_tool_phase_isolation_workers())
        concurrent_tool_phase_isolation_test = (
            "tools" not in tool_phase_a[0][1]
            and tool_phase_a[1]
            and "tools" in tool_phase_b[0][0]
            and not tool_phase_b[1]
        )
        success_cleanup_resets_tool_phase_test = (
            get_run_sql_requirement() is None
        )
        initialize_run_sql_requirement()
        record_injected_sql_examples(1)
        record_successful_run_sql_result(synthetic_sql_result(success=True))
        clear_run_sql_requirement()
        error_cleanup_resets_tool_phase_test = get_run_sql_requirement() is None
        no_synthetic_reasoning_content_test = (
            no_synthetic_reasoning_content_test
            and not any(
                contains_reasoning_content(payload)
                for payload in answer_only_service.parent_payloads
            )
        )

        class SyntheticBadRequestError(RuntimeError):
            pass

        class FailingDeepSeekService(DeepSeekPolicyService):
            async def _send_parent_request(self, request: Any) -> Any:
                self.parent_call_count += 1
                self.parent_payloads.append(self._build_payload(request))
                if self.parent_call_count == 2:
                    raise SyntheticBadRequestError(
                        "synthetic second-call provider failure"
                    )
                return tracing_models.LlmResponse(
                    content="synthetic first-call response",
                    finish_reason="stop",
                )

        failing_hook = OriginalQuestionLifecycleHook()
        failing_service = FailingDeepSeekService.__new__(FailingDeepSeekService)
        failing_service.model = "deepseek-v4-pro"
        failing_service._client = DeepSeekClient()
        failing_service.parent_call_count = 0
        failing_service.parent_payloads = []

        async def run_provider_exception() -> tuple[bool, bool, bool, bool, bool]:
            await failing_hook.before_message(None, "provider exception")
            record_injected_sql_examples(1)
            context = get_request_diagnostics()
            assert context is not None and context.trace_directory is not None
            request_end_path = context.trace_directory / "request-end.json"
            await failing_service.send_request(fake_request)
            propagated = False
            try:
                await failing_service.send_request(fake_request)
            except SyntheticBadRequestError:
                propagated = True
            request_end = json.loads(request_end_path.read_text(encoding="utf-8"))
            cleaned = (
                get_request_diagnostics() is None
                and get_run_sql_requirement() is None
                and get_original_question() is None
            )
            before_double_finalize = request_end_path.read_bytes()
            await failing_hook.after_message(None)
            double_safe = (
                request_end_path.read_bytes() == before_double_finalize
                and get_request_diagnostics() is None
                and get_run_sql_requirement() is None
                and get_original_question() is None
            )
            recovery_service = DeepSeekPolicyService.__new__(DeepSeekPolicyService)
            recovery_service.model = "deepseek-v4-pro"
            recovery_service._client = DeepSeekClient()
            recovery_service.parent_call_count = 0
            recovery_service.parent_payloads = []
            await failing_hook.before_message(None, "request after provider exception")
            await recovery_service.send_request(fake_request)
            recovery_payload = recovery_service.parent_payloads[0]
            recovery_decision = get_run_sql_requirement().decisions[-1]
            error_cleanup_reset = (
                recovery_payload.get("tool_choice") == "auto"
                and recovery_payload.get("extra_body", {}).get("thinking")
                == {"type": "enabled"}
                and recovery_decision["provider_strategy"] == "original_request"
                and not get_run_sql_requirement().deepseek_non_thinking_tool_turn_active
            )
            await failing_hook.after_message(None)
            request_end_valid = (
                request_end.get("status") == "error"
                and request_end.get("exception_type")
                == "SyntheticBadRequestError"
                and request_end.get("context_cleanup_completed") is True
            )
            return (
                propagated,
                request_end_valid,
                cleaned,
                double_safe,
                error_cleanup_reset,
            )

        (
            exception_propagated,
            provider_exception_request_end_written_test,
            provider_exception_context_cleanup_test,
            double_finalization_idempotent_test,
            error_cleanup_resets_non_thinking_turn_test,
        ) = asyncio.run(run_provider_exception())
        no_provider_retry_on_bad_request_test = (
            exception_propagated and failing_service.parent_call_count == 2
        )
        no_provider_retry_test = no_provider_retry_on_bad_request_test

        sse_dir = trace_root / "synthetic-sse"
        synthetic_events = [
            {
                "conversation_id": "conv-self-test",
                "request_id": "req-self-test",
                "timestamp": "2026-01-01T00:00:00Z",
                "rich": {"type": "status_bar_update", "data": {"status": "working"}},
                "simple": None,
            },
            {
                "conversation_id": "conv-self-test",
                "request_id": "req-self-test",
                "timestamp": "2026-01-01T00:00:01Z",
                "rich": {
                    "type": "dataframe",
                    "data": {
                        "sql": "SELECT 1 AS value",
                        "columns": ["value"],
                        "data": [{"value": 1}],
                        "row_count": 1,
                        "execution_success": True,
                    },
                },
                "simple": {"type": "text", "text": "Query executed"},
            },
            {
                "conversation_id": "conv-self-test",
                "request_id": "req-self-test",
                "timestamp": "2026-01-01T00:00:02Z",
                "rich": {"type": "text", "data": {"content": "最终离线回答"}},
                "simple": {"type": "text", "text": "最终离线回答"},
            },
        ]
        synthetic_sse = "".join(
            "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            for event in synthetic_events
        ) + "data: [DONE]\n\n"

        class FakeSseResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def read(self) -> bytes:
                return synthetic_sse.encode("utf-8")

        original_urlopen = service_harness.urllib.request.urlopen
        original_parse_sse = service_harness.parse_sse_text
        raw_written_before_parse = False

        def fake_urlopen(*_args: Any, **_kwargs: Any) -> FakeSseResponse:
            return FakeSseResponse()

        def checked_parse_sse(raw_text: str):
            nonlocal raw_written_before_parse
            raw_written_before_parse = (sse_dir / "response.sse").is_file()
            return original_parse_sse(raw_text)

        service_harness.urllib.request.urlopen = fake_urlopen
        service_harness.parse_sse_text = checked_parse_sse
        try:
            synthetic_result = service_harness.post_sse(
                "离线请求", raw_evidence_dir=sse_dir
            )
        finally:
            service_harness.urllib.request.urlopen = original_urlopen
            service_harness.parse_sse_text = original_parse_sse
        sse_summary = json.loads(
            (sse_dir / "request-summary.json").read_text(encoding="utf-8")
        )
        raw_sse_persistence_test = (
            raw_written_before_parse
            and (sse_dir / "response.sse").read_text(encoding="utf-8")
            == synthetic_sse
            and len(json.loads((sse_dir / "events.json").read_text(encoding="utf-8")))
            == 3
        )
        final_text_capture_test = (
            (sse_dir / "final-text.txt").read_text(encoding="utf-8")
            == "最终离线回答"
            and bool(synthetic_result["answer"])
        )
        request_ids_capture_test = (
            sse_summary["conversation_id"] == "conv-self-test"
            and sse_summary["request_id"] == "req-self-test"
        )

    if original_trace_enabled is None:
        os.environ.pop("VANNA_REQUEST_TRACE_ENABLED", None)
    else:
        os.environ["VANNA_REQUEST_TRACE_ENABLED"] = original_trace_enabled
    if original_trace_dir is None:
        os.environ.pop("VANNA_REQUEST_TRACE_DIR", None)
    else:
        os.environ["VANNA_REQUEST_TRACE_DIR"] = original_trace_dir

    runner_self_test_pass = all(
        (
            accepted,
            rejected,
            zero_row_policy_test,
            zero_row_missing_schema_rejected,
            zero_row_missing_execution_success_rejected,
            empty_result_runner_columns_test,
            empty_result_tool_component_test,
            first_dataframe_event_selected_test,
            later_sql_does_not_overwrite_test,
            dataframe_event_count == 2,
            nonempty_dataframe_compatibility_test,
            suite_valid,
            memory_schema_valid,
            invalid_memory_expectation_rejected,
            not forbidden_imports,
            not forbidden_calls,
            same_path_rejected,
            early_import_rejected,
            formal_baseline_constants_valid,
            legacy_default_monitor_preserved,
            one_dynamic_parameter_rejected,
            invalid_dynamic_sha_rejected,
            dynamic_baseline_passed,
            dynamic_change_detected,
            request_trace_context_test,
            trace_context_cleanup_test,
            trace_disabled_no_write_test,
            final_system_prompt_capture_test,
            injected_sql_examples_capture_test,
            tool_schema_capture_test,
            multi_llm_call_capture_test,
            secret_redaction_test,
            raw_sse_persistence_test,
            final_text_capture_test,
            request_ids_capture_test,
            no_sql_example_no_force_test,
            sql_example_injected_requires_run_sql_test,
            filtered_examples_do_not_force_test,
            first_llm_call_forces_run_sql_test,
            second_llm_call_returns_to_auto_test,
            streaming_force_test,
            non_streaming_force_test,
            missing_run_sql_schema_fails_closed_test,
            request_object_not_mutated_test,
            concurrent_requirement_isolation_test,
            lifecycle_requirement_cleanup_test,
            trace_records_effective_tool_choice_test,
            deepseek_first_call_named_run_sql_non_thinking_test,
            deepseek_second_call_auto_non_thinking_test,
            deepseek_third_call_auto_non_thinking_test,
            deepseek_continuation_reasoning_effort_removed_test,
            deepseek_continuation_extra_body_preserved_test,
            deepseek_next_user_request_thinking_restored_test,
            deepseek_next_user_request_auto_restored_test,
            no_synthetic_reasoning_content_test,
            non_deepseek_policy_unchanged_test,
            thinking_override_failure_fails_closed_test,
            streaming_continuation_policy_test,
            non_streaming_continuation_policy_test,
            concurrent_non_thinking_turn_isolation_test,
            success_cleanup_resets_non_thinking_turn_test,
            error_cleanup_resets_non_thinking_turn_test,
            no_provider_retry_test,
            provider_exception_request_end_written_test,
            provider_exception_context_cleanup_test,
            double_finalization_idempotent_test,
            approved_example_zero_row_success_closes_tool_phase_test,
            approved_example_nonempty_success_closes_tool_phase_test,
            failed_sql_does_not_close_tool_phase_test,
            sql_guard_block_does_not_close_tool_phase_test,
            request_without_approved_example_unchanged_test,
            second_call_tools_removed_test,
            second_call_tool_choice_removed_test,
            second_call_thinking_disabled_test,
            second_call_reasoning_effort_removed_test,
            second_call_final_text_only_test,
            third_call_remains_answer_only_test,
            next_user_request_tools_restored_test,
            next_user_request_thinking_restored_test,
            streaming_answer_only_policy_test,
            non_streaming_answer_only_policy_test,
            concurrent_tool_phase_isolation_test,
            success_cleanup_resets_tool_phase_test,
            error_cleanup_resets_tool_phase_test,
        )
    )
    payload = {
        "suite_id": suite["suite_id"],
        "suite_content_sha256": suite_sha,
        "suite_case_count": len(suite["cases"]),
        "memory_case_count": len(suite["memory_cases"]),
        "memory_schema_valid": memory_schema_valid,
        "invalid_memory_expectation_rejected": invalid_memory_expectation_rejected,
        "structure_valid": suite_valid,
        "declarative_acceptance_test": accepted,
        "declarative_rejection_test": rejected,
        "zero_row_policy_test": zero_row_policy_test,
        "zero_row_missing_schema_rejected": zero_row_missing_schema_rejected,
        "zero_row_missing_execution_success_rejected": (
            zero_row_missing_execution_success_rejected
        ),
        "empty_result_runner_columns_test": empty_result_runner_columns_test,
        "empty_result_tool_component_test": empty_result_tool_component_test,
        "first_dataframe_event_selected_test": first_dataframe_event_selected_test,
        "later_sql_does_not_overwrite_test": later_sql_does_not_overwrite_test,
        "dataframe_event_count": dataframe_event_count,
        "nonempty_dataframe_compatibility_test": (
            nonempty_dataframe_compatibility_test
        ),
        "ZERO_ROW_POLICY_TEST": "PASS" if zero_row_policy_test else "FAIL",
        "ZERO_ROW_MISSING_SCHEMA_REJECTED": (
            "PASS" if zero_row_missing_schema_rejected else "FAIL"
        ),
        "ZERO_ROW_MISSING_EXECUTION_SUCCESS_REJECTED": (
            "PASS" if zero_row_missing_execution_success_rejected else "FAIL"
        ),
        "EMPTY_RESULT_RUNNER_COLUMNS_TEST": (
            "PASS" if empty_result_runner_columns_test else "FAIL"
        ),
        "EMPTY_RESULT_TOOL_COMPONENT_TEST": (
            "PASS" if empty_result_tool_component_test else "FAIL"
        ),
        "FIRST_DATAFRAME_EVENT_SELECTED_TEST": (
            "PASS" if first_dataframe_event_selected_test else "FAIL"
        ),
        "LATER_SQL_DOES_NOT_OVERWRITE_TEST": (
            "PASS" if later_sql_does_not_overwrite_test else "FAIL"
        ),
        "NONEMPTY_DATAFRAME_COMPATIBILITY_TEST": (
            "PASS" if nonempty_dataframe_compatibility_test else "FAIL"
        ),
        "runner_calls_f2_run_case": "run_case" in forbidden_calls,
        "runner_calls_context_diagnostics": "context_diagnostics" in forbidden_calls,
        "runner_imports_backend_memory": "backend.memory" in forbidden_imports,
        "runner_calls_create_memory": "create_memory" in forbidden_calls,
        "forbidden_imports": forbidden_imports,
        "formal_path_as_validation_rejected": same_path_rejected,
        "early_memory_module_import_rejected": early_import_rejected,
        "parent_memory_creation_disabled": not forbidden_calls,
        "formal_baseline_constants_valid": formal_baseline_constants_valid,
        "legacy_default_monitor_preserved": legacy_default_monitor_preserved,
        "one_dynamic_parameter_rejected": one_dynamic_parameter_rejected,
        "invalid_dynamic_sha_rejected": invalid_dynamic_sha_rejected,
        "dynamic_baseline_before_service_start_passed": dynamic_baseline_passed,
        "dynamic_formal_change_fail_fast_triggered": dynamic_change_detected,
        "REQUEST_TRACE_CONTEXT_TEST": "PASS" if request_trace_context_test else "FAIL",
        "TRACE_CONTEXT_CLEANUP_TEST": "PASS" if trace_context_cleanup_test else "FAIL",
        "TRACE_DISABLED_NO_WRITE_TEST": "PASS" if trace_disabled_no_write_test else "FAIL",
        "FINAL_SYSTEM_PROMPT_CAPTURE_TEST": "PASS" if final_system_prompt_capture_test else "FAIL",
        "INJECTED_SQL_EXAMPLES_CAPTURE_TEST": "PASS" if injected_sql_examples_capture_test else "FAIL",
        "TOOL_SCHEMA_CAPTURE_TEST": "PASS" if tool_schema_capture_test else "FAIL",
        "MULTI_LLM_CALL_CAPTURE_TEST": "PASS" if multi_llm_call_capture_test else "FAIL",
        "SECRET_REDACTION_TEST": "PASS" if secret_redaction_test else "FAIL",
        "RAW_SSE_PERSISTENCE_TEST": "PASS" if raw_sse_persistence_test else "FAIL",
        "FINAL_TEXT_CAPTURE_TEST": "PASS" if final_text_capture_test else "FAIL",
        "REQUEST_IDS_CAPTURE_TEST": "PASS" if request_ids_capture_test else "FAIL",
        "NO_SQL_EXAMPLE_NO_FORCE_TEST": "PASS" if no_sql_example_no_force_test else "FAIL",
        "SQL_EXAMPLE_INJECTED_REQUIRES_RUN_SQL_TEST": "PASS" if sql_example_injected_requires_run_sql_test else "FAIL",
        "FILTERED_EXAMPLES_DO_NOT_FORCE_TEST": "PASS" if filtered_examples_do_not_force_test else "FAIL",
        "FIRST_LLM_CALL_FORCES_RUN_SQL_TEST": "PASS" if first_llm_call_forces_run_sql_test else "FAIL",
        "SECOND_LLM_CALL_RETURNS_TO_AUTO_TEST": "PASS" if second_llm_call_returns_to_auto_test else "FAIL",
        "STREAMING_FORCE_TEST": "PASS" if streaming_force_test else "FAIL",
        "NON_STREAMING_FORCE_TEST": "PASS" if non_streaming_force_test else "FAIL",
        "MISSING_RUN_SQL_SCHEMA_FAILS_CLOSED_TEST": "PASS" if missing_run_sql_schema_fails_closed_test else "FAIL",
        "REQUEST_OBJECT_NOT_MUTATED_TEST": "PASS" if request_object_not_mutated_test else "FAIL",
        "CONCURRENT_REQUIREMENT_ISOLATION_TEST": "PASS" if concurrent_requirement_isolation_test else "FAIL",
        "LIFECYCLE_REQUIREMENT_CLEANUP_TEST": "PASS" if lifecycle_requirement_cleanup_test else "FAIL",
        "TRACE_RECORDS_EFFECTIVE_TOOL_CHOICE_TEST": "PASS" if trace_records_effective_tool_choice_test else "FAIL",
        "DEEPSEEK_FIRST_CALL_NAMED_RUN_SQL_NON_THINKING_TEST": "PASS" if deepseek_first_call_named_run_sql_non_thinking_test else "FAIL",
        "DEEPSEEK_SECOND_CALL_AUTO_NON_THINKING_TEST": "PASS" if deepseek_second_call_auto_non_thinking_test else "FAIL",
        "DEEPSEEK_THIRD_CALL_AUTO_NON_THINKING_TEST": "PASS" if deepseek_third_call_auto_non_thinking_test else "FAIL",
        "DEEPSEEK_CONTINUATION_REASONING_EFFORT_REMOVED_TEST": "PASS" if deepseek_continuation_reasoning_effort_removed_test else "FAIL",
        "DEEPSEEK_CONTINUATION_EXTRA_BODY_PRESERVED_TEST": "PASS" if deepseek_continuation_extra_body_preserved_test else "FAIL",
        "DEEPSEEK_NEXT_USER_REQUEST_THINKING_RESTORED_TEST": "PASS" if deepseek_next_user_request_thinking_restored_test else "FAIL",
        "DEEPSEEK_NEXT_USER_REQUEST_AUTO_RESTORED_TEST": "PASS" if deepseek_next_user_request_auto_restored_test else "FAIL",
        "NO_SYNTHETIC_REASONING_CONTENT_TEST": "PASS" if no_synthetic_reasoning_content_test else "FAIL",
        "NON_DEEPSEEK_POLICY_UNCHANGED_TEST": "PASS" if non_deepseek_policy_unchanged_test else "FAIL",
        "THINKING_OVERRIDE_FAILURE_FAILS_CLOSED_TEST": "PASS" if thinking_override_failure_fails_closed_test else "FAIL",
        "STREAMING_CONTINUATION_POLICY_TEST": "PASS" if streaming_continuation_policy_test else "FAIL",
        "NON_STREAMING_CONTINUATION_POLICY_TEST": "PASS" if non_streaming_continuation_policy_test else "FAIL",
        "CONCURRENT_NON_THINKING_TURN_ISOLATION_TEST": "PASS" if concurrent_non_thinking_turn_isolation_test else "FAIL",
        "SUCCESS_CLEANUP_RESETS_NON_THINKING_TURN_TEST": "PASS" if success_cleanup_resets_non_thinking_turn_test else "FAIL",
        "ERROR_CLEANUP_RESETS_NON_THINKING_TURN_TEST": "PASS" if error_cleanup_resets_non_thinking_turn_test else "FAIL",
        "NO_PROVIDER_RETRY_TEST": "PASS" if no_provider_retry_test else "FAIL",
        "PROVIDER_EXCEPTION_REQUEST_END_WRITTEN_TEST": "PASS" if provider_exception_request_end_written_test else "FAIL",
        "PROVIDER_EXCEPTION_CONTEXT_CLEANUP_TEST": "PASS" if provider_exception_context_cleanup_test else "FAIL",
        "DOUBLE_FINALIZATION_IDEMPOTENT_TEST": "PASS" if double_finalization_idempotent_test else "FAIL",
        "APPROVED_EXAMPLE_ZERO_ROW_SUCCESS_CLOSES_TOOL_PHASE_TEST": "PASS" if approved_example_zero_row_success_closes_tool_phase_test else "FAIL",
        "APPROVED_EXAMPLE_NONEMPTY_SUCCESS_CLOSES_TOOL_PHASE_TEST": "PASS" if approved_example_nonempty_success_closes_tool_phase_test else "FAIL",
        "FAILED_SQL_DOES_NOT_CLOSE_TOOL_PHASE_TEST": "PASS" if failed_sql_does_not_close_tool_phase_test else "FAIL",
        "SQL_GUARD_BLOCK_DOES_NOT_CLOSE_TOOL_PHASE_TEST": "PASS" if sql_guard_block_does_not_close_tool_phase_test else "FAIL",
        "REQUEST_WITHOUT_APPROVED_EXAMPLE_UNCHANGED_TEST": "PASS" if request_without_approved_example_unchanged_test else "FAIL",
        "SECOND_CALL_TOOLS_REMOVED_TEST": "PASS" if second_call_tools_removed_test else "FAIL",
        "SECOND_CALL_TOOL_CHOICE_REMOVED_TEST": "PASS" if second_call_tool_choice_removed_test else "FAIL",
        "SECOND_CALL_THINKING_DISABLED_TEST": "PASS" if second_call_thinking_disabled_test else "FAIL",
        "SECOND_CALL_REASONING_EFFORT_REMOVED_TEST": "PASS" if second_call_reasoning_effort_removed_test else "FAIL",
        "SECOND_CALL_FINAL_TEXT_ONLY_TEST": "PASS" if second_call_final_text_only_test else "FAIL",
        "THIRD_CALL_REMAINS_ANSWER_ONLY_TEST": "PASS" if third_call_remains_answer_only_test else "FAIL",
        "NEXT_USER_REQUEST_TOOLS_RESTORED_TEST": "PASS" if next_user_request_tools_restored_test else "FAIL",
        "NEXT_USER_REQUEST_THINKING_RESTORED_TEST": "PASS" if next_user_request_thinking_restored_test else "FAIL",
        "STREAMING_ANSWER_ONLY_POLICY_TEST": "PASS" if streaming_answer_only_policy_test else "FAIL",
        "NON_STREAMING_ANSWER_ONLY_POLICY_TEST": "PASS" if non_streaming_answer_only_policy_test else "FAIL",
        "CONCURRENT_TOOL_PHASE_ISOLATION_TEST": "PASS" if concurrent_tool_phase_isolation_test else "FAIL",
        "SUCCESS_CLEANUP_RESETS_TOOL_PHASE_TEST": "PASS" if success_cleanup_resets_tool_phase_test else "FAIL",
        "ERROR_CLEANUP_RESETS_TOOL_PHASE_TEST": "PASS" if error_cleanup_resets_tool_phase_test else "FAIL",
        "expected_formal_record_count": EXPECTED_FORMAL_RECORD_COUNT,
        "expected_formal_sha256": EXPECTED_FORMAL_SHA256,
        "runner_self_test_pass": runner_self_test_pass,
    }
    if evidence_dir:
        write_json(evidence_dir / "runner-self-test.json", payload)
        write_json(
            evidence_dir / "non-thinking-turn-test-results.json",
            {
                key: payload[key]
                for key in (
                    "DEEPSEEK_FIRST_CALL_NAMED_RUN_SQL_NON_THINKING_TEST",
                    "DEEPSEEK_SECOND_CALL_AUTO_NON_THINKING_TEST",
                    "DEEPSEEK_THIRD_CALL_AUTO_NON_THINKING_TEST",
                    "DEEPSEEK_CONTINUATION_REASONING_EFFORT_REMOVED_TEST",
                    "DEEPSEEK_CONTINUATION_EXTRA_BODY_PRESERVED_TEST",
                    "DEEPSEEK_NEXT_USER_REQUEST_THINKING_RESTORED_TEST",
                    "DEEPSEEK_NEXT_USER_REQUEST_AUTO_RESTORED_TEST",
                    "NO_SYNTHETIC_REASONING_CONTENT_TEST",
                    "NON_DEEPSEEK_POLICY_UNCHANGED_TEST",
                    "THINKING_OVERRIDE_FAILURE_FAILS_CLOSED_TEST",
                    "REQUEST_OBJECT_NOT_MUTATED_TEST",
                    "STREAMING_CONTINUATION_POLICY_TEST",
                    "NON_STREAMING_CONTINUATION_POLICY_TEST",
                    "CONCURRENT_NON_THINKING_TURN_ISOLATION_TEST",
                    "NO_PROVIDER_RETRY_TEST",
                )
            },
        )
        write_json(
            evidence_dir / "context-cleanup-test-results.json",
            {
                key: payload[key]
                for key in (
                    "SUCCESS_CLEANUP_RESETS_NON_THINKING_TURN_TEST",
                    "ERROR_CLEANUP_RESETS_NON_THINKING_TURN_TEST",
                    "PROVIDER_EXCEPTION_REQUEST_END_WRITTEN_TEST",
                    "PROVIDER_EXCEPTION_CONTEXT_CLEANUP_TEST",
                    "DOUBLE_FINALIZATION_IDEMPOTENT_TEST",
                    "NO_PROVIDER_RETRY_TEST",
                    "SUCCESS_CLEANUP_RESETS_TOOL_PHASE_TEST",
                    "ERROR_CLEANUP_RESETS_TOOL_PHASE_TEST",
                )
            },
        )
        write_json(
            evidence_dir / "tool-phase-closure-test-results.json",
            {
                key: payload[key]
                for key in (
                    "APPROVED_EXAMPLE_ZERO_ROW_SUCCESS_CLOSES_TOOL_PHASE_TEST",
                    "APPROVED_EXAMPLE_NONEMPTY_SUCCESS_CLOSES_TOOL_PHASE_TEST",
                    "FAILED_SQL_DOES_NOT_CLOSE_TOOL_PHASE_TEST",
                    "SQL_GUARD_BLOCK_DOES_NOT_CLOSE_TOOL_PHASE_TEST",
                    "REQUEST_WITHOUT_APPROVED_EXAMPLE_UNCHANGED_TEST",
                    "SECOND_CALL_TOOLS_REMOVED_TEST",
                    "SECOND_CALL_TOOL_CHOICE_REMOVED_TEST",
                    "SECOND_CALL_THINKING_DISABLED_TEST",
                    "SECOND_CALL_REASONING_EFFORT_REMOVED_TEST",
                    "SECOND_CALL_FINAL_TEXT_ONLY_TEST",
                    "THIRD_CALL_REMAINS_ANSWER_ONLY_TEST",
                    "NEXT_USER_REQUEST_TOOLS_RESTORED_TEST",
                    "NEXT_USER_REQUEST_THINKING_RESTORED_TEST",
                    "STREAMING_ANSWER_ONLY_POLICY_TEST",
                    "NON_STREAMING_ANSWER_ONLY_POLICY_TEST",
                    "CONCURRENT_TOOL_PHASE_ISOLATION_TEST",
                    "REQUEST_OBJECT_NOT_MUTATED_TEST",
                    "NO_SYNTHETIC_REASONING_CONTENT_TEST",
                    "NO_PROVIDER_RETRY_TEST",
                )
            },
        )
        write_json(
            evidence_dir / "runner-static-guard.json",
            {
                "forbidden_imports": forbidden_imports,
                "forbidden_calls": forbidden_calls,
                "formal_path_as_validation_rejected": same_path_rejected,
                "early_memory_module_import_rejected": early_import_rejected,
                "formal_baseline_constants_valid": formal_baseline_constants_valid,
                "expected_formal_record_count": EXPECTED_FORMAL_RECORD_COUNT,
                "expected_formal_sha256": EXPECTED_FORMAL_SHA256,
                "static_guard_pass": runner_self_test_pass,
            },
        )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["runner_self_test_pass"] else 2


def run_suite(
    suite_path: Path,
    data_dir: Path,
    agent_dir: Path,
    evidence_dir: Path,
    *,
    expected_formal_record_count: int,
    expected_formal_sha256: str,
) -> int:
    assert_no_early_memory_modules()
    validation_dir, formal_dir = validate_isolation_paths(data_dir)
    configure_parent_environment(validation_dir, agent_dir)
    from tools.regression_service_harness import (
        extract_chart_specs,
        post_sse,
        query_result_files,
        read_csv,
        redact,
        run_memory_regression,
        start_server,
        stop_server,
    )

    suite, suite_sha = load_and_validate_suite(suite_path)
    if suite_sha != EXPECTED_SUITE_SHA256:
        raise RuntimeError("SUITE_CONTENT_SHA256_MISMATCH")
    if not validation_dir.is_dir():
        raise RuntimeError(f"验证副本不存在: {validation_dir}")
    agent_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        evidence_dir / "parent-environment.json",
        {
            "vanna_data_dir": os.environ["VANNA_DATA_DIR"],
            "agent_data_dir": os.environ["AGENT_DATA_DIR"],
            "hf_hub_offline": os.environ["HF_HUB_OFFLINE"],
            "legacy_sql_examples_disabled": os.environ["VANNA_DISABLE_LEGACY_SQL_EXAMPLES"],
            "resolved_data_dir": str(validation_dir),
            "formal_runtime": str(formal_dir),
            "formal_monitor_record_count": expected_formal_record_count,
            "formal_monitor_sha256": expected_formal_sha256,
            "formal_path_referenced_as_data_dir": validation_dir == formal_dir,
            "parent_memory_diagnostics_executed": False,
            "parent_memory_creation_disabled": True,
        },
    )
    process = None
    logs: list[str] = []
    key = ""
    cases: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    memory_result: dict[str, Any] = {
        "memory_case_count": len(suite["memory_cases"]),
        "memory_pass_count": 0,
        "accepted": False,
        "cases": [],
    }
    failure: str | None = None
    try:
        formal_checkpoint(
            "before_memory_regression",
            checkpoints,
            evidence_dir,
            expected_record_count=expected_formal_record_count,
            expected_sha256=expected_formal_sha256,
        )
        memory_result = run_memory_regression(
            validation_dir, suite["memory_cases"], evidence_dir
        )
        if not memory_result["accepted"]:
            raise RuntimeError("MEMORY_REGRESSION_FAILED")
        formal_checkpoint(
            "after_memory_regression",
            checkpoints,
            evidence_dir,
            expected_record_count=expected_formal_record_count,
            expected_sha256=expected_formal_sha256,
        )
        formal_checkpoint(
            "before_service_start",
            checkpoints,
            evidence_dir,
            expected_record_count=expected_formal_record_count,
            expected_sha256=expected_formal_sha256,
        )
        process, logs, _, key = start_server(validation_dir, agent_dir, False)
        write_json(
            evidence_dir / "server-environment.json",
            {
                "server_entry_point": "step4_server.py",
                "vanna_data_dir": str(validation_dir),
                "agent_data_dir": str(agent_dir),
                "resolved_data_dir": str(validation_dir),
                "confirmation_method": "start_server arguments and child env construction contract",
                "server_started": True,
                "parent_and_server_data_dir_match": Path(os.environ["VANNA_DATA_DIR"]).resolve() == validation_dir,
            },
        )
        formal_checkpoint(
            "after_service_start",
            checkpoints,
            evidence_dir,
            expected_record_count=expected_formal_record_count,
            expected_sha256=expected_formal_sha256,
        )
        for index, case in enumerate(suite["cases"], start=1):
            raw = run_http_case(
                case,
                agent_dir,
                post_sse=post_sse,
                query_result_files=query_result_files,
                read_csv=read_csv,
                extract_chart_specs=extract_chart_specs,
            )
            cases.append({"validation": validate_case(case, raw), "execution": sanitized_case_result(raw)})
            formal_checkpoint(
                f"after_case_{index:02d}",
                checkpoints,
                evidence_dir,
                expected_record_count=expected_formal_record_count,
                expected_sha256=expected_formal_sha256,
            )
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
    finally:
        stop_server(process)
        try:
            formal_checkpoint(
                "after_service_stop",
                checkpoints,
                evidence_dir,
                expected_record_count=expected_formal_record_count,
                expected_sha256=expected_formal_sha256,
            )
        except Exception as error:
            if failure is None:
                failure = f"{type(error).__name__}: {error}"

    pass_count = sum(int(item["validation"]["accepted"]) for item in cases)
    fail_fast_triggered = any(not item["passed"] for item in checkpoints)
    payload = {
        "suite_id": suite["suite_id"],
        "suite_content_sha256": suite_sha,
        "question_count": len(cases),
        "question_pass_count": pass_count,
        "memory_case_count": memory_result["memory_case_count"],
        "memory_pass_count": memory_result["memory_pass_count"],
        "memory_accepted": memory_result["accepted"],
        "memory_cases": memory_result["cases"],
        "accepted": (
            failure is None
            and not fail_fast_triggered
            and memory_result["accepted"]
            and pass_count == len(cases) == suite["case_count"]
        ),
        "parent_memory_diagnostics_executed": False,
        "parent_memory_creation_disabled": True,
        "formal_runtime_fail_fast_triggered": fail_fast_triggered,
        "formal_monitor_record_count": expected_formal_record_count,
        "formal_monitor_sha256": expected_formal_sha256,
        "formal_monitor_checkpoints": len(checkpoints),
        "failure": failure,
        "cases": cases,
    }
    write_json(evidence_dir / "regression-result.json", payload)
    (evidence_dir / "server-log.txt").write_text(redact("\n".join(logs), [key]), encoding="utf-8")
    print(
        json.dumps(
            {
                name: payload[name]
                for name in (
                    "suite_id",
                    "suite_content_sha256",
                    "question_count",
                    "question_pass_count",
                    "memory_case_count",
                    "memory_pass_count",
                    "memory_accepted",
                    "accepted",
                    "formal_runtime_fail_fast_triggered",
                    "failure",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["accepted"] else 2


def main() -> int:
    assert_no_early_memory_modules()
    args = parse_args()
    expected_record_count, expected_sha256 = resolve_formal_monitor_baseline(
        args.expected_formal_record_count, args.expected_formal_sha256
    )
    suite_path = args.suite.resolve()
    evidence_dir = args.evidence_dir.resolve() if args.evidence_dir else None
    if args.self_test:
        return self_test(suite_path, evidence_dir)
    if not args.data_dir or not args.agent_dir or not evidence_dir:
        raise RuntimeError("完整运行必须提供 --data-dir、--agent-dir 和 --evidence-dir")
    return run_suite(
        suite_path,
        args.data_dir.resolve(),
        args.agent_dir.resolve(),
        evidence_dir,
        expected_formal_record_count=expected_record_count,
        expected_formal_sha256=expected_sha256,
    )


if __name__ == "__main__":
    raise SystemExit(main())
