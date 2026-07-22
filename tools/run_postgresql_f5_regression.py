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
        "expected_formal_record_count": EXPECTED_FORMAL_RECORD_COUNT,
        "expected_formal_sha256": EXPECTED_FORMAL_SHA256,
        "runner_self_test_pass": runner_self_test_pass,
    }
    if evidence_dir:
        write_json(evidence_dir / "runner-self-test.json", payload)
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
