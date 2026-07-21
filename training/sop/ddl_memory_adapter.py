"""仅面向仓库外隔离 Chroma 的 DDL Memory create/replace 适配层。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from training.sop.ddl_memory_identity import (
    DdlMemoryIdentity,
    DdlMemoryIdentityInput,
    build_ddl_memory_identity,
)
from training.sop.ddl_memory_plan import (
    DdlMemoryPlanAction,
    ExistingDdlMemoryRecord,
    build_ddl_memory_plan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = Path(r"E:\3\_training_backups").resolve()
FORMAL_CHROMA = Path(r"E:\3\_runtime\vanna-level1\vanna_data").resolve()
REPOSITORY_CHROMA = (PROJECT_ROOT / "vanna_data").resolve()
COLLECTION_NAME = "tool_memories"
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT = 0
INTEGRATION_ROOT_PATTERN = re.compile(r"f6-1d-\d{8}-\d{6}\Z")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_isolated_chroma_path(
    persist_directory: Path | str, *, require_empty: bool = True
) -> Path:
    if not isinstance(persist_directory, (Path, str)) or not str(persist_directory).strip():
        raise ValueError("persist_directory 必须由调用方显式传入")
    target = Path(persist_directory).expanduser().resolve()
    if not _is_within(target, BACKUP_ROOT):
        raise ValueError(f"隔离 Chroma 必须位于 {BACKUP_ROOT} 下")
    if _is_within(target, FORMAL_CHROMA):
        raise ValueError("隔离 Chroma 禁止位于正式 Chroma 内")
    if _is_within(target, REPOSITORY_CHROMA):
        raise ValueError("隔离 Chroma 禁止位于仓库 vanna_data 内")
    if _is_within(target, PROJECT_ROOT):
        raise ValueError("隔离 Chroma 必须位于项目仓库外")
    if require_empty and target.exists() and any(target.iterdir()):
        raise ValueError(f"隔离 Chroma 必须全新或为空：{target}")
    return target


def validate_evidence_path(evidence_dir: Path | str, isolated_chroma: Path) -> Path:
    target = Path(evidence_dir).expanduser().resolve()
    if target.name != "evidence" or INTEGRATION_ROOT_PATTERN.fullmatch(
        target.parent.name
    ) is None:
        raise ValueError("Evidence 路径必须为 f6-1d-<YYYYMMDD-HHMMSS>\\evidence")
    if target.parent != isolated_chroma.parent:
        raise ValueError("Evidence 与 isolated_chroma 必须属于同一个 F6-1D 运行目录")
    if not _is_within(target, BACKUP_ROOT):
        raise ValueError(f"Evidence 必须位于 {BACKUP_ROOT} 下")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Evidence 必须全新或为空：{target}")
    return target


class DdlMemoryChromaAdapter:
    """包装 collection 的显式 ID create/replace 原语；不实现删除或完整 Apply。"""

    def __init__(self, collection: Any, *, memory_owner: Any | None = None) -> None:
        self._collection = collection
        self._memory_owner = memory_owner

    def snapshot_records(self) -> tuple[ExistingDdlMemoryRecord, ...]:
        raw = self._collection.get(include=["documents", "metadatas"])
        ids = list(raw.get("ids") or [])
        documents = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        if not (len(ids) == len(documents) == len(metadatas)):
            raise ValueError(
                "collection 快照数组长度不一致："
                f"ids={len(ids)}, documents={len(documents)}, metadatas={len(metadatas)}"
            )
        records = [
            ExistingDdlMemoryRecord(
                record_id=record_id,
                document=document,
                metadata=metadata or {},
            )
            for record_id, document, metadata in zip(ids, documents, metadatas)
        ]
        return tuple(sorted(records, key=lambda item: item.record_id))

    def create_from_action(self, action: DdlMemoryPlanAction) -> ExistingDdlMemoryRecord:
        desired = self._validate_action(action, expected_action="create")
        if action.expected_existing_content_fingerprint is not None:
            raise ValueError("create action 的旧指纹必须为 None")
        before_count = self._collection.count()
        if self._get_exact(action.record_id) is not None:
            raise ValueError(f"create 冲突：record_id 已存在：{action.record_id}")

        self._collection.add(
            ids=[action.record_id],
            documents=[desired.normalized_ddl],
            metadatas=[dict(action.desired_metadata or {})],
        )
        after_count = self._collection.count()
        if after_count != before_count + 1:
            raise RuntimeError(
                f"create 后 collection 数量异常：before={before_count}, after={after_count}"
            )
        stored = self._require_exact(action.record_id)
        self._verify_exact_storage(stored, desired)
        self._verify_plan_unchanged(desired)
        return stored

    def replace_from_action(self, action: DdlMemoryPlanAction) -> ExistingDdlMemoryRecord:
        desired = self._validate_action(action, expected_action="changed")
        old_fingerprint = action.expected_existing_content_fingerprint
        new_fingerprint = action.desired_content_fingerprint
        if old_fingerprint is None or new_fingerprint is None:
            raise ValueError("changed action 必须同时携带旧指纹和新指纹")

        before_count = self._collection.count()
        current = self._get_exact(action.record_id)
        if current is None:
            raise ValueError(f"replace 冲突：record_id 不存在：{action.record_id}")

        current_plan = build_ddl_memory_plan([desired], [current])
        if len(current_plan.actions) != 1 or current_plan.actions[0].action not in {
            "changed",
            "unchanged",
        }:
            raise ValueError(f"replace 当前记录不是合法 managed v1：{action.record_id}")
        current_fingerprint = current.metadata.get("content_fingerprint")
        if current_fingerprint != old_fingerprint:
            raise ValueError(
                "replace stale 冲突："
                f"expected={old_fingerprint}, actual={current_fingerprint}"
            )

        self._collection.update(
            ids=[action.record_id],
            documents=[desired.normalized_ddl],
            metadatas=[dict(action.desired_metadata or {})],
        )
        after_count = self._collection.count()
        if after_count != before_count:
            raise RuntimeError(
                f"replace 后 collection 数量增长：before={before_count}, after={after_count}"
            )
        stored = self._require_exact(action.record_id)
        self._verify_exact_storage(stored, desired)
        self._verify_plan_unchanged(desired)
        return stored

    def _validate_action(
        self, action: DdlMemoryPlanAction, *, expected_action: str
    ) -> DdlMemoryIdentity:
        if not isinstance(action, DdlMemoryPlanAction):
            raise ValueError("action 必须是 DdlMemoryPlanAction")
        if action.action != expected_action:
            raise ValueError(
                f"{expected_action}_from_action 只接受 {expected_action}，实际为 {action.action}"
            )
        if action.normalized_ddl is None or action.desired_metadata is None:
            raise ValueError(f"{expected_action} action 缺少目标 DDL 或 Metadata")
        if action.desired_content_fingerprint is None:
            raise ValueError(f"{expected_action} action 缺少目标 content_fingerprint")

        try:
            rebuilt = build_ddl_memory_identity(
                DdlMemoryIdentityInput(
                    source_id=action.source_id,
                    schema_name=action.schema_name,
                    object_type=action.object_type,
                    object_name=action.object_name,
                ),
                action.normalized_ddl,
            )
        except ValueError as exc:
            raise ValueError(f"{expected_action} action 身份无效：{exc}") from exc

        expected_metadata = dict(rebuilt.effective_metadata)
        expected_metadata["content_fingerprint"] = rebuilt.content_fingerprint
        checks = {
            "logical_id": (action.logical_id, rebuilt.logical_id),
            "record_id": (action.record_id, rebuilt.record_id),
            "normalized_ddl": (action.normalized_ddl, rebuilt.normalized_ddl),
            "desired_content_fingerprint": (
                action.desired_content_fingerprint,
                rebuilt.content_fingerprint,
            ),
            "desired_metadata": (dict(action.desired_metadata), expected_metadata),
        }
        failures = [
            field_name
            for field_name, (actual, expected) in checks.items()
            if actual != expected
        ]
        if failures:
            raise ValueError(
                f"{expected_action} action 完整性校验失败：{', '.join(failures)}"
            )
        return rebuilt

    def _get_exact(self, record_id: str) -> ExistingDdlMemoryRecord | None:
        raw = self._collection.get(
            ids=[record_id], include=["documents", "metadatas"]
        )
        ids = list(raw.get("ids") or [])
        documents = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        if not (len(ids) == len(documents) == len(metadatas)):
            raise ValueError("record_id 精确读取的返回数组长度不一致")
        if not ids:
            return None
        if len(ids) != 1 or ids[0] != record_id:
            raise ValueError(f"record_id 精确读取结果异常：{record_id}")
        return ExistingDdlMemoryRecord(
            record_id=ids[0],
            document=documents[0],
            metadata=metadatas[0] or {},
        )

    def _require_exact(self, record_id: str) -> ExistingDdlMemoryRecord:
        record = self._get_exact(record_id)
        if record is None:
            raise RuntimeError(f"写入后未找到 record_id：{record_id}")
        return record

    def _verify_exact_storage(
        self, stored: ExistingDdlMemoryRecord, desired: DdlMemoryIdentity
    ) -> None:
        expected_metadata = dict(desired.effective_metadata)
        expected_metadata["content_fingerprint"] = desired.content_fingerprint
        if stored.document != desired.normalized_ddl:
            raise RuntimeError(f"写入后 document 不一致：{stored.record_id}")
        if dict(stored.metadata) != expected_metadata:
            raise RuntimeError(f"写入后 Metadata 不一致：{stored.record_id}")

    def _verify_plan_unchanged(self, desired: DdlMemoryIdentity) -> None:
        plan = build_ddl_memory_plan([desired], self.snapshot_records())
        matching = [
            action for action in plan.actions if action.record_id == desired.record_id
        ]
        if len(matching) != 1 or matching[0].action != "unchanged":
            raise RuntimeError(f"写入后 Plan 未判定 unchanged：{desired.record_id}")


def open_isolated_adapter(
    persist_directory: Path | str, *, require_empty: bool = True
) -> DdlMemoryChromaAdapter:
    target = validate_isolated_chroma_path(
        persist_directory, require_empty=require_empty
    )
    target.mkdir(parents=True, exist_ok=True)

    from backend.memory import ChineseChromaAgentMemory, EMBEDDING_FUNCTION

    memory = ChineseChromaAgentMemory(
        persist_directory=str(target),
        collection_name=COLLECTION_NAME,
        embedding_function=EMBEDDING_FUNCTION,
    )
    collection = memory._get_collection()
    return DdlMemoryChromaAdapter(collection, memory_owner=memory)


class _FakeCollection:
    def __init__(self) -> None:
        self._records: dict[str, tuple[str, dict[str, Any]]] = {}

    def count(self) -> int:
        return len(self._records)

    def get(
        self,
        ids: Sequence[str] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, list[Any]]:
        selected_ids = (
            [record_id for record_id in ids if record_id in self._records]
            if ids is not None
            else list(reversed(list(self._records)))
        )
        return {
            "ids": selected_ids,
            "documents": [self._records[record_id][0] for record_id in selected_ids],
            "metadatas": [
                dict(self._records[record_id][1]) for record_id in selected_ids
            ],
        }

    def add(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        for record_id in ids:
            if record_id in self._records:
                raise ValueError(f"duplicate id: {record_id}")
        for record_id, document, metadata in zip(ids, documents, metadatas):
            self._records[record_id] = (document, dict(metadata))

    def update(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        for record_id in ids:
            if record_id not in self._records:
                raise ValueError(f"missing id: {record_id}")
        for record_id, document, metadata in zip(ids, documents, metadatas):
            self._records[record_id] = (document, dict(metadata))


def _identity(column_type: str = "integer") -> DdlMemoryIdentity:
    return build_ddl_memory_identity(
        DdlMemoryIdentityInput("postgres_water", "public", "table", "adapter_demo"),
        f'CREATE TABLE "adapter_demo" (\n  id {column_type}\n);',
    )


def _single_action(
    desired: DdlMemoryIdentity,
    existing: Sequence[ExistingDdlMemoryRecord],
    expected_action: str,
) -> DdlMemoryPlanAction:
    plan = build_ddl_memory_plan([desired], existing)
    matching = [action for action in plan.actions if action.record_id == desired.record_id]
    if len(matching) != 1 or matching[0].action != expected_action:
        raise AssertionError(f"预期 {expected_action} action，实际为 {matching}")
    return matching[0]


def _expect_error(callable_object: Any, expected_text: str) -> None:
    try:
        callable_object()
    except (RuntimeError, ValueError) as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常信息未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期失败：{expected_text}")


def self_test() -> int:
    protected_paths = (
        FORMAL_CHROMA,
        FORMAL_CHROMA / "child",
        REPOSITORY_CHROMA,
        REPOSITORY_CHROMA / "child",
        PROJECT_ROOT / "adapter-test",
        Path(r"E:\3\outside-training-backups\adapter-test"),
    )
    for protected_path in protected_paths:
        _expect_error(
            lambda item=protected_path: validate_isolated_chroma_path(item),
            "隔离 Chroma",
        )

    collection = _FakeCollection()
    collection.add(
        ids=["z-unmanaged", "a-unmanaged"],
        documents=["z", "a"],
        metadatas=[{"kind": "legacy-z"}, {"kind": "legacy-a"}],
    )
    adapter = DdlMemoryChromaAdapter(collection)
    assert [record.record_id for record in adapter.snapshot_records()] == [
        "a-unmanaged",
        "z-unmanaged",
    ]
    unmanaged_before = adapter.snapshot_records()

    old_identity = _identity("integer")
    create_action = _single_action(old_identity, adapter.snapshot_records(), "create")
    adapter.create_from_action(create_action)
    assert collection.count() == 3
    assert _single_action(
        old_identity, adapter.snapshot_records(), "unchanged"
    ).action == "unchanged"
    _expect_error(lambda: adapter.create_from_action(create_action), "已存在")

    new_identity = _identity("bigint")
    changed_action = _single_action(
        new_identity, adapter.snapshot_records(), "changed"
    )
    count_before_replace = collection.count()
    adapter.replace_from_action(changed_action)
    assert collection.count() == count_before_replace
    assert _single_action(
        new_identity, adapter.snapshot_records(), "unchanged"
    ).action == "unchanged"
    _expect_error(lambda: adapter.replace_from_action(changed_action), "stale 冲突")
    assert collection.count() == count_before_replace

    unchanged_action = _single_action(
        new_identity, adapter.snapshot_records(), "unchanged"
    )
    removed_action = build_ddl_memory_plan([], adapter.snapshot_records()).actions[0]
    _expect_error(
        lambda: adapter.create_from_action(unchanged_action), "只接受 create"
    )
    _expect_error(
        lambda: adapter.replace_from_action(removed_action), "只接受 changed"
    )

    tampered_actions = (
        replace(create_action, record_id=create_action.record_id + "x"),
        replace(create_action, normalized_ddl=create_action.normalized_ddl + " "),
        replace(create_action, desired_content_fingerprint="0" * 64),
        replace(
            create_action,
            desired_metadata={**dict(create_action.desired_metadata or {}), "extra": "x"},
        ),
    )
    fresh_adapter = DdlMemoryChromaAdapter(_FakeCollection())
    for tampered in tampered_actions:
        _expect_error(
            lambda item=tampered: fresh_adapter.create_from_action(item),
            "完整性校验失败",
        )
    assert fresh_adapter._collection.count() == 0

    unmanaged_after = tuple(
        record
        for record in adapter.snapshot_records()
        if record.record_id in {"a-unmanaged", "z-unmanaged"}
    )
    assert unmanaged_after == unmanaged_before
    assert not hasattr(DdlMemoryChromaAdapter, "delete")
    assert not hasattr(DdlMemoryChromaAdapter, "remove")
    assert not hasattr(DdlMemoryChromaAdapter, "apply_plan")

    forbidden_modules = ("chromadb", "vanna", "backend.memory")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )

    print("DDL_MEMORY_ADAPTER_SELF_TEST=PASS")
    print("PATH_GUARD_TEST=PASS")
    print("SNAPSHOT_TEST=PASS")
    print("CREATE_TEST=PASS")
    print("REPLACE_TEST=PASS")
    print("STALE_CONFLICT_TEST=PASS")
    print("UNMANAGED_PRESERVATION_TEST=PASS")
    print("DELETE_CAPABILITY=NONE")
    return 0


def _record_to_stable_dict(record: ExistingDdlMemoryRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "document": record.document,
        "metadata": dict(record.metadata),
    }


def integration_test(isolated_chroma: Path, evidence_dir: Path) -> dict[str, Any]:
    isolated = validate_isolated_chroma_path(isolated_chroma, require_empty=True)
    evidence = validate_evidence_path(evidence_dir, isolated)
    evidence.mkdir(parents=True, exist_ok=False)

    adapter = open_isolated_adapter(isolated, require_empty=True)
    count_initial = adapter._collection.count()
    if count_initial != 0:
        raise RuntimeError(f"隔离 Chroma 初始记录数必须为 0，实际为 {count_initial}")

    unmanaged_id = "f6-1d-unmanaged-test"
    unmanaged_document = "UNMANAGED TEST RECORD"
    unmanaged_metadata = {"kind": "legacy-test", "is_text_memory": True}
    adapter._collection.add(
        ids=[unmanaged_id],
        documents=[unmanaged_document],
        metadatas=[unmanaged_metadata],
    )
    count_after_unmanaged = adapter._collection.count()
    unmanaged_before = adapter._get_exact(unmanaged_id)

    old_identity = _identity("integer")
    create_action = _single_action(old_identity, adapter.snapshot_records(), "create")
    adapter.create_from_action(create_action)
    count_after_create = adapter._collection.count()
    unchanged_after_create = _single_action(
        old_identity, adapter.snapshot_records(), "unchanged"
    )

    new_identity = _identity("bigint")
    changed_action = _single_action(
        new_identity, adapter.snapshot_records(), "changed"
    )
    adapter.replace_from_action(changed_action)
    count_after_replace = adapter._collection.count()
    unchanged_after_replace = _single_action(
        new_identity, adapter.snapshot_records(), "unchanged"
    )

    stale_error = None
    try:
        adapter.replace_from_action(changed_action)
    except ValueError as exc:
        stale_error = str(exc)
    if stale_error is None or "stale 冲突" not in stale_error:
        raise RuntimeError("旧 changed action 未触发 stale 冲突")
    count_after_stale = adapter._collection.count()
    unmanaged_after = adapter._get_exact(unmanaged_id)
    if unmanaged_before != unmanaged_after:
        raise RuntimeError("unmanaged 测试记录发生变化")

    del adapter
    gc.collect()
    reopened = open_isolated_adapter(isolated, require_empty=False)
    reopened_records = reopened.snapshot_records()
    reopened_count = reopened._collection.count()
    reopened_plan = build_ddl_memory_plan([new_identity], reopened_records)
    reopened_matching = [
        action
        for action in reopened_plan.actions
        if action.record_id == new_identity.record_id
    ]

    expected_counts = (0, 1, 2, 2, 2)
    actual_counts = (
        count_initial,
        count_after_unmanaged,
        count_after_create,
        count_after_replace,
        count_after_stale,
    )
    accepted = (
        actual_counts == expected_counts
        and unchanged_after_create.action == "unchanged"
        and unchanged_after_replace.action == "unchanged"
        and reopened_count == 2
        and len(reopened_matching) == 1
        and reopened_matching[0].action == "unchanged"
        and reopened._get_exact(unmanaged_id) == unmanaged_before
    )
    if not accepted:
        raise RuntimeError(
            f"F6-1D 隔离集成验收失败：counts={actual_counts}, reopened={reopened_count}"
        )

    summary = {
        "integration_test": "PASS",
        "collection_name": COLLECTION_NAME,
        "embedding_configuration": "BAAI/bge-small-zh-v1.5 via backend.memory.EMBEDDING_FUNCTION",
        "count_initial": count_initial,
        "count_after_unmanaged": count_after_unmanaged,
        "count_after_create": count_after_create,
        "count_after_replace": count_after_replace,
        "count_after_stale_conflict": count_after_stale,
        "reopened_count": reopened_count,
        "create_verified_unchanged": True,
        "replace_verified_unchanged": True,
        "stale_conflict_observed": True,
        "stale_conflict_reason": stale_error,
        "unmanaged_preserved": True,
        "unmanaged_record_sha256": hashlib.sha256(
            json.dumps(
                _record_to_stable_dict(unmanaged_before),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "delete_capability": "NONE",
        "save_text_memory_called": False,
        "random_memory_id_generated": False,
        "formal_chroma_client_open_attempt_count_by_script": (
            FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT
        ),
        "isolated_chroma": str(isolated),
    }
    (evidence / "adapter-integration-test.txt").write_text(
        "\n".join(
            [
                "COMMAND=python -m training.sop.ddl_memory_adapter --integration-test",
                "EXIT_CODE=0",
                "ADAPTER_INTEGRATION_TEST=PASS",
                f"COUNT_INITIAL={count_initial}",
                f"COUNT_AFTER_UNMANAGED={count_after_unmanaged}",
                f"COUNT_AFTER_CREATE={count_after_create}",
                f"COUNT_AFTER_REPLACE={count_after_replace}",
                f"COUNT_AFTER_STALE_CONFLICT={count_after_stale}",
                "STALE_CONFLICT_TEST=PASS",
                "UNMANAGED_PRESERVATION_TEST=PASS",
                "REOPEN_PERSISTENCE_TEST=PASS",
                "FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence / "integration-summary.json").write_text(
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
        print(f"ADAPTER_INTEGRATION_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for key in (
        "count_initial",
        "count_after_unmanaged",
        "count_after_create",
        "count_after_replace",
        "count_after_stale_conflict",
        "formal_chroma_client_open_attempt_count_by_script",
    ):
        print(f"{key}={summary[key]}")
    print("adapter_integration_test=PASS")
    print(f"evidence_dir={args.evidence_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
