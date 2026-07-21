"""训练批次的确定性、纯只读静态校验。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.sql_guard import SQLGuard
from training.sop.batch_schema import TrainingBatch


ERROR_CODES = (
    "BATCH_SCHEMA_INVALID",
    "BATCH_NOT_FROZEN",
    "BATCH_COUNT_MISMATCH",
    "DUPLICATE_SAMPLE_ID",
    "SAMPLE_LEVEL_MISMATCH",
    "SAMPLE_NOT_APPROVED",
    "UNSUPPORTED_TOOL",
    "EMPTY_QUESTION",
    "EMPTY_SQL",
    "SQL_GUARD_REJECTED",
    "SQL_MULTIPLE_STATEMENTS",
    "EXPECTED_TABLES_MISMATCH",
    "OUTPUT_PATH_FORBIDDEN",
)
_ERROR_ORDER = {code: index for index, code in enumerate(ERROR_CODES)}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    sample_id: str
    reason: str

    def sort_key(self) -> tuple[int, str, str]:
        return (_ERROR_ORDER.get(self.code, len(ERROR_CODES)), self.sample_id, self.reason)


@dataclass
class SampleValidationResult:
    sample_id: str
    passed: bool
    normalized_sql: str
    used_tables: list[str]
    guard_severity: str
    guard_reason: str


@dataclass
class BatchValidationResult:
    valid: bool
    training_batch_id: str | None
    sample_count: int
    batch_content_sha256: str | None
    summary: dict[str, Any] | None
    sample_results: list[SampleValidationResult] = field(default_factory=list)
    errors: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "training_batch_id": self.training_batch_id,
            "sample_count": self.sample_count,
            "batch_content_sha256": self.batch_content_sha256,
            "summary": self.summary,
            "samples": [asdict(item) for item in self.sample_results],
            "errors": [asdict(item) for item in self.errors],
        }


def _normalize_table_name(value: str) -> str:
    cleaned = value.strip().strip('"`[]').lower()
    return cleaned.split(".")[-1].strip('"`[]')


def _normalized_tables(values: list[str]) -> list[str]:
    return sorted({_normalize_table_name(value) for value in values if value.strip()})


def _normalize_sql(sql: str) -> str:
    """仅规范化注释和 SQL 外部空白，不改变字符串字面量。"""

    output: list[str] = []
    index = 0
    quote: str | None = None
    pending_space = False
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if quote:
            output.append(char)
            if char == quote:
                if next_char == quote:
                    output.append(next_char)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in {"'", '"'}:
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            quote = char
            output.append(char)
            index += 1
            continue

        if char == "-" and next_char == "-":
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            pending_space = True
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(sql) and sql[index : index + 2] != "*/":
                index += 1
            index = min(index + 2, len(sql))
            pending_space = True
            continue

        if char.isspace():
            pending_space = True
            index += 1
            continue

        if pending_space and output and output[-1] != " ":
            output.append(" ")
        pending_space = False
        output.append(char)
        index += 1

    normalized = "".join(output).strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    return normalized


def _statement_count(sql: str) -> int:
    """统计引号和注释之外的非空 SQL 语句。"""

    statements: list[str] = []
    current: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if quote:
            current.append(char)
            if char == quote:
                if next_char == quote:
                    current.append(next_char)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue

        if char == "-" and next_char == "-":
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            current.append(" ")
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(sql) and sql[index : index + 2] != "*/":
                index += 1
            index = min(index + 2, len(sql))
            current.append(" ")
            continue

        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    final_statement = "".join(current).strip()
    if final_statement:
        statements.append(final_statement)
    return len(statements)


def _schema_error_result(data: Any, error: Exception) -> BatchValidationResult:
    issues: list[ValidationIssue] = []
    if isinstance(error, ValidationError):
        raw_samples = data.get("samples", []) if isinstance(data, dict) else []
        for item in sorted(error.errors(), key=lambda value: tuple(map(str, value["loc"]))):
            location = item["loc"]
            sample_id = "-"
            if len(location) >= 2 and location[0] == "samples" and isinstance(location[1], int):
                index = location[1]
                if index < len(raw_samples) and isinstance(raw_samples[index], dict):
                    sample_id = str(raw_samples[index].get("sample_id") or "-")
            field_name = ".".join(map(str, location)) or "batch"
            issues.append(
                ValidationIssue(
                    code="BATCH_SCHEMA_INVALID",
                    sample_id=sample_id,
                    reason=f"{field_name}: {item['msg']}",
                )
            )
    else:
        issues.append(
            ValidationIssue(
                code="BATCH_SCHEMA_INVALID", sample_id="-", reason=str(error)
            )
        )
    issues.sort(key=ValidationIssue.sort_key)
    return BatchValidationResult(
        valid=False,
        training_batch_id=data.get("training_batch_id") if isinstance(data, dict) else None,
        sample_count=len(data.get("samples", [])) if isinstance(data, dict) and isinstance(data.get("samples"), list) else 0,
        batch_content_sha256=None,
        summary=None,
        errors=issues,
    )


def _build_summary(batch: TrainingBatch) -> dict[str, Any]:
    return {
        "schema_version": batch.schema_version,
        "training_batch_id": batch.training_batch_id,
        "training_level": batch.training_level,
        "status": batch.status,
        "source": batch.source.strip(),
        "expected_new_memory_count": batch.expected_new_memory_count,
        "sample_count": len(batch.samples),
        "sample_ids": [sample.sample_id for sample in batch.samples],
        "samples": [
            {
                "sample_id": sample.sample_id,
                "question": sample.question.strip(),
                "tool_name": sample.tool_name,
                "args": {"sql": _normalize_sql(sample.args.sql)},
                "training_level": sample.training_level,
                "train_decision": sample.train_decision,
                "review_reason": sample.review_reason.strip(),
                "source": sample.source.strip(),
                "expected_behavior": sample.expected_behavior.strip(),
                "expected_tables": _normalized_tables(sample.expected_tables),
            }
            for sample in batch.samples
        ],
    }


def _summary_sha256(summary: dict[str, Any]) -> str:
    payload = json.dumps(
        summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_training_batch(
    data: Any, *, sql_guard: SQLGuard | None = None
) -> BatchValidationResult:
    """校验已解析的批次数据；不连接数据库、不打开 Chroma、不写 Memory。"""

    try:
        batch = TrainingBatch.model_validate(data)
    except ValidationError as error:
        return _schema_error_result(data, error)

    issues: list[ValidationIssue] = []
    if batch.schema_version != "1.0":
        issues.append(
            ValidationIssue("BATCH_SCHEMA_INVALID", "-", "schema_version 必须为 1.0")
        )
    if batch.status != "frozen":
        issues.append(
            ValidationIssue("BATCH_NOT_FROZEN", "-", "status 必须为 frozen")
        )
    if not batch.source.strip():
        issues.append(ValidationIssue("BATCH_SCHEMA_INVALID", "-", "source 不得为空"))
    if not batch.samples:
        issues.append(ValidationIssue("BATCH_SCHEMA_INVALID", "-", "samples 不得为空"))
    if batch.expected_new_memory_count != len(batch.samples):
        issues.append(
            ValidationIssue(
                "BATCH_COUNT_MISMATCH",
                "-",
                "expected_new_memory_count 必须等于 samples 数量",
            )
        )

    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for sample in batch.samples:
        if sample.sample_id in seen_ids:
            duplicate_ids.add(sample.sample_id)
        seen_ids.add(sample.sample_id)
    for sample_id in sorted(duplicate_ids):
        issues.append(
            ValidationIssue("DUPLICATE_SAMPLE_ID", sample_id, "批次内 sample_id 重复")
        )

    guard = sql_guard or SQLGuard()
    sample_results: list[SampleValidationResult] = []
    for sample in batch.samples:
        sample_issues: list[ValidationIssue] = []
        sample_id = sample.sample_id
        question = sample.question.strip()
        sql = sample.args.sql.strip()

        if not question:
            sample_issues.append(ValidationIssue("EMPTY_QUESTION", sample_id, "question 不得为空"))
        if not sql:
            sample_issues.append(ValidationIssue("EMPTY_SQL", sample_id, "SQL 不得为空"))
        if sample.training_level != batch.training_level:
            sample_issues.append(
                ValidationIssue(
                    "SAMPLE_LEVEL_MISMATCH",
                    sample_id,
                    "样本 training_level 必须与批次一致",
                )
            )
        if sample.train_decision != "approved":
            sample_issues.append(
                ValidationIssue("SAMPLE_NOT_APPROVED", sample_id, "train_decision 必须为 approved")
            )
        if sample.tool_name != "run_sql":
            sample_issues.append(
                ValidationIssue("UNSUPPORTED_TOOL", sample_id, "tool_name 必须为 run_sql")
            )
        for field_name, field_value in (
            ("review_reason", sample.review_reason),
            ("source", sample.source),
            ("expected_behavior", sample.expected_behavior),
        ):
            if not field_value.strip():
                sample_issues.append(
                    ValidationIssue(
                        "BATCH_SCHEMA_INVALID", sample_id, f"{field_name} 不得为空"
                    )
                )

        normalized_expected_tables = _normalized_tables(sample.expected_tables)
        if not normalized_expected_tables:
            sample_issues.append(
                ValidationIssue(
                    "BATCH_SCHEMA_INVALID", sample_id, "expected_tables 不得为空"
                )
            )
        if len(normalized_expected_tables) != len(sample.expected_tables):
            sample_issues.append(
                ValidationIssue(
                    "BATCH_SCHEMA_INVALID", sample_id, "expected_tables 不得重复或包含空值"
                )
            )

        if _statement_count(sql) > 1:
            sample_issues.append(
                ValidationIssue(
                    "SQL_MULTIPLE_STATEMENTS", sample_id, "SQL 只能包含一条语句"
                )
            )

        try:
            guard_result = guard.validate(sql=sql, query=question)
            if not guard_result.passed or guard_result.severity == "error":
                sample_issues.append(
                    ValidationIssue(
                        "SQL_GUARD_REJECTED", sample_id, guard_result.reason
                    )
                )
            used_tables = _normalized_tables(guard_result.used_tables)
            if used_tables != normalized_expected_tables:
                sample_issues.append(
                    ValidationIssue(
                        "EXPECTED_TABLES_MISMATCH",
                        sample_id,
                        f"SQLGuard tables={used_tables}; expected_tables={normalized_expected_tables}",
                    )
                )
            sample_results.append(
                SampleValidationResult(
                    sample_id=sample_id,
                    passed=not sample_issues,
                    normalized_sql=_normalize_sql(sql),
                    used_tables=used_tables,
                    guard_severity=guard_result.severity,
                    guard_reason=guard_result.reason,
                )
            )
        except Exception as error:
            sample_issues.append(
                ValidationIssue(
                    "SQL_GUARD_REJECTED",
                    sample_id,
                    f"SQLGuard 调用失败: {type(error).__name__}: {error}",
                )
            )
            sample_results.append(
                SampleValidationResult(
                    sample_id=sample_id,
                    passed=False,
                    normalized_sql=_normalize_sql(sql),
                    used_tables=[],
                    guard_severity="error",
                    guard_reason="SQLGuard 调用失败",
                )
            )
        issues.extend(sample_issues)

    issues.sort(key=ValidationIssue.sort_key)
    valid = not issues
    summary = _build_summary(batch) if valid else None
    return BatchValidationResult(
        valid=valid,
        training_batch_id=batch.training_batch_id,
        sample_count=len(batch.samples),
        batch_content_sha256=_summary_sha256(summary) if summary else None,
        summary=summary,
        sample_results=sample_results,
        errors=issues,
    )


def validate_training_batch_file(
    path: str | Path, *, sql_guard: SQLGuard | None = None
) -> BatchValidationResult:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return _schema_error_result({}, error)
    return validate_training_batch(data, sql_guard=sql_guard)
