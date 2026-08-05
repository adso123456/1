"""运行时学习候选捕获：硬门禁 + 受限结果证据 + 确定性候选身份。

捕获必须绝不抛出、绝不阻塞 SSE 主链路、绝不调用 Judge、
绝不写正式 Chroma、绝不调用 save_tool_usage()。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from backend.learning_candidate_store import (
    LearningCandidateStore,
    normalize_question,
    normalize_sql,
)
from backend.query_performance import QueryPerformanceState
from backend.runtime_learning_models import LearningCandidate, ResultEvidence
from config.learning_settings import OnlineLearningSettings

_SENSITIVE_COLUMN_RE = re.compile(
    r"password|passwd|secret|token|api_?key|private_?key|id_?card|phone|mobile|email|身份证|手机号|邮箱",
    flags=re.I,
)
_SELECT_STAR_RE = re.compile(r"\bselect\s+\*", flags=re.I | re.S)
_LIMIT_RE = re.compile(r"\blimit\b", flags=re.I)
_AGGREGATE_RE = re.compile(
    r"\b(count|sum|avg|min|max|stddev|var_pop|var_samp)\s*\(|\bgroup\s+by\b",
    flags=re.I,
)


def is_select_sql(sql: str) -> bool:
    """仅 SELECT 或 WITH...SELECT，且为单条语句。"""
    stripped = str(sql or "").strip().rstrip(";").strip()
    if not stripped:
        return False
    if ";" in stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith("select"):
        return True
    if lowered.startswith("with"):
        return bool(re.search(r"\bselect\b", lowered))
    return False


def has_select_star(sql: str) -> bool:
    return bool(_SELECT_STAR_RE.search(sql or ""))


def has_limit(sql: str) -> bool:
    return bool(_LIMIT_RE.search(sql or ""))


def is_aggregate_query(sql: str) -> bool:
    """聚合查询（结果规模天然受控）判断；不使用简单字符串全匹配。"""
    lowered = str(sql or "").lower()
    return bool(_AGGREGATE_RE.search(lowered))


def _is_binary(value: Any) -> bool:
    return isinstance(value, (bytes, bytearray, memoryview))


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and not isinstance(value, (bytes, bytearray))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate_cell(value: Any, max_cell_chars: int = 200) -> Any:
    if _is_binary(value):
        return "[binary]"
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            text = _canonical_json(value)
        except (TypeError, ValueError):
            return "[unserializable]"
    else:
        text = str(value)
    if len(text) > max_cell_chars:
        return text[:max_cell_chars] + "..."
    # 未截断返回原值，保留数值类型供 numeric_summary 使用
    return value


def build_result_evidence(
    metadata: dict[str, Any],
    *,
    max_rows: int,
    max_bytes: int,
) -> ResultEvidence | None:
    """从 run_sql 结果 metadata 生成受限、脱敏的证据。生成失败返回 None。"""
    try:
        columns = list(metadata.get("columns") or [])
        rows = list(metadata.get("results") or [])
        total_row_count = int(metadata.get("row_count") or 0)
    except (TypeError, ValueError):
        return None
    if not columns or total_row_count <= 0:
        return None

    safe_columns = [
        str(column) for column in columns if not _SENSITIVE_COLUMN_RE.search(str(column))
    ]
    safe_indices = [
        index
        for index, column in enumerate(columns)
        if not _SENSITIVE_COLUMN_RE.search(str(column))
    ]

    truncated = False
    kept_rows: list[list[Any]] = []
    for raw in rows:
        if len(kept_rows) >= max_rows:
            truncated = True
            break
        if isinstance(raw, dict):
            kept_rows.append(
                [
                    _truncate_cell(raw.get(columns[i]))
                    if i in safe_indices
                    else "[REDACTED]"
                    for i in range(len(columns))
                ]
            )
        elif isinstance(raw, (list, tuple)):
            kept_rows.append(
                [
                    _truncate_cell(raw[i]) if i < len(raw) and i in safe_indices else "[REDACTED]"
                    for i in range(len(columns))
                ]
            )
        else:
            truncated = True
            break
    if len(kept_rows) < min(total_row_count, max_rows):
        truncated = True

    numeric_summary: dict[str, dict[str, float]] = {}
    for col_index in safe_indices:
        column_name = str(columns[col_index])
        values = [
            row[col_index]
            for row in kept_rows
            if _is_numeric(row[col_index]) and row[col_index] is not None
        ]
        values = [float(value) for value in values if value is not None]
        if len(values) < 1:
            continue
        numeric_summary[column_name] = {
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "sum": round(sum(values), 6),
            "avg": round(sum(values) / len(values), 6),
        }

    evidence: dict[str, Any] = {
        "columns": columns,
        "rows": kept_rows,
        "row_count": len(kept_rows),
        "total_row_count": total_row_count,
        "truncated": truncated,
        "max_rows": max_rows,
        "numeric_summary": numeric_summary,
    }
    serialized = _canonical_json(evidence)
    if len(serialized.encode("utf-8")) > max_bytes:
        truncated = True
        while (
            kept_rows
            and len(_canonical_json(evidence).encode("utf-8")) > max_bytes
        ):
            kept_rows = kept_rows[: len(kept_rows) // 2]
            evidence["rows"] = kept_rows
            evidence["row_count"] = len(kept_rows)
            evidence["truncated"] = True
            evidence["numeric_summary"] = {}
    evidence["result_sha256"] = _sha256(_canonical_json(evidence))
    try:
        return ResultEvidence(**evidence)
    except (TypeError, ValueError):
        return None


def candidate_identity(
    *,
    source_id: str,
    question: str,
    sql: str,
) -> tuple[str, str, str, str, str]:
    """确定性身份：(candidate_id, normalized_question, normalized_sql, content_fingerprint, args_json)。"""
    normalized_question = normalize_question(question)
    normalized_sql = normalize_sql(sql)
    candidate_id = _sha256(f"{source_id}|{question}|{sql}")[:24]
    content_fingerprint = _sha256(
        f"{source_id}|{normalized_question}|{normalized_sql}"
    )
    args_json = json.dumps({"sql": sql}, ensure_ascii=False, sort_keys=True)
    return (
        candidate_id,
        normalized_question,
        normalized_sql,
        content_fingerprint,
        args_json,
    )


def capture_candidate(
    *,
    state: QueryPerformanceState,
    source_id: str,
    database_type: str,
    runtime_revision: int,
    final_answer: str,
    request_failed: bool,
    store: LearningCandidateStore,
    settings: OnlineLearningSettings,
) -> LearningCandidate | None:
    """V1 硬门禁全通过才写入 staged 候选；任何失败静默跳过，绝不抛出。"""
    try:
        if not settings.enabled or not settings.capture_enabled:
            return None
        if state.request_cancelled:
            return None
        if state.timeout_stage:
            return None
        if request_failed or state.request_failed:
            return None
        if state.successful_run_sql_count != 1:
            return None
        if not isinstance(source_id, str) or not source_id.strip():
            return None
        if not state.conversation_id or not state.request_id:
            return None
        if not isinstance(runtime_revision, int) or runtime_revision <= 0:
            return None

        question = str(state.question or "").strip()
        sql = str(state.last_sql or "").strip()
        if not question or not sql:
            return None
        answer = str(final_answer or "").strip()
        if not answer:
            return None

        if not is_select_sql(sql):
            return None
        if has_select_star(sql):
            return None
        if not has_limit(sql) and not is_aggregate_query(sql):
            return None

        metadata = dict(state.last_result_metadata or {})
        if str(metadata.get("query_type") or "") != "SELECT":
            return None
        try:
            if int(metadata.get("row_count") or 0) <= 0:
                return None
        except (TypeError, ValueError):
            return None

        guard = metadata.get("sql_guard")
        if not isinstance(guard, dict):
            return None
        if guard.get("passed") is not True:
            return None
        if str(guard.get("severity") or "") != "ok":
            return None
        if guard.get("forbidden_operations"):
            return None

        evidence = build_result_evidence(
            metadata,
            max_rows=settings.max_result_rows,
            max_bytes=settings.max_result_bytes,
        )
        if evidence is None:
            return None

        (
            candidate_id,
            normalized_question,
            normalized_sql,
            content_fingerprint,
            args_json,
        ) = candidate_identity(source_id=source_id, question=question, sql=sql)

        now = time.time()
        candidate = LearningCandidate(
            candidate_id=candidate_id,
            source_id=source_id,
            conversation_id=state.conversation_id,
            request_id=state.request_id,
            database_type=database_type,
            captured_runtime_revision=runtime_revision,
            question=question,
            normalized_question=normalized_question,
            question_sha256=_sha256(normalized_question),
            sql=sql,
            normalized_sql=normalized_sql,
            sql_sha256=_sha256(normalized_sql),
            args_json=args_json,
            content_fingerprint=content_fingerprint,
            guard_result_json=_canonical_json(guard),
            used_tables=list(guard.get("used_tables") or []),
            used_columns=list(guard.get("used_columns") or []),
            result_summary_json=_canonical_json(
                {
                    "row_count": metadata.get("row_count"),
                    "columns": metadata.get("columns"),
                    "query_type": metadata.get("query_type"),
                }
            ),
            result_evidence_json=evidence.model_dump_json(),
            result_truncated=evidence.truncated,
            result_sha256=evidence.result_sha256,
            final_answer=answer,
            answer_sha256=_sha256(answer),
            status="staged",
            created_at=now,
            updated_at=now,
        )
        store.save_candidate(candidate)
        return candidate
    except Exception:
        # 候选库写入失败绝不能影响用户问答。
        return None
