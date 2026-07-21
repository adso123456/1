"""阶段 0B-1 训练批次契约与静态审查器测试。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard
from training.sop.batch_validator import validate_training_batch


FIXTURE_DIR = PROJECT_ROOT / "tools" / "fixtures" / "training_sop"
VALID_FIXTURE = FIXTURE_DIR / "valid_batch.json"
CLI = PROJECT_ROOT / "tools" / "validate_training_batch.py"


def load_fixture(name: str = "valid_batch.json") -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def error_codes(result) -> list[str]:
    return [issue.code for issue in result.errors]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=environment,
    )


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=True,
    ).stdout


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    valid_data = load_fixture()
    valid_result = validate_training_batch(valid_data)
    results.append(
        (
            "合法批次通过",
            valid_result.valid and valid_result.sample_count == 2,
            str(valid_result.to_dict()),
        )
    )

    expected_top_level_fields = {
        "schema_version",
        "training_batch_id",
        "training_level",
        "status",
        "source",
        "expected_new_memory_count",
        "sample_count",
        "sample_ids",
        "samples",
    }
    expected_sample_fields = {
        "sample_id",
        "question",
        "tool_name",
        "args",
        "training_level",
        "train_decision",
        "review_reason",
        "source",
        "expected_behavior",
        "expected_tables",
    }
    summary = valid_result.summary or {}
    canonical_fields_ok = (
        set(summary) == expected_top_level_fields
        and bool(summary.get("samples"))
        and set(summary["samples"][0]) == expected_sample_fields
        and set(summary["samples"][0]["args"]) == {"sql"}
    )
    results.append(
        (
            "规范化摘要覆盖完整有效契约",
            canonical_fields_ok,
            f"top={sorted(summary)}; sample={sorted(summary.get('samples', [{}])[0])}",
        )
    )

    def change_training_level(data: dict) -> None:
        data["training_level"] = "level4_changed_sql_examples"
        for sample in data["samples"]:
            sample["training_level"] = data["training_level"]

    def change_expected_table_with_sql(data: dict) -> None:
        data["samples"][0]["args"]["sql"] = (
            "SELECT outlet_name FROM rs_outlet_info_v2 LIMIT 10"
        )
        data["samples"][0]["expected_tables"] = ["rs_outlet_info_v2"]

    meaningful_mutations = [
        ("顶层 source", lambda data: data.__setitem__("source", "变更后的纯测试来源")),
        (
            "training_batch_id",
            lambda data: data.__setitem__(
                "training_batch_id", "level4-fixture-20260714-99"
            ),
        ),
        ("training_level", change_training_level),
        (
            "样本 question",
            lambda data: data["samples"][0].__setitem__(
                "question", "变更后的测试区域排污口问题是什么？"
            ),
        ),
        (
            "样本 SQL 语义",
            lambda data: data["samples"][0]["args"].__setitem__(
                "sql",
                "SELECT outlet_code, area_name FROM rs_outlet "
                "ORDER BY outlet_code LIMIT 10",
            ),
        ),
        (
            "样本 review_reason",
            lambda data: data["samples"][0].__setitem__(
                "review_reason", "变更后的静态审查批准原因"
            ),
        ),
        (
            "样本 source",
            lambda data: data["samples"][0].__setitem__(
                "source", "变更后的纯模拟样本来源"
            ),
        ),
        (
            "样本 expected_behavior",
            lambda data: data["samples"][0].__setitem__(
                "expected_behavior", "变更后的预期查询行为"
            ),
        ),
        ("样本 expected_tables 与 SQL", change_expected_table_with_sql),
    ]
    for field_name, mutate in meaningful_mutations:
        changed_data = copy.deepcopy(valid_data)
        mutate(changed_data)
        changed_result = validate_training_batch(changed_data)
        results.append(
            (
                f"有效字段变化改变 digest：{field_name}",
                valid_result.valid
                and changed_result.valid
                and changed_result.batch_content_sha256
                != valid_result.batch_content_sha256,
                str(changed_result.batch_content_sha256),
            )
        )

    whitespace_equivalent = copy.deepcopy(valid_data)
    whitespace_equivalent["source"] = f"  {whitespace_equivalent['source']}  "
    for field_name in ("question", "review_reason", "source", "expected_behavior"):
        value = whitespace_equivalent["samples"][0][field_name]
        whitespace_equivalent["samples"][0][field_name] = f"  {value}  "

    sql_whitespace_equivalent = copy.deepcopy(valid_data)
    sql_whitespace_equivalent["samples"][0]["args"]["sql"] = (
        "  SELECT  outlet_name,  area_name\n"
        "FROM  rs_outlet\nORDER  BY  outlet_name\nLIMIT  10  "
    )

    semicolon_equivalent = copy.deepcopy(valid_data)
    semicolon_equivalent["samples"][0]["args"]["sql"] += ";"

    schema_equivalent = copy.deepcopy(valid_data)
    schema_equivalent["samples"][1]["expected_tables"] = ["rs_outlet"]

    equivalence_cases = [
        ("普通字段首尾空白", whitespace_equivalent),
        ("SQL 外部多余空白", sql_whitespace_equivalent),
        ("SQL 末尾单个分号", semicolon_equivalent),
        ("expected_tables schema 前缀", schema_equivalent),
    ]
    for case_name, equivalent_data in equivalence_cases:
        equivalent_result = validate_training_batch(equivalent_data)
        results.append(
            (
                f"规范化等价保持 digest：{case_name}",
                valid_result.valid
                and equivalent_result.valid
                and equivalent_result.batch_content_sha256
                == valid_result.batch_content_sha256,
                str(equivalent_result.batch_content_sha256),
            )
        )

    def joined_batch(expected_tables: list[str]) -> dict:
        data = copy.deepcopy(valid_data)
        data["samples"][0]["question"] = "测试两张排污口表的关联字段是什么？"
        data["samples"][0]["args"]["sql"] = (
            "SELECT ro.outlet_name, info.outlet_code_national "
            "FROM rs_outlet ro "
            "JOIN rs_outlet_info_v2 info ON ro.outlet_name = info.outlet_name "
            "LIMIT 10"
        )
        data["samples"][0]["expected_tables"] = expected_tables
        return data

    table_order_first = validate_training_batch(
        joined_batch(["rs_outlet", "rs_outlet_info_v2"])
    )
    table_order_second = validate_training_batch(
        joined_batch(["public.rs_outlet_info_v2", "public.rs_outlet"])
    )
    results.append(
        (
            "规范化等价保持 digest：expected_tables 顺序",
            table_order_first.valid
            and table_order_second.valid
            and table_order_first.batch_content_sha256
            == table_order_second.batch_content_sha256,
            f"first={table_order_first.batch_content_sha256}; "
            f"second={table_order_second.batch_content_sha256}; "
            f"errors={[issue.__dict__ for issue in table_order_first.errors + table_order_second.errors]}",
        )
    )

    unknown_field = copy.deepcopy(valid_data)
    unknown_field["unexpected"] = True
    schema_result = validate_training_batch(unknown_field)
    results.append(
        (
            "非法 schema 被拒绝",
            not schema_result.valid and "BATCH_SCHEMA_INVALID" in error_codes(schema_result),
            str(error_codes(schema_result)),
        )
    )

    not_frozen = copy.deepcopy(valid_data)
    not_frozen["status"] = "draft"
    frozen_result = validate_training_batch(not_frozen)
    results.append(
        (
            "status 非 frozen 被拒绝",
            "BATCH_NOT_FROZEN" in error_codes(frozen_result),
            str(error_codes(frozen_result)),
        )
    )

    wrong_count = copy.deepcopy(valid_data)
    wrong_count["expected_new_memory_count"] = 3
    count_result = validate_training_batch(wrong_count)
    results.append(
        (
            "expected count 不一致被拒绝",
            "BATCH_COUNT_MISMATCH" in error_codes(count_result),
            str(error_codes(count_result)),
        )
    )

    duplicate_result = validate_training_batch(load_fixture("invalid_duplicate_sample_id.json"))
    results.append(
        (
            "批次内 sample_id 重复被拒绝",
            "DUPLICATE_SAMPLE_ID" in error_codes(duplicate_result),
            str(error_codes(duplicate_result)),
        )
    )

    wrong_level = copy.deepcopy(valid_data)
    wrong_level["samples"][0]["training_level"] = "level4_other_sql_examples"
    level_result = validate_training_batch(wrong_level)
    results.append(
        (
            "training_level 不一致被拒绝",
            "SAMPLE_LEVEL_MISMATCH" in error_codes(level_result),
            str(error_codes(level_result)),
        )
    )

    rejected = copy.deepcopy(valid_data)
    rejected["samples"][0]["train_decision"] = "rejected"
    decision_result = validate_training_batch(rejected)
    results.append(
        (
            "非 approved 被拒绝",
            "SAMPLE_NOT_APPROVED" in error_codes(decision_result),
            str(error_codes(decision_result)),
        )
    )

    wrong_tool = copy.deepcopy(valid_data)
    wrong_tool["samples"][0]["tool_name"] = "visualize_data"
    tool_result = validate_training_batch(wrong_tool)
    results.append(
        (
            "非 run_sql 被拒绝",
            "UNSUPPORTED_TOOL" in error_codes(tool_result),
            str(error_codes(tool_result)),
        )
    )

    empty_values = copy.deepcopy(valid_data)
    empty_values["samples"][0]["question"] = "  "
    empty_values["samples"][0]["args"]["sql"] = "  "
    empty_result = validate_training_batch(empty_values)
    results.append(
        (
            "空问题和空 SQL 被拒绝",
            {"EMPTY_QUESTION", "EMPTY_SQL"}.issubset(error_codes(empty_result)),
            str(error_codes(empty_result)),
        )
    )

    non_select_result = validate_training_batch(load_fixture("invalid_non_select_sql.json"))
    results.append(
        (
            "非 SELECT 被 SQLGuard 拒绝",
            "SQL_GUARD_REJECTED" in error_codes(non_select_result),
            str(error_codes(non_select_result)),
        )
    )

    multiple = copy.deepcopy(valid_data)
    multiple["samples"] = [multiple["samples"][0]]
    multiple["expected_new_memory_count"] = 1
    multiple["samples"][0]["args"]["sql"] = (
        "SELECT outlet_name FROM rs_outlet; "
        "SELECT area_name FROM rs_outlet"
    )
    multiple_result = validate_training_batch(multiple)
    results.append(
        (
            "多语句被拒绝",
            "SQL_MULTIPLE_STATEMENTS" in error_codes(multiple_result),
            str(error_codes(multiple_result)),
        )
    )

    mismatch_result = validate_training_batch(load_fixture("invalid_metadata_mismatch.json"))
    results.append(
        (
            "表清单不一致被拒绝",
            "EXPECTED_TABLES_MISMATCH" in error_codes(mismatch_result),
            str(error_codes(mismatch_result)),
        )
    )

    invalid_results = [
        schema_result,
        frozen_result,
        count_result,
        duplicate_result,
        non_select_result,
        mismatch_result,
    ]
    results.append(
        (
            "无效批次不生成正式摘要",
            all(
                not result.valid
                and result.summary is None
                and result.batch_content_sha256 is None
                for result in invalid_results
            ),
            str(
                [
                    (result.valid, result.summary, result.batch_content_sha256)
                    for result in invalid_results
                ]
            ),
        )
    )

    repeated_result = validate_training_batch(load_fixture())
    results.append(
        (
            "相同输入 digest 一致",
            valid_result.batch_content_sha256 == repeated_result.batch_content_sha256,
            str(valid_result.batch_content_sha256),
        )
    )

    reordered = copy.deepcopy(valid_data)
    reordered["samples"].reverse()
    reordered_result = validate_training_batch(reordered)
    results.append(
        (
            "样本顺序变化会改变 digest",
            reordered_result.valid
            and reordered_result.batch_content_sha256 != valid_result.batch_content_sha256,
            str(reordered_result.batch_content_sha256),
        )
    )

    stable_errors_data = copy.deepcopy(valid_data)
    stable_errors_data["status"] = "draft"
    stable_errors_data["expected_new_memory_count"] = 99
    stable_errors_data["samples"][0]["tool_name"] = "other"
    stable_first = validate_training_batch(stable_errors_data)
    stable_second = validate_training_batch(copy.deepcopy(stable_errors_data))
    first_errors = [issue.__dict__ for issue in stable_first.errors]
    second_errors = [issue.__dict__ for issue in stable_second.errors]
    results.append(
        ("错误列表顺序稳定", first_errors == second_errors, str(first_errors))
    )

    class CountingGuard:
        def __init__(self) -> None:
            self.inner = SQLGuard()
            self.calls: list[tuple[str, str]] = []

        def validate(self, sql: str, query: str):
            self.calls.append((sql, query))
            return self.inner.validate(sql=sql, query=query)

    counting_guard = CountingGuard()
    counted_result = validate_training_batch(valid_data, sql_guard=counting_guard)
    results.append(
        (
            "每条样本调用 SQLGuard.validate",
            counted_result.valid and len(counting_guard.calls) == 2,
            f"calls={len(counting_guard.calls)}",
        )
    )

    status_before = git_status()
    cli_valid = run_cli(str(VALID_FIXTURE))
    status_after = git_status()
    results.append(
        (
            "CLI 成功返回 0 且默认不产生仓库文件",
            cli_valid.returncode == 0
            and "VALID" in cli_valid.stdout
            and status_before == status_after,
            f"returncode={cli_valid.returncode}",
        )
    )

    cli_invalid = run_cli(str(FIXTURE_DIR / "invalid_duplicate_sample_id.json"))
    results.append(
        (
            "CLI 失败返回非 0 并输出稳定错误码",
            cli_invalid.returncode != 0
            and "INVALID" in cli_invalid.stdout
            and "DUPLICATE_SAMPLE_ID" in cli_invalid.stdout,
            f"returncode={cli_invalid.returncode}",
        )
    )

    with tempfile.TemporaryDirectory(prefix="training-sop-output-") as temp_dir:
        json_output = Path(temp_dir) / "result.json"
        markdown_output = Path(temp_dir) / "result.md"
        output_process = run_cli(
            str(VALID_FIXTURE),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        )
        output_ok = (
            output_process.returncode == 0
            and json_output.is_file()
            and markdown_output.is_file()
            and json.loads(json_output.read_text(encoding="utf-8"))["valid"] is True
        )
        results.append(
            ("仓库外机器和人类可读结果可生成", output_ok, str(temp_dir))
        )

    forbidden_results = []
    with tempfile.TemporaryDirectory(prefix="training-sop-forbidden-") as temp_dir:
        forbidden_paths = [
            Path(temp_dir) / "vanna_data" / "should_not_exist.json",
            Path(temp_dir) / "agent_data" / "should_not_exist.md",
        ]
        for forbidden_path in forbidden_paths:
            option = (
                "--json-output"
                if forbidden_path.suffix == ".json"
                else "--markdown-output"
            )
            process = run_cli(str(VALID_FIXTURE), option, str(forbidden_path))
            forbidden_results.append(
                process.returncode != 0
                and "OUTPUT_PATH_FORBIDDEN" in process.stdout
                and not forbidden_path.exists()
            )
    results.append(
        (
            "禁止输出到 vanna_data 和 agent_data",
            all(forbidden_results),
            str(forbidden_results),
        )
    )

    forbidden_modules = sorted(
        name
        for name in sys.modules
        if name == "chromadb"
        or name.startswith("chromadb.")
        or name == "psycopg2"
        or name.startswith("psycopg2.")
    )
    results.append(
        (
            "验证过程不导入 Chroma 或数据库驱动",
            not forbidden_modules,
            str(forbidden_modules),
        )
    )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
