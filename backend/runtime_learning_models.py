"""运行时学习候选、证据、Judge 结果的严格数据模型。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 候选状态有限状态机；禁止任意字符串状态。
LearningStatus = Literal[
    "staged",
    "judging",
    "pass",
    "needs_review",
    "reject",
    "publish_pending",
    "publishing",
    "published",
    "publish_failed",
    "superseded",
]

# 合法状态迁移：from -> allowed to。不在表中的迁移一律拒绝。
_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "staged": frozenset({"judging", "needs_review", "reject", "pass"}),
    "judging": frozenset({"pass", "needs_review", "reject", "staged"}),
    "pass": frozenset({"publish_pending", "superseded"}),
    "needs_review": frozenset(
        {"publish_pending", "reject", "superseded", "pass"}
    ),
    "reject": frozenset({"superseded"}),
    "publish_pending": frozenset(
        {"publishing", "needs_review", "reject", "publish_failed", "superseded"}
    ),
    "publishing": frozenset({"published", "publish_failed"}),
    "published": frozenset({"superseded"}),
    "publish_failed": frozenset(
        {"publish_pending", "publishing", "needs_review", "reject", "superseded"}
    ),
    "superseded": frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    allowed = _STATUS_TRANSITIONS.get(current)
    if allowed is None:
        return False
    return target in allowed


class JudgeVerdict(BaseModel):
    """Judge 固定 JSON 输出，自动 PASS 门禁以此为唯一事实源。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "NEEDS_REVIEW", "REJECT"]
    confidence: float = Field(ge=0.0, le=1.0)
    question_sql_aligned: bool = False
    answer_result_aligned: bool = False
    metadata_valid: bool = False
    business_ambiguity: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class ResultEvidence(BaseModel):
    """受限、脱敏的结果证据，不保存完整 DataFrame。"""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    total_row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    max_rows: int = Field(default=0, ge=0)
    numeric_summary: dict[str, dict[str, float]] = Field(default_factory=dict)
    result_sha256: str = ""


class LearningCandidate(BaseModel):
    """一条学习候选的完整字段（与候选库 schema 对应）。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    source_id: str
    conversation_id: str
    request_id: str
    database_type: str
    captured_runtime_revision: int = Field(ge=0)
    reviewed_runtime_revision: int = Field(default=0, ge=0)
    published_runtime_revision: int = Field(default=0, ge=0)
    question: str
    normalized_question: str
    question_sha256: str
    sql: str
    normalized_sql: str
    sql_sha256: str
    args_json: str
    content_fingerprint: str
    guard_result_json: str
    used_tables: list[str] = Field(default_factory=list)
    used_columns: list[str] = Field(default_factory=list)
    result_summary_json: str
    result_evidence_json: str
    result_truncated: bool = False
    result_sha256: str = ""
    final_answer: str
    answer_sha256: str
    status: LearningStatus = "staged"
    judge_verdict: str = ""
    judge_confidence: float = 0.0
    judge_reason: str = ""
    conflict_status: str = ""
    judge_attempts: int = Field(default=0, ge=0)
    publish_batch_id: str = ""
    last_error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    judged_at: float | None = None
    published_at: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "conversation_id": self.conversation_id,
            "request_id": self.request_id,
            "database_type": self.database_type,
            "captured_runtime_revision": self.captured_runtime_revision,
            "reviewed_runtime_revision": self.reviewed_runtime_revision,
            "published_runtime_revision": self.published_runtime_revision,
            "question": self.question,
            "normalized_question": self.normalized_question,
            "question_sha256": self.question_sha256,
            "sql": self.sql,
            "normalized_sql": self.normalized_sql,
            "sql_sha256": self.sql_sha256,
            "args_json": self.args_json,
            "content_fingerprint": self.content_fingerprint,
            "guard_result_json": self.guard_result_json,
            "used_tables_json": json.dumps(
                self.used_tables, ensure_ascii=False, sort_keys=True
            ),
            "used_columns_json": json.dumps(
                self.used_columns, ensure_ascii=False, sort_keys=True
            ),
            "result_summary_json": self.result_summary_json,
            "result_evidence_json": self.result_evidence_json,
            "result_truncated": int(self.result_truncated),
            "result_sha256": self.result_sha256,
            "final_answer": self.final_answer,
            "answer_sha256": self.answer_sha256,
            "status": self.status,
            "judge_verdict": self.judge_verdict,
            "judge_confidence": self.judge_confidence,
            "judge_reason": self.judge_reason,
            "conflict_status": self.conflict_status,
            "judge_attempts": self.judge_attempts,
            "publish_batch_id": self.publish_batch_id,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "judged_at": self.judged_at,
            "published_at": self.published_at,
        }


class JudgeRunRecord(BaseModel):
    """一次 Judge 调用的完整记录。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    candidate_id: str
    verdict: str
    confidence: float
    reason: str
    payload_json: str
    created_at: float


class PublishBatchRecord(BaseModel):
    """一次发布微批次的记录。"""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    source_id: str
    candidate_ids: list[str]
    status: str
    runtime_revision_before: int
    runtime_revision_after: int = 0
    error_json: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
