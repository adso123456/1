"""使用真实 115 条 DDL 在仓库外全新 Chroma 中执行幂等验收。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from train_step3 import build_all_table_ddls, group_tables, load_metadata_index
from training.sop.ddl_memory_adapter import (
    BACKUP_ROOT,
    FORMAL_CHROMA,
    FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT,
    PROJECT_ROOT,
    REPOSITORY_CHROMA,
    open_isolated_adapter,
    validate_isolated_chroma_path,
)
from training.sop.ddl_memory_apply import apply_ddl_memory_plan
from training.sop.ddl_memory_identity import (
    IDENTITY_VERSION,
    DdlMemoryIdentity,
    DdlMemoryIdentityInput,
    build_ddl_memory_identity,
)
from training.sop.ddl_memory_plan import (
    STORAGE_METADATA_FIELDS,
    DdlMemoryPlan,
    ExistingDdlMemoryRecord,
    build_ddl_memory_plan,
)


EXPECTED_DDL_COUNT = 115
ACCEPTANCE_ROOT_PATTERN = re.compile(r"f6-1f-\d{8}-\d{6}\Z")


@dataclass(frozen=True)
class DesiredInputBundle:
    metadata_record_count: int
    table_count: int
    generated_ddl_count: int
    unique_object_name_count: int
    unique_logical_id_count: int
    unique_record_id_count: int
    desired_memories: tuple[DdlMemoryIdentity, ...]


@dataclass(frozen=True)
class SemanticSnapshot:
    semantic_snapshot_sha256: str
    records: tuple[Mapping[str, Any], ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_desired_bundle(bundle: DesiredInputBundle) -> DesiredInputBundle:
    expected = {
        "metadata_record_count_positive": bundle.metadata_record_count > 0,
        "table_count": bundle.table_count == EXPECTED_DDL_COUNT,
        "generated_ddl_count": bundle.generated_ddl_count == EXPECTED_DDL_COUNT,
        "unique_object_name_count": (
            bundle.unique_object_name_count == EXPECTED_DDL_COUNT
        ),
        "unique_logical_id_count": (
            bundle.unique_logical_id_count == EXPECTED_DDL_COUNT
        ),
        "unique_record_id_count": bundle.unique_record_id_count == EXPECTED_DDL_COUNT,
        "desired_memory_count": len(bundle.desired_memories) == EXPECTED_DDL_COUNT,
    }
    failures = [name for name, accepted in expected.items() if not accepted]
    if failures:
        raise ValueError(f"115 条真实输入前置校验失败：{', '.join(failures)}")
    return bundle


def build_full_desired_memories() -> DesiredInputBundle:
    """只调用 train_step3 的纯读取/生成函数；不创建 Memory Client。"""
    metadata_records = load_metadata_index()
    tables = group_tables(metadata_records)
    generated, _geometry_count = build_all_table_ddls(tables)
    desired = tuple(
        build_ddl_memory_identity(
            DdlMemoryIdentityInput(
                source_id="postgres_water",
                schema_name="public",
                object_type="table",
                object_name=item["table"],
            ),
            item["ddl"],
        )
        for item in generated
    )
    sorted_desired = tuple(
        sorted(desired, key=lambda item: item.effective_metadata["object_name"])
    )
    bundle = DesiredInputBundle(
        metadata_record_count=len(metadata_records),
        table_count=len(tables),
        generated_ddl_count=len(generated),
        unique_object_name_count=len(
            {item.effective_metadata["object_name"] for item in sorted_desired}
        ),
        unique_logical_id_count=len({item.logical_id for item in sorted_desired}),
        unique_record_id_count=len({item.record_id for item in sorted_desired}),
        desired_memories=sorted_desired,
    )
    return _validate_desired_bundle(bundle)


def _desired_manifest(
    desired_memories: Sequence[DdlMemoryIdentity],
) -> tuple[dict[str, str], ...]:
    records = [
        {
            "table_name": item.effective_metadata["object_name"],
            "record_id": item.record_id,
            "document_sha256": hashlib.sha256(
                item.normalized_ddl.encode("utf-8")
            ).hexdigest(),
            "content_fingerprint": item.content_fingerprint,
        }
        for item in desired_memories
    ]
    record_ids = [item["record_id"] for item in records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("期望清单存在重复 record_id")
    return tuple(sorted(records, key=lambda item: item["record_id"]))


def build_semantic_snapshot(
    records: Sequence[ExistingDdlMemoryRecord],
) -> SemanticSnapshot:
    seen_ids: set[str] = set()
    manifest: list[dict[str, Any]] = []
    for record in records:
        if record.record_id in seen_ids:
            raise ValueError(f"语义快照存在重复 record_id：{record.record_id}")
        seen_ids.add(record.record_id)
        missing = sorted(STORAGE_METADATA_FIELDS - set(record.metadata))
        if missing:
            raise ValueError(
                f"语义快照记录 {record.record_id} 缺少 managed Metadata：{missing}"
            )
        stable_metadata = {
            key: record.metadata[key] for key in sorted(STORAGE_METADATA_FIELDS)
        }
        if stable_metadata["record_id"] != record.record_id:
            raise ValueError(f"语义快照顶层 ID 与 Metadata 不一致：{record.record_id}")
        manifest.append(
            {
                "record_id": record.record_id,
                "document_sha256": hashlib.sha256(
                    record.document.encode("utf-8")
                ).hexdigest(),
                "managed_metadata": stable_metadata,
            }
        )
    sorted_manifest = tuple(sorted(manifest, key=lambda item: item["record_id"]))
    snapshot_sha = hashlib.sha256(
        _canonical_json(sorted_manifest).encode("utf-8")
    ).hexdigest()
    return SemanticSnapshot(snapshot_sha, sorted_manifest)


def _identity_key_from_metadata(metadata: Mapping[str, Any]) -> str:
    return (
        f"{IDENTITY_VERSION}|{metadata['source_id']}|{metadata['schema_name']}|"
        f"{metadata['object_type']}|{metadata['object_name']}"
    )


def _duplicate_group_counts(
    records: Sequence[ExistingDdlMemoryRecord],
) -> dict[str, int]:
    record_ids = Counter(record.record_id for record in records)
    logical_ids = Counter(record.metadata.get("logical_id") for record in records)
    identity_keys = Counter(
        _identity_key_from_metadata(record.metadata) for record in records
    )
    return {
        "duplicate_record_id_groups": sum(count > 1 for count in record_ids.values()),
        "duplicate_logical_id_groups": sum(
            count > 1 for count in logical_ids.values()
        ),
        "duplicate_identity_key_groups": sum(
            count > 1 for count in identity_keys.values()
        ),
    }


def _plan_counts(plan: DdlMemoryPlan) -> dict[str, int]:
    return {
        "desired": plan.desired_count,
        "managed_existing": plan.managed_existing_count,
        "unmanaged_existing": plan.unmanaged_existing_count,
        "create": plan.create_count,
        "unchanged": plan.unchanged_count,
        "changed": plan.changed_count,
        "removed": plan.removed_count,
    }


def _require_plan_counts(
    plan: DdlMemoryPlan, expected: Mapping[str, int], stage: str
) -> None:
    actual = _plan_counts(plan)
    if actual != dict(expected):
        raise RuntimeError(f"{stage} Plan 计数失败：actual={actual}, expected={dict(expected)}")


def _validate_acceptance_paths(
    isolated_chroma: Path | str, evidence_dir: Path | str
) -> tuple[Path, Path]:
    isolated = validate_isolated_chroma_path(isolated_chroma, require_empty=True)
    evidence = Path(evidence_dir).expanduser().resolve()
    run_root = isolated.parent
    if (
        isolated.name != "isolated_chroma"
        or ACCEPTANCE_ROOT_PATTERN.fullmatch(run_root.name) is None
    ):
        raise ValueError(
            "隔离路径必须为 E:\\3\\_training_backups\\"
            "f6-1f-<YYYYMMDD-HHMMSS>\\isolated_chroma"
        )
    if evidence.name != "evidence" or evidence.parent != run_root:
        raise ValueError("Evidence 必须与 isolated_chroma 位于同一 F6-1F 运行目录")
    if not _is_within(evidence, BACKUP_ROOT):
        raise ValueError(f"Evidence 必须位于 {BACKUP_ROOT} 下")
    if evidence.exists() and any(evidence.iterdir()):
        raise ValueError(f"Evidence 必须全新或为空：{evidence}")
    return isolated, evidence


def _existing_from_desired(
    desired: DdlMemoryIdentity, runtime_metadata: Mapping[str, Any] | None = None
) -> ExistingDdlMemoryRecord:
    metadata = dict(desired.effective_metadata)
    metadata["content_fingerprint"] = desired.content_fingerprint
    metadata.update(runtime_metadata or {})
    return ExistingDdlMemoryRecord(
        desired.record_id, desired.normalized_ddl, metadata
    )


def _expect_error(callable_object: Any, expected_text: str) -> None:
    try:
        callable_object()
    except (RuntimeError, ValueError) as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期失败：{expected_text}")


def self_test() -> int:
    first = build_full_desired_memories()
    repeated = build_full_desired_memories()
    assert first == repeated
    assert [
        item.effective_metadata["object_name"] for item in first.desired_memories
    ] == sorted(
        item.effective_metadata["object_name"] for item in first.desired_memories
    )

    desired_manifest = _desired_manifest(first.desired_memories)
    reversed_manifest = _desired_manifest(tuple(reversed(first.desired_memories)))
    assert desired_manifest == reversed_manifest

    plain_records = tuple(_existing_from_desired(item) for item in first.desired_memories)
    runtime_records = tuple(
        _existing_from_desired(
            item,
            {
                "timestamp": "2099-01-01T00:00:00Z",
                "absolute_path": r"X:\ignored\candidate",
            },
        )
        for item in reversed(first.desired_memories)
    )
    plain_snapshot = build_semantic_snapshot(plain_records)
    runtime_snapshot = build_semantic_snapshot(runtime_records)
    assert plain_snapshot.semantic_snapshot_sha256 == (
        runtime_snapshot.semantic_snapshot_sha256
    )

    duplicate_records = plain_records + (plain_records[0],)
    _expect_error(
        lambda: build_semantic_snapshot(duplicate_records), "重复 record_id"
    )
    invalid_bundle = DesiredInputBundle(
        metadata_record_count=first.metadata_record_count,
        table_count=114,
        generated_ddl_count=114,
        unique_object_name_count=114,
        unique_logical_id_count=114,
        unique_record_id_count=114,
        desired_memories=first.desired_memories[:114],
    )
    _expect_error(lambda: _validate_desired_bundle(invalid_bundle), "前置校验失败")

    protected_paths = (
        FORMAL_CHROMA,
        FORMAL_CHROMA / "child",
        REPOSITORY_CHROMA,
        REPOSITORY_CHROMA / "child",
        PROJECT_ROOT / "acceptance-test",
    )
    for protected in protected_paths:
        _expect_error(
            lambda path=protected: validate_isolated_chroma_path(path),
            "隔离 Chroma",
        )

    forbidden_modules = ("chromadb", "vanna", "backend.memory")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )
    assert FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT == 0

    print("DDL_MEMORY_IDEMPOTENCY_ACCEPTANCE_SELF_TEST=PASS")
    print("SOURCE_INPUT_VALIDATION_TEST=PASS:115")
    print("INPUT_ORDER_STABILITY_TEST=PASS")
    print("SEMANTIC_SNAPSHOT_STABILITY_TEST=PASS")
    print("DUPLICATE_ID_REJECTION_TEST=PASS")
    print("NON_115_REJECTION_TEST=PASS")
    print("PATH_GUARD_TEST=PASS")
    print("CHROMA_CLIENT_CREATED=0")
    return 0


def acceptance_test(
    isolated_chroma: Path | str, evidence_dir: Path | str
) -> dict[str, Any]:
    # 真实输入及其 115/唯一性门禁必须先于任何 Chroma Client 创建。
    bundle = build_full_desired_memories()
    desired = bundle.desired_memories
    desired_manifest = _desired_manifest(desired)
    isolated, evidence = _validate_acceptance_paths(isolated_chroma, evidence_dir)
    evidence.mkdir(parents=True, exist_ok=False)

    initial_expected = {
        "desired": 115,
        "managed_existing": 0,
        "unmanaged_existing": 0,
        "create": 115,
        "unchanged": 0,
        "changed": 0,
        "removed": 0,
    }
    stable_expected = {
        "desired": 115,
        "managed_existing": 115,
        "unmanaged_existing": 0,
        "create": 0,
        "unchanged": 115,
        "changed": 0,
        "removed": 0,
    }

    adapter = open_isolated_adapter(isolated, require_empty=True)
    first_plan = build_ddl_memory_plan(desired, adapter.snapshot_records())
    _require_plan_counts(first_plan, initial_expected, "第一轮")
    first_result = apply_ddl_memory_plan(adapter, desired, first_plan)
    if (
        first_result.created_count,
        first_result.verified_noop_count,
        first_result.replaced_count,
        first_result.retained_removed_count,
        first_result.count_before,
        first_result.count_after,
    ) != (115, 0, 0, 0, 0, 115):
        raise RuntimeError(f"第一轮 Apply 计数失败：{first_result.to_dict()}")
    after_first_records = adapter.snapshot_records()
    first_snapshot = build_semantic_snapshot(after_first_records)
    first_stable_plan = build_ddl_memory_plan(desired, after_first_records)
    _require_plan_counts(first_stable_plan, stable_expected, "第一轮写后")

    del adapter
    gc.collect()
    second_adapter = open_isolated_adapter(isolated, require_empty=False)
    second_plan = build_ddl_memory_plan(desired, second_adapter.snapshot_records())
    _require_plan_counts(second_plan, stable_expected, "第二轮")
    second_result = apply_ddl_memory_plan(second_adapter, desired, second_plan)
    if (
        second_result.created_count,
        second_result.verified_noop_count,
        second_result.replaced_count,
        second_result.retained_removed_count,
        second_result.count_before,
        second_result.count_after,
    ) != (0, 115, 0, 0, 115, 115):
        raise RuntimeError(f"第二轮 Apply 计数失败：{second_result.to_dict()}")
    after_second_records = second_adapter.snapshot_records()
    second_snapshot = build_semantic_snapshot(after_second_records)

    del second_adapter
    gc.collect()
    final_adapter = open_isolated_adapter(isolated, require_empty=False)
    final_records = final_adapter.snapshot_records()
    final_plan = build_ddl_memory_plan(desired, final_records)
    _require_plan_counts(final_plan, stable_expected, "最终重开")
    final_snapshot = build_semantic_snapshot(final_records)
    duplicate_counts = _duplicate_group_counts(final_records)
    if any(duplicate_counts.values()):
        raise RuntimeError(f"最终重复身份组非零：{duplicate_counts}")
    if len(final_records) != 115:
        raise RuntimeError(f"最终重开记录数不是 115：{len(final_records)}")
    if not (
        first_snapshot.semantic_snapshot_sha256
        == second_snapshot.semantic_snapshot_sha256
        == final_snapshot.semantic_snapshot_sha256
    ):
        raise RuntimeError("三次语义快照 SHA 不一致")

    summary = {
        "acceptance_test": "PASS",
        "source": "train_step3.load_metadata_index/group_tables/build_all_table_ddls",
        "metadata_record_count": bundle.metadata_record_count,
        "source_table_count": bundle.table_count,
        "desired_memory_count": len(desired),
        "unique_object_name_count": bundle.unique_object_name_count,
        "unique_logical_id_count": bundle.unique_logical_id_count,
        "unique_record_id_count": bundle.unique_record_id_count,
        "first_plan_counts": _plan_counts(first_plan),
        "first_apply_result": {
            "created": first_result.created_count,
            "verified_noop": first_result.verified_noop_count,
            "replaced": first_result.replaced_count,
            "retained_removed": first_result.retained_removed_count,
            "count_before": first_result.count_before,
            "count_after": first_result.count_after,
        },
        "second_plan_counts": _plan_counts(second_plan),
        "second_apply_result": {
            "created": second_result.created_count,
            "verified_noop": second_result.verified_noop_count,
            "replaced": second_result.replaced_count,
            "retained_removed": second_result.retained_removed_count,
            "count_before": second_result.count_before,
            "count_after": second_result.count_after,
        },
        "final_plan_counts": _plan_counts(final_plan),
        "final_reopen_count": len(final_records),
        **duplicate_counts,
        "first_apply_snapshot_sha": first_snapshot.semantic_snapshot_sha256,
        "second_apply_snapshot_sha": second_snapshot.semantic_snapshot_sha256,
        "reopened_final_snapshot_sha": final_snapshot.semantic_snapshot_sha256,
        "snapshot_equality_test": "PASS",
        "formal_chroma_client_open_attempts_by_script": (
            FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT
        ),
    }
    (evidence / "desired-record-manifest.json").write_text(
        json.dumps(
            {
                "record_count": len(desired_manifest),
                "records": desired_manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence / "semantic-snapshot-manifest.json").write_text(
        json.dumps(
            {
                "record_count": len(final_snapshot.records),
                "first_apply_snapshot_sha": first_snapshot.semantic_snapshot_sha256,
                "second_apply_snapshot_sha": second_snapshot.semantic_snapshot_sha256,
                "reopened_final_snapshot_sha": final_snapshot.semantic_snapshot_sha256,
                "records": final_snapshot.records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence / "acceptance-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    run_lines = [
        "COMMAND=python -m training.sop.ddl_memory_idempotency_acceptance --acceptance-test",
        "EXIT_CODE=0",
        "DDL_MEMORY_IDEMPOTENCY_ACCEPTANCE=PASS",
        f"SOURCE_TABLE_COUNT={bundle.table_count}",
        f"DESIRED_MEMORY_COUNT={len(desired)}",
        "FIRST_PLAN_COUNTS=desired:115,managed:0,unmanaged:0,create:115,unchanged:0,changed:0,removed:0",
        "FIRST_APPLY_RESULT=created:115,verified_noop:0,replaced:0,retained_removed:0,count:0->115",
        "SECOND_PLAN_COUNTS=desired:115,managed:115,unmanaged:0,create:0,unchanged:115,changed:0,removed:0",
        "SECOND_APPLY_RESULT=created:0,verified_noop:115,replaced:0,retained_removed:0,count:115->115",
        "FINAL_REOPEN_COUNT=115",
        "DUPLICATE_RECORD_ID_GROUPS=0",
        "DUPLICATE_LOGICAL_ID_GROUPS=0",
        "DUPLICATE_IDENTITY_KEY_GROUPS=0",
        f"FIRST_APPLY_SNAPSHOT_SHA={first_snapshot.semantic_snapshot_sha256}",
        f"SECOND_APPLY_SNAPSHOT_SHA={second_snapshot.semantic_snapshot_sha256}",
        f"REOPENED_FINAL_SNAPSHOT_SHA={final_snapshot.semantic_snapshot_sha256}",
        "SNAPSHOT_EQUALITY_TEST=PASS",
        "FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0",
    ]
    (evidence / "acceptance-run.txt").write_text(
        "\n".join(run_lines) + "\n", encoding="utf-8"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--acceptance-test", action="store_true")
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
        raise SystemExit("--acceptance-test 必须显式传入隔离 Chroma 和 Evidence 路径")
    try:
        summary = acceptance_test(args.isolated_chroma, args.evidence_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        evidence = args.evidence_dir.resolve()
        if evidence.exists() and evidence.is_dir():
            (evidence / "acceptance-run.txt").write_text(
                "COMMAND=python -m training.sop.ddl_memory_idempotency_acceptance --acceptance-test\n"
                "EXIT_CODE=1\n"
                f"DDL_MEMORY_IDEMPOTENCY_ACCEPTANCE=FAIL\nERROR={type(exc).__name__}: {exc}\n",
                encoding="utf-8",
            )
        print(f"DDL_MEMORY_ACCEPTANCE_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    output_keys = (
        "source_table_count",
        "desired_memory_count",
        "unique_logical_id_count",
        "unique_record_id_count",
        "final_reopen_count",
        "duplicate_record_id_groups",
        "duplicate_logical_id_groups",
        "duplicate_identity_key_groups",
        "first_apply_snapshot_sha",
        "second_apply_snapshot_sha",
        "reopened_final_snapshot_sha",
        "snapshot_equality_test",
        "formal_chroma_client_open_attempts_by_script",
    )
    for key in output_keys:
        print(f"{key}={summary[key]}")
    print("ddl_memory_idempotency_acceptance=PASS")
    print(f"evidence_dir={args.evidence_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
