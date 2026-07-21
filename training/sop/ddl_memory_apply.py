"""仅面向仓库外隔离 Chroma 的 DDL Memory 受控 Apply 编排器。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from training.sop.ddl_memory_adapter import (
    BACKUP_ROOT,
    COLLECTION_NAME,
    FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT,
    DdlMemoryChromaAdapter,
    open_isolated_adapter,
    validate_isolated_chroma_path,
)
from training.sop.ddl_memory_identity import (
    DdlMemoryIdentity,
    DdlMemoryIdentityInput,
    build_ddl_memory_identity,
)
from training.sop.ddl_memory_plan import (
    PLAN_VERSION,
    DdlMemoryPlan,
    ExistingDdlMemoryRecord,
    build_ddl_memory_plan,
)


APPLY_VERSION = "ddl-memory-apply-v1"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
INTEGRATION_ROOT_PATTERN = re.compile(r"f6-1e-\d{8}-\d{6}\Z")


@dataclass(frozen=True)
class DdlMemoryApplyOutcome:
    action: str
    record_id: str
    outcome: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "record_id": self.record_id,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DdlMemoryApplyResult:
    apply_version: str
    input_plan_sha256: str
    final_plan_sha256: str
    count_before: int
    count_after: int
    created_count: int
    verified_noop_count: int
    replaced_count: int
    retained_removed_count: int
    unmanaged_count_before: int
    unmanaged_count_after: int
    outcomes: tuple[DdlMemoryApplyOutcome, ...]
    apply_result_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "apply_version": self.apply_version,
            "input_plan_sha256": self.input_plan_sha256,
            "final_plan_sha256": self.final_plan_sha256,
            "count_before": self.count_before,
            "count_after": self.count_after,
            "created_count": self.created_count,
            "verified_noop_count": self.verified_noop_count,
            "replaced_count": self.replaced_count,
            "retained_removed_count": self.retained_removed_count,
            "unmanaged_count_before": self.unmanaged_count_before,
            "unmanaged_count_after": self.unmanaged_count_after,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "apply_result_sha256": self.apply_result_sha256,
        }


class DdlMemoryApplyPreconditionError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.writes_started = False
        self.candidate_usable = True
        self.completed_outcomes: tuple[DdlMemoryApplyOutcome, ...] = ()
        self.failed_record_id: str | None = None


class DdlMemoryApplyExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        writes_started: bool,
        completed_outcomes: Sequence[DdlMemoryApplyOutcome],
        failed_record_id: str,
    ) -> None:
        super().__init__(message)
        self.writes_started = writes_started
        self.candidate_usable = False
        self.completed_outcomes = tuple(completed_outcomes)
        self.failed_record_id = failed_record_id


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _record_map(
    records: Sequence[ExistingDdlMemoryRecord],
) -> dict[str, ExistingDdlMemoryRecord]:
    result: dict[str, ExistingDdlMemoryRecord] = {}
    for record in records:
        if record.record_id in result:
            raise ValueError(f"快照存在重复 record_id：{record.record_id}")
        result[record.record_id] = record
    return result


def _unmanaged_map(
    records: Sequence[ExistingDdlMemoryRecord], plan: DdlMemoryPlan
) -> dict[str, ExistingDdlMemoryRecord]:
    managed_existing_ids = {
        action.record_id for action in plan.actions if action.action != "create"
    }
    return {
        record.record_id: record
        for record in records
        if record.record_id not in managed_existing_ids
    }


def _validate_expected_plan(
    expected_plan: DdlMemoryPlan,
    desired_memories: Sequence[DdlMemoryIdentity],
    current_records: Sequence[ExistingDdlMemoryRecord],
) -> DdlMemoryPlan:
    if not isinstance(expected_plan, DdlMemoryPlan):
        raise DdlMemoryApplyPreconditionError("expected_plan 必须是 DdlMemoryPlan")
    if expected_plan.plan_version != PLAN_VERSION:
        raise DdlMemoryApplyPreconditionError(
            f"plan_version 不受支持：{expected_plan.plan_version}"
        )
    if SHA256_PATTERN.fullmatch(expected_plan.plan_sha256) is None:
        raise DdlMemoryApplyPreconditionError("plan_sha256 必须是 64 位小写 SHA-256")

    try:
        current_by_id = _record_map(current_records)
        rebuilt = build_ddl_memory_plan(desired_memories, current_records)
    except (TypeError, ValueError) as exc:
        raise DdlMemoryApplyPreconditionError(f"当前快照或期望集合无效：{exc}") from exc
    if rebuilt != expected_plan or rebuilt.to_dict() != expected_plan.to_dict():
        raise DdlMemoryApplyPreconditionError(
            "计划已过期或内容/SHA 不匹配；Apply 前重建计划与 expected_plan 不一致"
        )

    for action in expected_plan.actions:
        exists = action.record_id in current_by_id
        if action.action == "create" and exists:
            raise DdlMemoryApplyPreconditionError(
                f"create action 当前 record_id 已存在：{action.record_id}"
            )
        if action.action != "create" and not exists:
            raise DdlMemoryApplyPreconditionError(
                f"{action.action} action 当前 record_id 不存在：{action.record_id}"
            )
    return rebuilt


def _result_payload(
    *,
    input_plan_sha256: str,
    final_plan_sha256: str,
    count_before: int,
    count_after: int,
    outcomes: Sequence[DdlMemoryApplyOutcome],
    unmanaged_count_before: int,
    unmanaged_count_after: int,
) -> dict[str, Any]:
    return {
        "apply_version": APPLY_VERSION,
        "input_plan_sha256": input_plan_sha256,
        "final_plan_sha256": final_plan_sha256,
        "count_before": count_before,
        "count_after": count_after,
        "created_count": sum(item.outcome == "created" for item in outcomes),
        "verified_noop_count": sum(
            item.outcome == "verified_noop" for item in outcomes
        ),
        "replaced_count": sum(item.outcome == "replaced" for item in outcomes),
        "retained_removed_count": sum(
            item.outcome == "retained" for item in outcomes
        ),
        "unmanaged_count_before": unmanaged_count_before,
        "unmanaged_count_after": unmanaged_count_after,
        "outcomes": [item.to_dict() for item in outcomes],
    }


def apply_ddl_memory_plan(
    adapter: DdlMemoryChromaAdapter,
    desired_memories: Sequence[DdlMemoryIdentity],
    expected_plan: DdlMemoryPlan,
) -> DdlMemoryApplyResult:
    """重验并执行已经审阅的确定性计划；removed 永远只保留。"""
    if not isinstance(adapter, DdlMemoryChromaAdapter):
        raise DdlMemoryApplyPreconditionError(
            "adapter 必须由调用方显式传入 DdlMemoryChromaAdapter"
        )
    desired = tuple(desired_memories)
    before_records = adapter.snapshot_records()
    rebuilt = _validate_expected_plan(expected_plan, desired, before_records)
    before_by_id = _record_map(before_records)
    unmanaged_before = _unmanaged_map(before_records, rebuilt)
    removed_before = {
        action.record_id: before_by_id[action.record_id]
        for action in rebuilt.actions
        if action.action == "removed"
    }

    outcomes: list[DdlMemoryApplyOutcome] = []
    writes_started = False
    for action in rebuilt.actions:
        action_count_before = len(adapter.snapshot_records())
        try:
            if action.action == "create":
                writes_started = True
                adapter.create_from_action(action)
                outcome = DdlMemoryApplyOutcome(
                    action="create", record_id=action.record_id, outcome="created"
                )
                expected_delta = 1
            elif action.action == "changed":
                writes_started = True
                adapter.replace_from_action(action)
                outcome = DdlMemoryApplyOutcome(
                    action="changed", record_id=action.record_id, outcome="replaced"
                )
                expected_delta = 0
            elif action.action == "unchanged":
                outcome = DdlMemoryApplyOutcome(
                    action="unchanged",
                    record_id=action.record_id,
                    outcome="verified_noop",
                )
                expected_delta = 0
            elif action.action == "removed":
                outcome = DdlMemoryApplyOutcome(
                    action="removed",
                    record_id=action.record_id,
                    outcome="retained",
                    reason="removal_deferred",
                )
                expected_delta = 0
            else:  # Plan 模块本身已限制，此处保留防御性失败。
                raise ValueError(f"不支持的 action：{action.action}")

            action_count_after = len(adapter.snapshot_records())
            if action_count_after - action_count_before != expected_delta:
                raise RuntimeError(
                    f"{action.action} 记录数增量异常："
                    f"before={action_count_before}, after={action_count_after}, "
                    f"expected_delta={expected_delta}"
                )
            outcomes.append(outcome)
        except Exception as exc:
            raise DdlMemoryApplyExecutionError(
                f"Apply 执行失败，候选库不可验收：{type(exc).__name__}: {exc}",
                writes_started=writes_started,
                completed_outcomes=outcomes,
                failed_record_id=action.record_id,
            ) from exc

    try:
        after_records = adapter.snapshot_records()
        final_plan = build_ddl_memory_plan(desired, after_records)
        after_by_id = _record_map(after_records)
        unmanaged_after = _unmanaged_map(after_records, final_plan)
        removed_after = {
            record_id: after_by_id.get(record_id) for record_id in removed_before
        }
        expected_final_counts = (
            0,
            len(desired),
            0,
            rebuilt.removed_count,
        )
        actual_final_counts = (
            final_plan.create_count,
            final_plan.unchanged_count,
            final_plan.changed_count,
            final_plan.removed_count,
        )
        if actual_final_counts != expected_final_counts:
            raise RuntimeError(
                "Apply 后 Plan 未收敛："
                f"actual={actual_final_counts}, expected={expected_final_counts}"
            )
        if unmanaged_after != unmanaged_before:
            raise RuntimeError("Apply 后 unmanaged 记录的 ID、内容或 Metadata 发生变化")
        if removed_after != removed_before:
            raise RuntimeError("Apply 后 retained removed 记录发生变化")
        count_before = len(before_records)
        count_after = len(after_records)
        if count_after - count_before != rebuilt.create_count:
            raise RuntimeError(
                "Apply 后总记录增量不等于 create 数："
                f"before={count_before}, after={count_after}, "
                f"create={rebuilt.create_count}"
            )

        payload = _result_payload(
            input_plan_sha256=rebuilt.plan_sha256,
            final_plan_sha256=final_plan.plan_sha256,
            count_before=count_before,
            count_after=count_after,
            outcomes=outcomes,
            unmanaged_count_before=len(unmanaged_before),
            unmanaged_count_after=len(unmanaged_after),
        )
        result_sha256 = hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()
        return DdlMemoryApplyResult(
            apply_version=payload["apply_version"],
            input_plan_sha256=payload["input_plan_sha256"],
            final_plan_sha256=payload["final_plan_sha256"],
            count_before=payload["count_before"],
            count_after=payload["count_after"],
            created_count=payload["created_count"],
            verified_noop_count=payload["verified_noop_count"],
            replaced_count=payload["replaced_count"],
            retained_removed_count=payload["retained_removed_count"],
            unmanaged_count_before=payload["unmanaged_count_before"],
            unmanaged_count_after=payload["unmanaged_count_after"],
            outcomes=tuple(outcomes),
            apply_result_sha256=result_sha256,
        )
    except Exception as exc:
        if isinstance(exc, DdlMemoryApplyExecutionError):
            raise
        raise DdlMemoryApplyExecutionError(
            f"Apply 写后验收失败，候选库不可验收：{type(exc).__name__}: {exc}",
            writes_started=writes_started,
            completed_outcomes=outcomes,
            failed_record_id="__post_validation__",
        ) from exc


class _FakeCollection:
    def __init__(self) -> None:
        self.records: dict[str, tuple[str, dict[str, Any]]] = {}
        self.write_call_count = 0

    def count(self) -> int:
        return len(self.records)

    def get(
        self,
        ids: Sequence[str] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, list[Any]]:
        selected = (
            [record_id for record_id in ids if record_id in self.records]
            if ids is not None
            else list(reversed(self.records))
        )
        return {
            "ids": selected,
            "documents": [self.records[item][0] for item in selected],
            "metadatas": [dict(self.records[item][1]) for item in selected],
        }

    def add(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        self.write_call_count += 1
        for record_id in ids:
            if record_id in self.records:
                raise ValueError(f"duplicate id: {record_id}")
        for record_id, document, metadata in zip(ids, documents, metadatas):
            self.records[record_id] = (document, dict(metadata))

    def update(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        self.write_call_count += 1
        for record_id in ids:
            if record_id not in self.records:
                raise ValueError(f"missing id: {record_id}")
        for record_id, document, metadata in zip(ids, documents, metadatas):
            self.records[record_id] = (document, dict(metadata))


def _identity(name: str, column_type: str = "integer") -> DdlMemoryIdentity:
    return build_ddl_memory_identity(
        DdlMemoryIdentityInput("postgres_water", "public", "table", name),
        f'CREATE TABLE "{name}" (\n  id {column_type}\n);',
    )


def _storage_metadata(identity: DdlMemoryIdentity) -> dict[str, str]:
    metadata = dict(identity.effective_metadata)
    metadata["content_fingerprint"] = identity.content_fingerprint
    return metadata


def _seed_identity(collection: Any, identity: DdlMemoryIdentity) -> None:
    collection.add(
        ids=[identity.record_id],
        documents=[identity.normalized_ddl],
        metadatas=[_storage_metadata(identity)],
    )


def _build_fixture() -> tuple[
    DdlMemoryChromaAdapter,
    _FakeCollection,
    tuple[DdlMemoryIdentity, ...],
    DdlMemoryPlan,
]:
    unchanged = _identity("apply_unchanged")
    changed_old = _identity("apply_changed", "integer")
    changed_new = _identity("apply_changed", "bigint")
    removed = _identity("apply_removed")
    created = _identity("apply_created")
    collection = _FakeCollection()
    collection.add(
        ids=["f6-1e-unmanaged-test"],
        documents=["UNMANAGED TEST RECORD"],
        metadatas=[{"kind": "legacy-test", "is_text_memory": True}],
    )
    _seed_identity(collection, unchanged)
    _seed_identity(collection, changed_old)
    _seed_identity(collection, removed)
    collection.write_call_count = 0
    adapter = DdlMemoryChromaAdapter(collection)
    desired = (unchanged, changed_new, created)
    plan = build_ddl_memory_plan(desired, adapter.snapshot_records())
    return adapter, collection, desired, plan


def _expect_precondition_error(callable_object: Any, expected_text: str) -> None:
    try:
        callable_object()
    except DdlMemoryApplyPreconditionError as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常未包含 {expected_text!r}：{exc}") from exc
        assert exc.writes_started is False
        assert exc.completed_outcomes == ()
    else:
        raise AssertionError(f"预期 Apply 前置条件失败：{expected_text}")


def self_test() -> int:
    adapter, collection, desired, plan = _build_fixture()
    assert (
        plan.create_count,
        plan.unchanged_count,
        plan.changed_count,
        plan.removed_count,
    ) == (1, 1, 1, 1)
    result = apply_ddl_memory_plan(adapter, desired, plan)
    assert result.apply_version == APPLY_VERSION
    assert (result.count_before, result.count_after) == (4, 5)
    assert (
        result.created_count,
        result.verified_noop_count,
        result.replaced_count,
        result.retained_removed_count,
    ) == (1, 1, 1, 1)
    assert [item.record_id for item in result.outcomes] == [
        action.record_id for action in plan.actions
    ]
    assert [item.outcome for item in result.outcomes].count("created") == 1
    assert [item.outcome for item in result.outcomes].count("verified_noop") == 1
    assert [item.outcome for item in result.outcomes].count("replaced") == 1
    assert [item.outcome for item in result.outcomes].count("retained") == 1
    final_plan = build_ddl_memory_plan(desired, adapter.snapshot_records())
    assert (
        final_plan.create_count,
        final_plan.unchanged_count,
        final_plan.changed_count,
        final_plan.removed_count,
    ) == (0, 3, 0, 1)
    assert result.unmanaged_count_before == result.unmanaged_count_after == 1
    assert collection.write_call_count == 2

    writes_before_stale = collection.write_call_count
    _expect_precondition_error(
        lambda: apply_ddl_memory_plan(adapter, desired, plan), "计划已过期"
    )
    assert collection.write_call_count == writes_before_stale

    sha_adapter, sha_collection, sha_desired, sha_plan = _build_fixture()
    bad_sha_plan = replace(sha_plan, plan_sha256="0" * 64)
    _expect_precondition_error(
        lambda: apply_ddl_memory_plan(sha_adapter, sha_desired, bad_sha_plan),
        "内容/SHA 不匹配",
    )
    assert sha_collection.write_call_count == 0

    stale_adapter, stale_collection, stale_desired, stale_plan = _build_fixture()
    stale_collection.records["late-unmanaged"] = ("late", {"kind": "legacy"})
    _expect_precondition_error(
        lambda: apply_ddl_memory_plan(stale_adapter, stale_desired, stale_plan),
        "计划已过期",
    )
    assert stale_collection.write_call_count == 0

    adapter_two, _, desired_two, plan_two = _build_fixture()
    deterministic_result = apply_ddl_memory_plan(adapter_two, desired_two, plan_two)
    assert deterministic_result == result
    assert SHA256_PATTERN.fullmatch(result.apply_result_sha256)
    assert not hasattr(DdlMemoryChromaAdapter, "delete")
    assert not hasattr(DdlMemoryChromaAdapter, "remove")
    forbidden_modules = ("chromadb", "vanna", "backend.memory")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )
    assert FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT == 0

    print("DDL_MEMORY_APPLY_SELF_TEST=PASS")
    print("PLAN_REVALIDATION_TEST=PASS")
    print("CREATE_OUTCOME=created")
    print("UNCHANGED_OUTCOME=verified_noop")
    print("CHANGED_OUTCOME=replaced")
    print("REMOVED_OUTCOME=retained:removal_deferred")
    print("STALE_PLAN_TEST=PASS:writes_started=false")
    print("UNMANAGED_PRESERVATION_TEST=PASS")
    print("REMOVED_PRESERVATION_TEST=PASS")
    print("COUNT_DELTA_TEST=PASS:create_only")
    print("DETERMINISTIC_RESULT_SHA_TEST=PASS")
    print("DELETE_CAPABILITY=NONE")
    print("CHROMA_CLIENT_CREATED=0")
    return 0


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_integration_paths(
    isolated_chroma: Path | str, evidence_dir: Path | str
) -> tuple[Path, Path]:
    isolated = validate_isolated_chroma_path(isolated_chroma, require_empty=True)
    evidence = Path(evidence_dir).expanduser().resolve()
    run_root = isolated.parent
    if (
        isolated.name != "isolated_chroma"
        or INTEGRATION_ROOT_PATTERN.fullmatch(run_root.name) is None
    ):
        raise ValueError(
            "隔离路径必须为 E:\\3\\_training_backups\\"
            "f6-1e-<YYYYMMDD-HHMMSS>\\isolated_chroma"
        )
    if evidence.name != "evidence" or evidence.parent != run_root:
        raise ValueError("Evidence 必须与 isolated_chroma 位于同一 F6-1E 运行目录")
    if not _is_within(evidence, BACKUP_ROOT):
        raise ValueError(f"Evidence 必须位于 {BACKUP_ROOT} 下")
    if evidence.exists() and any(evidence.iterdir()):
        raise ValueError(f"Evidence 必须全新或为空：{evidence}")
    return isolated, evidence


def _stable_record(record: ExistingDdlMemoryRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "document": record.document,
        "metadata": dict(record.metadata),
    }


def integration_test(
    isolated_chroma: Path | str, evidence_dir: Path | str
) -> dict[str, Any]:
    isolated, evidence = _validate_integration_paths(isolated_chroma, evidence_dir)
    evidence.mkdir(parents=True, exist_ok=False)
    adapter = open_isolated_adapter(isolated, require_empty=True)

    unchanged = _identity("integration_unchanged")
    changed_old = _identity("integration_changed", "integer")
    changed_new = _identity("integration_changed", "bigint")
    removed = _identity("integration_removed")
    created = _identity("integration_created")
    unmanaged_id = "f6-1e-unmanaged-test"
    adapter._collection.add(
        ids=[unmanaged_id],
        documents=["UNMANAGED TEST RECORD"],
        metadatas=[{"kind": "legacy-test", "is_text_memory": True}],
    )
    _seed_identity(adapter._collection, unchanged)
    _seed_identity(adapter._collection, changed_old)
    _seed_identity(adapter._collection, removed)
    desired = (unchanged, changed_new, created)
    initial_records = adapter.snapshot_records()
    initial_plan = build_ddl_memory_plan(desired, initial_records)
    initial_counts = (
        initial_plan.create_count,
        initial_plan.unchanged_count,
        initial_plan.changed_count,
        initial_plan.removed_count,
    )
    if len(initial_records) != 4 or initial_counts != (1, 1, 1, 1):
        raise RuntimeError(
            f"F6-1E 初始夹具不符合验收：count={len(initial_records)}, plan={initial_counts}"
        )
    initial_by_id = _record_map(initial_records)
    unmanaged_before = initial_by_id[unmanaged_id]
    removed_before = initial_by_id[removed.record_id]

    result = apply_ddl_memory_plan(adapter, desired, initial_plan)
    count_after_apply = len(adapter.snapshot_records())
    final_plan = build_ddl_memory_plan(desired, adapter.snapshot_records())
    final_counts = {
        "create": final_plan.create_count,
        "unchanged": final_plan.unchanged_count,
        "changed": final_plan.changed_count,
        "removed": final_plan.removed_count,
    }
    after_by_id = _record_map(adapter.snapshot_records())
    unmanaged_preserved = after_by_id[unmanaged_id] == unmanaged_before
    removed_preserved = after_by_id[removed.record_id] == removed_before
    changed_id_preserved = changed_old.record_id == changed_new.record_id

    stale_count_before = len(adapter.snapshot_records())
    stale_error: DdlMemoryApplyPreconditionError | None = None
    try:
        apply_ddl_memory_plan(adapter, desired, initial_plan)
    except DdlMemoryApplyPreconditionError as exc:
        stale_error = exc
    if stale_error is None or stale_error.writes_started:
        raise RuntimeError("复用旧计划未在零写入前置门禁失败")
    if len(adapter.snapshot_records()) != stale_count_before:
        raise RuntimeError("旧计划失败后记录数变化")

    second_result = apply_ddl_memory_plan(adapter, desired, final_plan)
    count_after_second = len(adapter.snapshot_records())
    if (
        second_result.created_count != 0
        or second_result.replaced_count != 0
        or second_result.verified_noop_count != 3
        or second_result.retained_removed_count != 1
        or count_after_second != count_after_apply
    ):
        raise RuntimeError("第二次新计划 Apply 未保持纯 noop/retained")

    del adapter
    gc.collect()
    reopened = open_isolated_adapter(isolated, require_empty=False)
    reopened_records = reopened.snapshot_records()
    reopened_plan = build_ddl_memory_plan(desired, reopened_records)
    reopened_by_id = _record_map(reopened_records)
    reopen_ok = (
        len(reopened_records) == 5
        and reopened_plan.create_count == 0
        and reopened_plan.unchanged_count == 3
        and reopened_plan.changed_count == 0
        and reopened_plan.removed_count == 1
        and reopened_by_id[unmanaged_id] == unmanaged_before
        and reopened_by_id[removed.record_id] == removed_before
    )
    if not (
        result.created_count == 1
        and result.verified_noop_count == 1
        and result.replaced_count == 1
        and result.retained_removed_count == 1
        and count_after_apply == 5
        and final_counts == {"create": 0, "unchanged": 3, "changed": 0, "removed": 1}
        and unmanaged_preserved
        and removed_preserved
        and changed_id_preserved
        and reopen_ok
    ):
        raise RuntimeError("F6-1E 隔离集成验收失败")

    summary = {
        "integration_test": "PASS",
        "apply_version": APPLY_VERSION,
        "collection_name": COLLECTION_NAME,
        "initial_plan_counts": {
            "create": 1,
            "unchanged": 1,
            "changed": 1,
            "removed": 1,
        },
        "count_initial": 4,
        "count_after_apply": count_after_apply,
        "final_plan_counts": final_counts,
        "outcomes": [item.to_dict() for item in result.outcomes],
        "input_plan_sha256": result.input_plan_sha256,
        "apply_result_sha256": result.apply_result_sha256,
        "stale_plan_test": "PASS:writes_started=false",
        "unmanaged_preserved": unmanaged_preserved,
        "unmanaged_record_sha256": hashlib.sha256(
            _canonical_json(_stable_record(unmanaged_before)).encode("utf-8")
        ).hexdigest(),
        "removed_preserved": removed_preserved,
        "removed_record_sha256": hashlib.sha256(
            _canonical_json(_stable_record(removed_before)).encode("utf-8")
        ).hexdigest(),
        "changed_record_id_preserved": changed_id_preserved,
        "second_apply_result": {
            "created": second_result.created_count,
            "verified_noop": second_result.verified_noop_count,
            "replaced": second_result.replaced_count,
            "retained": second_result.retained_removed_count,
            "count_after": count_after_second,
        },
        "reopen_persistence_test": "PASS",
        "delete_capability": "NONE",
        "formal_chroma_client_open_attempts_by_script": (
            FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT
        ),
        "isolated_chroma": str(isolated),
    }
    lines = [
        "COMMAND=python -m training.sop.ddl_memory_apply --integration-test",
        "EXIT_CODE=0",
        "DDL_MEMORY_APPLY_INTEGRATION_TEST=PASS",
        "COUNT_INITIAL=4",
        f"COUNT_AFTER_APPLY={count_after_apply}",
        "FINAL_PLAN_COUNTS=create:0,unchanged:3,changed:0,removed:1",
        "CREATE_OUTCOME=created",
        "UNCHANGED_OUTCOME=verified_noop",
        "CHANGED_OUTCOME=replaced",
        "REMOVED_OUTCOME=retained:removal_deferred",
        "STALE_PLAN_TEST=PASS:writes_started=false",
        "UNMANAGED_PRESERVATION_TEST=PASS",
        "REMOVED_PRESERVATION_TEST=PASS",
        "SECOND_APPLY_RESULT=verified_noop:3,retained:1,count_delta:0",
        "REOPEN_PERSISTENCE_TEST=PASS",
        "DELETE_CAPABILITY=NONE",
        "FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0",
    ]
    (evidence / "apply-integration-test.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (evidence / "apply-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--integration-test", action="store_true")
    parser.add_argument("--isolated-chroma", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        if args.isolated_chroma is not None or args.evidence_dir is not None:
            raise SystemExit("--self-test 不接受隔离路径参数")
        return self_test()
    if args.isolated_chroma is None or args.evidence_dir is None:
        raise SystemExit("--integration-test 必须显式传入隔离 Chroma 和 Evidence 路径")
    try:
        summary = integration_test(args.isolated_chroma, args.evidence_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        evidence = args.evidence_dir.resolve()
        if evidence.exists() and evidence.is_dir():
            (evidence / "apply-integration-test.txt").write_text(
                "COMMAND=python -m training.sop.ddl_memory_apply --integration-test\n"
                "EXIT_CODE=1\n"
                f"DDL_MEMORY_APPLY_INTEGRATION_TEST=FAIL\nERROR={type(exc).__name__}: {exc}\n",
                encoding="utf-8",
            )
        print(f"DDL_MEMORY_APPLY_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"count_initial={summary['count_initial']}")
    print(f"count_after_apply={summary['count_after_apply']}")
    print("final_plan_counts=create:0,unchanged:3,changed:0,removed:1")
    print("second_apply_result=verified_noop:3,retained:1,count_delta:0")
    print("formal_chroma_client_open_attempts_by_script=0")
    print("ddl_memory_apply_integration_test=PASS")
    print(f"evidence_dir={args.evidence_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
