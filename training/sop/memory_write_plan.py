"""run_sql Tool Memory 的确定性写入计划契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

from training.sop.batch_validator import validate_training_batch


PLAN_SCHEMA_VERSION = "1.0"
RECORD_SCHEMA_VERSION = "1.0"
MEMORY_KIND = "tool_usage"
RECORD_ID_PREFIX = "toolmem-v1-"

PlanStatus = Literal[
    "create",
    "resume_same_batch",
    "preexisting_other_batch",
    "conflict",
]


class MemoryWritePlanError(ValueError):
    """批次无法进入写入计划生成阶段。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class MemoryContentIdentity:
    canonical_content: dict[str, Any]
    memory_content_sha256: str
    record_id: str


@dataclass(frozen=True)
class ExistingRecordSnapshot:
    record_id: str
    canonical_content: Mapping[str, Any]
    memory_content_sha256: str
    created_by_training_batch_id: str
    created_by_batch_content_sha256: str
    created_from_sample_id: str


@dataclass(frozen=True)
class WritePlanIssue:
    code: str
    sample_id: str
    record_id: str
    message: str


@dataclass(frozen=True)
class MemoryWritePlanItem:
    sample_id: str
    record_id: str
    delivery_item_sha256: str
    memory_content_sha256: str
    status: PlanStatus
    canonical_content: dict[str, Any]
    governance_metadata: dict[str, Any]
    compatibility_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryWritePlan:
    plan_schema_version: str
    batch_content_sha256: str
    training_batch_id: str
    expected_new_memory_count: int
    items: tuple[MemoryWritePlanItem, ...]
    create_count: int
    resume_same_batch_count: int
    preexisting_other_batch_count: int
    conflict_count: int
    executable: bool
    issues: tuple[WritePlanIssue, ...]
    write_plan_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_schema_version": self.plan_schema_version,
            "batch_content_sha256": self.batch_content_sha256,
            "training_batch_id": self.training_batch_id,
            "expected_new_memory_count": self.expected_new_memory_count,
            "items": [item.to_dict() for item in self.items],
            "create_count": self.create_count,
            "resume_same_batch_count": self.resume_same_batch_count,
            "preexisting_other_batch_count": self.preexisting_other_batch_count,
            "conflict_count": self.conflict_count,
            "executable": self.executable,
            "issues": [asdict(issue) for issue in self.issues],
            "write_plan_sha256": self.write_plan_sha256,
        }


@dataclass(frozen=True)
class ExecutionLedgerEntry:
    """后续执行器可使用的账本条目；本模块不负责持久化。"""

    execution_id: str
    batch_content_sha256: str
    write_plan_sha256: str
    record_id: str
    sample_id: str
    planned_status: PlanStatus
    execution_status: str
    created_this_attempt: bool
    error_code: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_memory_identity_from_canonical_content(
    canonical_content: Mapping[str, Any],
) -> MemoryContentIdentity:
    """为已经规范化的 Tool Memory 内容计算全局内容身份。"""

    canonical_copy = json.loads(_canonical_json(dict(canonical_content)).decode("utf-8"))
    content_sha256 = _sha256(_canonical_json(canonical_copy))
    return MemoryContentIdentity(
        canonical_content=canonical_copy,
        memory_content_sha256=content_sha256,
        record_id=RECORD_ID_PREFIX + content_sha256,
    )


def _delivery_item_sha256(
    batch_content_sha256: str, sample_id: str, record_id: str
) -> str:
    return _sha256(
        (batch_content_sha256 + sample_id + record_id).encode("utf-8")
    )


def _canonical_content(sample: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "question": sample["question"],
        "tool_name": sample["tool_name"],
        "args": {"sql": sample["args"]["sql"]},
        "success": True,
    }


def _governance_metadata(
    *,
    batch_content_sha256: str,
    training_batch_id: str,
    sample: Mapping[str, Any],
    memory_content_sha256: str,
) -> dict[str, Any]:
    return {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "memory_kind": MEMORY_KIND,
        "memory_content_sha256": memory_content_sha256,
        "created_by_training_batch_id": training_batch_id,
        "created_by_batch_content_sha256": batch_content_sha256,
        "created_from_sample_id": sample["sample_id"],
        "training_level": sample["training_level"],
        "train_decision": sample["train_decision"],
    }


def _compatibility_metadata(
    *,
    batch_content_sha256: str,
    training_batch_id: str,
    sample: Mapping[str, Any],
    memory_content_sha256: str,
) -> dict[str, Any]:
    return {
        "sample_id": sample["sample_id"],
        "training_level": sample["training_level"],
        "train_decision": sample["train_decision"],
        "review_reason": sample["review_reason"],
        "source": sample["source"],
        "expected_behavior": sample["expected_behavior"],
        "expected_tables": list(sample["expected_tables"]),
        "training_batch_id": training_batch_id,
        "batch_content_sha256": batch_content_sha256,
        "memory_content_sha256": memory_content_sha256,
    }


def _snapshot_status(
    *,
    snapshot_key: str,
    snapshot: ExistingRecordSnapshot,
    identity: MemoryContentIdentity,
    training_batch_id: str,
    batch_content_sha256: str,
    sample_id: str,
) -> PlanStatus:
    content_equal = _canonical_json(snapshot.canonical_content) == _canonical_json(
        identity.canonical_content
    )
    identity_equal = (
        snapshot_key == identity.record_id
        and snapshot.record_id == identity.record_id
        and snapshot.memory_content_sha256 == identity.memory_content_sha256
    )
    if not content_equal or not identity_equal:
        return "conflict"
    if (
        snapshot.created_by_training_batch_id == training_batch_id
        and snapshot.created_by_batch_content_sha256 == batch_content_sha256
        and snapshot.created_from_sample_id == sample_id
    ):
        return "resume_same_batch"
    return "preexisting_other_batch"


def _plan_summary(
    *,
    batch_content_sha256: str,
    training_batch_id: str,
    expected_new_memory_count: int,
    items: list[MemoryWritePlanItem],
    counts: Mapping[str, int],
    executable: bool,
    issues: list[WritePlanIssue],
) -> dict[str, Any]:
    return {
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "batch_content_sha256": batch_content_sha256,
        "training_batch_id": training_batch_id,
        "expected_new_memory_count": expected_new_memory_count,
        "items": [
            {
                "record_id": item.record_id,
                "delivery_item_sha256": item.delivery_item_sha256,
                "sample_id": item.sample_id,
                "memory_content_sha256": item.memory_content_sha256,
                "status": item.status,
            }
            for item in items
        ],
        "create_count": counts["create"],
        "resume_same_batch_count": counts["resume_same_batch"],
        "preexisting_other_batch_count": counts["preexisting_other_batch"],
        "conflict_count": counts["conflict"],
        "executable": executable,
        "issues": [asdict(issue) for issue in issues],
    }


def build_memory_write_plan(
    batch_data: Any,
    *,
    approved_batch_content_sha256: str,
    existing_records: Mapping[str, ExistingRecordSnapshot] | None = None,
    sql_guard: Any | None = None,
) -> MemoryWritePlan:
    """校验批次并生成不访问任何 Memory 存储的确定性写入计划。"""

    validation = validate_training_batch(batch_data, sql_guard=sql_guard)
    if not validation.valid or validation.summary is None:
        codes = ",".join(issue.code for issue in validation.errors)
        raise MemoryWritePlanError("BATCH_INVALID", codes or "批次校验失败")
    if not validation.batch_content_sha256:
        raise MemoryWritePlanError("BATCH_DIGEST_MISSING", "批次摘要不存在")
    if validation.batch_content_sha256 != approved_batch_content_sha256:
        raise MemoryWritePlanError(
            "BATCH_DIGEST_MISMATCH", "批准摘要与实际批次摘要不一致"
        )

    summary = validation.summary
    batch_content_sha256 = validation.batch_content_sha256
    training_batch_id = summary["training_batch_id"]
    expected_new_memory_count = summary["expected_new_memory_count"]
    snapshots = existing_records or {}
    items: list[MemoryWritePlanItem] = []
    record_to_sample_ids: dict[str, list[str]] = {}

    for sample in summary["samples"]:
        identity = build_memory_identity_from_canonical_content(
            _canonical_content(sample)
        )
        sample_id = sample["sample_id"]
        record_to_sample_ids.setdefault(identity.record_id, []).append(sample_id)
        snapshot = snapshots.get(identity.record_id)
        status: PlanStatus = "create"
        if snapshot is not None:
            if not isinstance(snapshot, ExistingRecordSnapshot):
                raise MemoryWritePlanError(
                    "INVALID_EXISTING_RECORD_SNAPSHOT",
                    f"{identity.record_id} 的快照类型无效",
                )
            status = _snapshot_status(
                snapshot_key=identity.record_id,
                snapshot=snapshot,
                identity=identity,
                training_batch_id=training_batch_id,
                batch_content_sha256=batch_content_sha256,
                sample_id=sample_id,
            )
        items.append(
            MemoryWritePlanItem(
                sample_id=sample_id,
                record_id=identity.record_id,
                delivery_item_sha256=_delivery_item_sha256(
                    batch_content_sha256, sample_id, identity.record_id
                ),
                memory_content_sha256=identity.memory_content_sha256,
                status=status,
                canonical_content=identity.canonical_content,
                governance_metadata=_governance_metadata(
                    batch_content_sha256=batch_content_sha256,
                    training_batch_id=training_batch_id,
                    sample=sample,
                    memory_content_sha256=identity.memory_content_sha256,
                ),
                compatibility_metadata=_compatibility_metadata(
                    batch_content_sha256=batch_content_sha256,
                    training_batch_id=training_batch_id,
                    sample=sample,
                    memory_content_sha256=identity.memory_content_sha256,
                ),
            )
        )

    issues: list[WritePlanIssue] = []
    for record_id, sample_ids in record_to_sample_ids.items():
        if len(sample_ids) > 1:
            issues.append(
                WritePlanIssue(
                    code="DUPLICATE_MEMORY_CONTENT_IN_BATCH",
                    sample_id=",".join(sample_ids),
                    record_id=record_id,
                    message="批次内多个 sample_id 映射到相同 Tool Memory 内容",
                )
            )

    for item in items:
        if item.status == "preexisting_other_batch":
            issues.append(
                WritePlanIssue(
                    code="PREEXISTING_OTHER_BATCH",
                    sample_id=item.sample_id,
                    record_id=item.record_id,
                    message="相同内容已由其他批次创建",
                )
            )
        elif item.status == "conflict":
            issues.append(
                WritePlanIssue(
                    code="CONTENT_ADDRESS_CONFLICT",
                    sample_id=item.sample_id,
                    record_id=item.record_id,
                    message="既有记录与内容寻址身份不一致",
                )
            )

    counts = {
        status: sum(item.status == status for item in items)
        for status in (
            "create",
            "resume_same_batch",
            "preexisting_other_batch",
            "conflict",
        )
    }
    if counts["resume_same_batch"] == 0:
        if counts["create"] != expected_new_memory_count:
            issues.append(
                WritePlanIssue(
                    code="FIRST_EXECUTION_COUNT_MISMATCH",
                    sample_id="-",
                    record_id="-",
                    message="首次执行 create_count 与 expected_new_memory_count 不一致",
                )
            )
    elif (
        counts["create"] + counts["resume_same_batch"]
        != expected_new_memory_count
    ):
        issues.append(
            WritePlanIssue(
                code="RESUME_COUNT_MISMATCH",
                sample_id="-",
                record_id="-",
                message="恢复执行 create_count + resume_same_batch_count 与批准总数不一致",
            )
        )

    executable = not issues
    plan_summary = _plan_summary(
        batch_content_sha256=batch_content_sha256,
        training_batch_id=training_batch_id,
        expected_new_memory_count=expected_new_memory_count,
        items=items,
        counts=counts,
        executable=executable,
        issues=issues,
    )
    return MemoryWritePlan(
        plan_schema_version=PLAN_SCHEMA_VERSION,
        batch_content_sha256=batch_content_sha256,
        training_batch_id=training_batch_id,
        expected_new_memory_count=expected_new_memory_count,
        items=tuple(items),
        create_count=counts["create"],
        resume_same_batch_count=counts["resume_same_batch"],
        preexisting_other_batch_count=counts["preexisting_other_batch"],
        conflict_count=counts["conflict"],
        executable=executable,
        issues=tuple(issues),
        write_plan_sha256=_sha256(_canonical_json(plan_summary)),
    )
