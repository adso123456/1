"""根据期望 DDL Memory 与只读快照生成确定性计划；不执行任何写入。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from training.sop.ddl_memory_identity import (
    DdlMemoryIdentity,
    DdlMemoryIdentityInput,
    EFFECTIVE_METADATA_FIELDS,
    IDENTITY_VERSION,
    MEMORY_TYPE,
    build_ddl_memory_identity,
)


PLAN_VERSION = "ddl-memory-plan-v1"
PLAN_ACTIONS = frozenset({"create", "unchanged", "changed", "removed"})
RECORD_ID_PATTERN = re.compile(r"ddlmem-v1-[0-9a-f]{64}\Z")
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
STORAGE_METADATA_FIELDS = EFFECTIVE_METADATA_FIELDS | {"content_fingerprint"}


@dataclass(frozen=True)
class ExistingDdlMemoryRecord:
    record_id: str
    document: str
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id:
            raise ValueError("existing record_id 必须是非空字符串")
        if not isinstance(self.document, str):
            raise ValueError("existing document 必须是字符串")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("existing metadata 必须是 Mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class DdlMemoryPlanAction:
    action: str
    record_id: str
    logical_id: str
    source_id: str
    schema_name: str
    object_type: str
    object_name: str
    desired_content_fingerprint: str | None
    expected_existing_content_fingerprint: str | None
    normalized_ddl: str | None = None
    desired_metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.action not in PLAN_ACTIONS:
            raise ValueError(f"非法 Plan action：{self.action}")
        if self.desired_metadata is not None:
            object.__setattr__(
                self, "desired_metadata", MappingProxyType(dict(self.desired_metadata))
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "record_id": self.record_id,
            "logical_id": self.logical_id,
            "source_id": self.source_id,
            "schema_name": self.schema_name,
            "object_type": self.object_type,
            "object_name": self.object_name,
            "desired_content_fingerprint": self.desired_content_fingerprint,
            "expected_existing_content_fingerprint": (
                self.expected_existing_content_fingerprint
            ),
            "normalized_ddl": self.normalized_ddl,
            "desired_metadata": (
                dict(self.desired_metadata) if self.desired_metadata is not None else None
            ),
        }


@dataclass(frozen=True)
class DdlMemoryPlan:
    plan_version: str
    desired_count: int
    managed_existing_count: int
    unmanaged_existing_count: int
    create_count: int
    unchanged_count: int
    changed_count: int
    removed_count: int
    actions: tuple[DdlMemoryPlanAction, ...]
    plan_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "desired_count": self.desired_count,
            "managed_existing_count": self.managed_existing_count,
            "unmanaged_existing_count": self.unmanaged_existing_count,
            "create_count": self.create_count,
            "unchanged_count": self.unchanged_count,
            "changed_count": self.changed_count,
            "removed_count": self.removed_count,
            "actions": [action.to_dict() for action in self.actions],
            "plan_sha256": self.plan_sha256,
        }


@dataclass(frozen=True)
class _ManagedExisting:
    snapshot: ExistingDdlMemoryRecord
    identity: DdlMemoryIdentityInput
    logical_id: str
    content_fingerprint: str


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_desired(desired: DdlMemoryIdentity) -> DdlMemoryIdentity:
    if not isinstance(desired, DdlMemoryIdentity):
        raise ValueError("desired 记录必须是 DdlMemoryIdentity")
    metadata = dict(desired.effective_metadata)
    if set(metadata) != EFFECTIVE_METADATA_FIELDS:
        raise ValueError(f"期望记录 {desired.record_id} 的有效 Metadata 字段不完整")
    try:
        identity_input = DdlMemoryIdentityInput(
            source_id=metadata["source_id"],
            schema_name=metadata["schema_name"],
            object_type=metadata["object_type"],
            object_name=metadata["object_name"],
        )
        rebuilt = build_ddl_memory_identity(identity_input, desired.normalized_ddl)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"期望记录 {desired.record_id} 身份无效：{exc}") from exc
    if desired != rebuilt:
        raise ValueError(f"期望记录 {desired.record_id} 与确定性身份模块重建结果不一致")
    return rebuilt


def _parse_managed_existing(
    record: ExistingDdlMemoryRecord,
) -> _ManagedExisting | None:
    metadata = record.metadata
    claims_v1 = metadata.get("identity_version") == IDENTITY_VERSION
    is_managed_marker = (
        metadata.get("memory_type") == MEMORY_TYPE and claims_v1
    )
    if not is_managed_marker:
        if claims_v1:
            raise ValueError(
                f"记录 {record.record_id} 声称是 {IDENTITY_VERSION}，但 memory_type 非法或缺失"
            )
        return None

    missing = sorted(STORAGE_METADATA_FIELDS - set(metadata))
    if missing:
        raise ValueError(f"受控记录 {record.record_id} 缺少 Metadata：{missing}")
    if RECORD_ID_PATTERN.fullmatch(record.record_id) is None:
        raise ValueError(f"受控记录 ID 格式非法：{record.record_id}")
    if metadata["record_id"] != record.record_id:
        raise ValueError(f"受控记录 {record.record_id} 与 Metadata record_id 不一致")
    fingerprint = metadata["content_fingerprint"]
    if not isinstance(fingerprint, str) or FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise ValueError(f"受控记录 {record.record_id} 的 content_fingerprint 格式非法")

    try:
        identity_input = DdlMemoryIdentityInput(
            source_id=metadata["source_id"],
            schema_name=metadata["schema_name"],
            object_type=metadata["object_type"],
            object_name=metadata["object_name"],
        )
        rebuilt = build_ddl_memory_identity(identity_input, record.document)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"受控记录 {record.record_id} 身份或 document 无效：{exc}") from exc

    logical_id = metadata["logical_id"]
    if logical_id != rebuilt.logical_id:
        raise ValueError(f"受控记录 {record.record_id} 的 logical_id 与身份字段不匹配")
    if record.record_id != rebuilt.record_id:
        raise ValueError(f"受控记录 {record.record_id} 与 Metadata logical_id 不匹配")
    identity_metadata = {key: metadata[key] for key in EFFECTIVE_METADATA_FIELDS}
    if identity_metadata != dict(rebuilt.effective_metadata):
        raise ValueError(f"受控记录 {record.record_id} 的身份 Metadata 不一致")
    if fingerprint != rebuilt.content_fingerprint:
        raise ValueError(
            f"受控记录 {record.record_id} 的 content_fingerprint 与 document 不一致"
        )

    return _ManagedExisting(
        snapshot=record,
        identity=identity_input,
        logical_id=rebuilt.logical_id,
        content_fingerprint=fingerprint,
    )


def _desired_storage_metadata(desired: DdlMemoryIdentity) -> Mapping[str, str]:
    metadata = dict(desired.effective_metadata)
    metadata["content_fingerprint"] = desired.content_fingerprint
    return MappingProxyType(metadata)


def _action_from_desired(
    action: str,
    desired: DdlMemoryIdentity,
    existing_fingerprint: str | None,
) -> DdlMemoryPlanAction:
    carry_target = action in {"create", "changed"}
    metadata = desired.effective_metadata
    return DdlMemoryPlanAction(
        action=action,
        record_id=desired.record_id,
        logical_id=desired.logical_id,
        source_id=metadata["source_id"],
        schema_name=metadata["schema_name"],
        object_type=metadata["object_type"],
        object_name=metadata["object_name"],
        desired_content_fingerprint=desired.content_fingerprint,
        expected_existing_content_fingerprint=existing_fingerprint,
        normalized_ddl=desired.normalized_ddl if carry_target else None,
        desired_metadata=_desired_storage_metadata(desired) if carry_target else None,
    )


def _removed_action(existing: _ManagedExisting) -> DdlMemoryPlanAction:
    identity = existing.identity
    return DdlMemoryPlanAction(
        action="removed",
        record_id=existing.snapshot.record_id,
        logical_id=existing.logical_id,
        source_id=identity.source_id,
        schema_name=identity.schema_name,
        object_type=identity.object_type,
        object_name=identity.object_name,
        desired_content_fingerprint=None,
        expected_existing_content_fingerprint=existing.content_fingerprint,
        normalized_ddl=None,
        desired_metadata=None,
    )


def _plan_payload(
    *,
    desired_count: int,
    managed_existing_count: int,
    unmanaged_existing_count: int,
    actions: tuple[DdlMemoryPlanAction, ...],
) -> dict[str, Any]:
    counts = {action: 0 for action in sorted(PLAN_ACTIONS)}
    for item in actions:
        counts[item.action] += 1
    return {
        "plan_version": PLAN_VERSION,
        "desired_count": desired_count,
        "managed_existing_count": managed_existing_count,
        "unmanaged_existing_count": unmanaged_existing_count,
        "create_count": counts["create"],
        "unchanged_count": counts["unchanged"],
        "changed_count": counts["changed"],
        "removed_count": counts["removed"],
        "actions": [action.to_dict() for action in actions],
    }


def build_ddl_memory_plan(
    desired_memories: Sequence[DdlMemoryIdentity],
    existing_records: Sequence[ExistingDdlMemoryRecord],
) -> DdlMemoryPlan:
    desired_by_id: dict[str, DdlMemoryIdentity] = {}
    desired_identity_keys: set[str] = set()
    for raw_desired in desired_memories:
        desired = _validate_desired(raw_desired)
        if desired.record_id in desired_by_id:
            raise ValueError(f"期望集合重复 record_id：{desired.record_id}")
        if desired.identity_key in desired_identity_keys:
            raise ValueError(f"期望集合重复逻辑对象：{desired.identity_key}")
        desired_by_id[desired.record_id] = desired
        desired_identity_keys.add(desired.identity_key)

    managed_by_id: dict[str, _ManagedExisting] = {}
    unmanaged_count = 0
    for record in existing_records:
        if not isinstance(record, ExistingDdlMemoryRecord):
            raise ValueError("existing 记录必须是 ExistingDdlMemoryRecord")
        managed = _parse_managed_existing(record)
        if managed is None:
            unmanaged_count += 1
            continue
        if record.record_id in managed_by_id:
            raise ValueError(f"现有受控记录重复 record_id：{record.record_id}")
        managed_by_id[record.record_id] = managed

    actions: list[DdlMemoryPlanAction] = []
    for record_id, desired in desired_by_id.items():
        existing = managed_by_id.get(record_id)
        if existing is None:
            actions.append(_action_from_desired("create", desired, None))
            continue
        desired_metadata = dict(desired.effective_metadata)
        existing_identity_metadata = {
            key: existing.snapshot.metadata[key] for key in EFFECTIVE_METADATA_FIELDS
        }
        if existing_identity_metadata != desired_metadata:
            raise ValueError(f"相同 record_id {record_id} 的身份 Metadata 与期望不一致")
        unchanged = (
            existing.snapshot.document == desired.normalized_ddl
            and existing.content_fingerprint == desired.content_fingerprint
        )
        actions.append(
            _action_from_desired(
                "unchanged" if unchanged else "changed",
                desired,
                existing.content_fingerprint,
            )
        )

    for record_id, existing in managed_by_id.items():
        if record_id not in desired_by_id:
            actions.append(_removed_action(existing))

    sorted_actions = tuple(sorted(actions, key=lambda item: item.record_id))
    payload = _plan_payload(
        desired_count=len(desired_by_id),
        managed_existing_count=len(managed_by_id),
        unmanaged_existing_count=unmanaged_count,
        actions=sorted_actions,
    )
    plan_sha256 = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return DdlMemoryPlan(
        plan_version=PLAN_VERSION,
        desired_count=payload["desired_count"],
        managed_existing_count=payload["managed_existing_count"],
        unmanaged_existing_count=payload["unmanaged_existing_count"],
        create_count=payload["create_count"],
        unchanged_count=payload["unchanged_count"],
        changed_count=payload["changed_count"],
        removed_count=payload["removed_count"],
        actions=sorted_actions,
        plan_sha256=plan_sha256,
    )


def _desired(name: str, column_type: str = "integer") -> DdlMemoryIdentity:
    identity = DdlMemoryIdentityInput("postgres_water", "public", "table", name)
    return build_ddl_memory_identity(
        identity, f'CREATE TABLE "{name}" (\n  id {column_type}\n);'
    )


def _existing(
    desired: DdlMemoryIdentity,
    *,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> ExistingDdlMemoryRecord:
    metadata = dict(desired.effective_metadata)
    metadata["content_fingerprint"] = desired.content_fingerprint
    metadata.update(runtime_metadata or {})
    return ExistingDdlMemoryRecord(desired.record_id, desired.normalized_ddl, metadata)


def _expect_value_error(callable_object: Any, expected_text: str) -> None:
    try:
        callable_object()
    except ValueError as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常信息未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期 ValueError：{expected_text}")


def self_test() -> int:
    alpha = _desired("alpha")
    beta_old = _desired("beta", "integer")
    beta_new = _desired("beta", "bigint")
    gamma = _desired("gamma")
    delta = _desired("delta")

    all_create = build_ddl_memory_plan([alpha, gamma], [])
    assert all_create.create_count == 2
    assert all(action.action == "create" for action in all_create.actions)

    unchanged = build_ddl_memory_plan([alpha], [_existing(alpha)])
    assert unchanged.unchanged_count == 1
    assert unchanged.actions[0].desired_content_fingerprint == alpha.content_fingerprint
    assert unchanged.actions[0].expected_existing_content_fingerprint == alpha.content_fingerprint

    changed = build_ddl_memory_plan([beta_new], [_existing(beta_old)])
    assert changed.changed_count == 1
    assert changed.actions[0].desired_content_fingerprint == beta_new.content_fingerprint
    assert changed.actions[0].expected_existing_content_fingerprint == beta_old.content_fingerprint

    removed = build_ddl_memory_plan([], [_existing(delta)])
    assert removed.removed_count == 1
    assert removed.actions[0].normalized_ddl is None
    assert removed.actions[0].desired_metadata is None
    assert removed.actions[0].desired_content_fingerprint is None

    unmanaged = ExistingDdlMemoryRecord(
        "legacy-uuid", "legacy", {"is_text_memory": True, "timestamp": "runtime"}
    )
    unmanaged_plan = build_ddl_memory_plan([], [unmanaged])
    assert unmanaged_plan.unmanaged_existing_count == 1
    assert unmanaged_plan.removed_count == 0
    assert not unmanaged_plan.actions

    mixed = build_ddl_memory_plan(
        [gamma, beta_new, alpha],
        [_existing(delta), unmanaged, _existing(alpha), _existing(beta_old)],
    )
    assert (
        mixed.create_count,
        mixed.unchanged_count,
        mixed.changed_count,
        mixed.removed_count,
        mixed.unmanaged_existing_count,
    ) == (1, 1, 1, 1, 1)
    reordered = build_ddl_memory_plan(
        [alpha, beta_new, gamma],
        [_existing(beta_old), _existing(alpha), unmanaged, _existing(delta)],
    )
    assert mixed == reordered
    assert mixed.to_dict() == reordered.to_dict()
    assert [action.record_id for action in mixed.actions] == sorted(
        action.record_id for action in mixed.actions
    )

    runtime_one = build_ddl_memory_plan(
        [alpha], [_existing(alpha, runtime_metadata={"timestamp": "one"})]
    )
    runtime_two = build_ddl_memory_plan(
        [alpha],
        [_existing(alpha, runtime_metadata={"timestamp": "two", "request_id": "x"})],
    )
    assert runtime_one == runtime_two

    _expect_value_error(
        lambda: build_ddl_memory_plan([alpha, alpha], []), "重复 record_id"
    )
    _expect_value_error(
        lambda: build_ddl_memory_plan([], [_existing(alpha), _existing(alpha)]),
        "现有受控记录重复",
    )

    conflicting_metadata = dict(alpha.effective_metadata)
    conflicting_metadata["object_name"] = "other"
    conflicting_metadata["content_fingerprint"] = alpha.content_fingerprint
    _expect_value_error(
        lambda: build_ddl_memory_plan(
            [alpha],
            [
                ExistingDdlMemoryRecord(
                    alpha.record_id, alpha.normalized_ddl, conflicting_metadata
                )
            ],
        ),
        "不匹配",
    )

    missing_fingerprint = dict(alpha.effective_metadata)
    _expect_value_error(
        lambda: build_ddl_memory_plan(
            [], [ExistingDdlMemoryRecord(alpha.record_id, alpha.normalized_ddl, missing_fingerprint)]
        ),
        "缺少 Metadata",
    )
    invalid_fingerprint = dict(alpha.effective_metadata)
    invalid_fingerprint["content_fingerprint"] = "INVALID"
    _expect_value_error(
        lambda: build_ddl_memory_plan(
            [], [ExistingDdlMemoryRecord(alpha.record_id, alpha.normalized_ddl, invalid_fingerprint)]
        ),
        "content_fingerprint 格式非法",
    )
    inconsistent_fingerprint = dict(alpha.effective_metadata)
    inconsistent_fingerprint["content_fingerprint"] = "0" * 64
    _expect_value_error(
        lambda: build_ddl_memory_plan(
            [],
            [
                ExistingDdlMemoryRecord(
                    alpha.record_id, alpha.normalized_ddl, inconsistent_fingerprint
                )
            ],
        ),
        "与 document 不一致",
    )

    invalid_v1_id = dict(alpha.effective_metadata)
    invalid_v1_id["record_id"] = "bad-id"
    invalid_v1_id["content_fingerprint"] = alpha.content_fingerprint
    _expect_value_error(
        lambda: build_ddl_memory_plan(
            [], [ExistingDdlMemoryRecord("bad-id", alpha.normalized_ddl, invalid_v1_id)]
        ),
        "ID 格式非法",
    )

    other_version = ExistingDdlMemoryRecord(
        "other", "other", {"memory_type": MEMORY_TYPE, "identity_version": "ddlmem-v2"}
    )
    assert build_ddl_memory_plan([], [other_version]).unmanaged_existing_count == 1

    assert re.fullmatch(r"[0-9a-f]{64}", mixed.plan_sha256)
    forbidden_modules = ("chromadb", "vanna", "agent_config")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )

    print("DDL_MEMORY_PLAN_SELF_TEST=PASS")
    print("PLAN_ACTION_COVERAGE=create,unchanged,changed,removed")
    print("PLAN_DETERMINISM_TEST=PASS")
    print("PLAN_CONFLICT_GATE_TEST=PASS")
    print("PURE_MODULE_IMPORT_TEST=PASS")
    return 0


def main() -> int:
    if sys.argv[1:] != ["--self-test"]:
        print("用法：python -m training.sop.ddl_memory_plan --self-test", file=sys.stderr)
        return 2
    return self_test()


if __name__ == "__main__":
    raise SystemExit(main())
