"""仓库外隔离 Chroma 中 run_sql Tool Memory 的精确适配层。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from training.sop.memory_write_plan import (
    MEMORY_KIND,
    RECORD_SCHEMA_VERSION,
    ExistingRecordSnapshot,
    MemoryWritePlan,
    MemoryWritePlanItem,
    build_memory_identity_from_canonical_content,
)
from vanna.integrations.chromadb import ChromaAgentMemory


RECORD_ID_PATTERN = re.compile(r"^toolmem-v1-[0-9a-f]{64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTROLLED_METADATA_FIELDS = (
    "question",
    "tool_name",
    "args_json",
    "success",
    "metadata_json",
    "record_schema_version",
    "memory_kind",
    "memory_content_sha256",
    "created_by_training_batch_id",
    "created_by_batch_content_sha256",
    "created_from_sample_id",
    "training_level",
    "train_decision",
)
COMPATIBILITY_METADATA_FIELDS = (
    "sample_id",
    "training_level",
    "train_decision",
    "review_reason",
    "source",
    "expected_behavior",
    "expected_tables",
    "training_batch_id",
    "batch_content_sha256",
    "memory_content_sha256",
)

RecordClassification = Literal[
    "controlled_tool_record",
    "legacy_tool_record",
    "text_memory",
    "malformed_record",
    "unknown_record",
]


class ChromaToolMemoryAdapterError(RuntimeError):
    """适配层稳定错误，``code`` 可供后续执行器判定。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class AdapterIssue:
    code: str
    record_id: str
    message: str


@dataclass(frozen=True)
class StoredToolRecord:
    storage_id: str
    document: str | None
    metadata: Mapping[str, Any] | None
    classification: RecordClassification
    canonical_content: Mapping[str, Any] | None = None
    memory_content_sha256: str | None = None
    derived_record_id: str | None = None
    compatibility_metadata: Mapping[str, Any] | None = None
    issues: tuple[AdapterIssue, ...] = ()


@dataclass(frozen=True)
class ToolRecordInventory:
    records: tuple[StoredToolRecord, ...]
    store_count_before: int
    store_count_after: int
    classifications: Mapping[str, int]
    derived_record_ids: Mapping[str, tuple[str, ...]]
    legacy_id_mismatches: tuple[str, ...]
    duplicate_existing_content: Mapping[str, tuple[str, ...]]
    content_address_conflicts: tuple[str, ...]
    issues: tuple[AdapterIssue, ...]
    inventory_sha256: str


@dataclass(frozen=True)
class ExactRecordResult:
    record_id: str
    status: Literal["found", "missing", "malformed", "storage_error"]
    record: StoredToolRecord | None = None
    error_code: str = ""


@dataclass(frozen=True)
class PlanPreflightResult:
    requested_record_ids: tuple[str, ...]
    controlled_existing_records: Mapping[str, ExistingRecordSnapshot]
    absent_record_ids: tuple[str, ...]
    legacy_conflicts: Mapping[str, tuple[str, ...]]
    duplicate_content_conflicts: Mapping[str, tuple[str, ...]]
    malformed_conflicts: tuple[str, ...]
    store_count_before: int
    store_count_after: int
    executable: bool
    issues: tuple[AdapterIssue, ...]
    store_preflight_sha256: str


@dataclass(frozen=True)
class AddRecordResult:
    record_id: str
    status: Literal[
        "created", "existing_same", "existing_conflict", "blocked", "storage_error"
    ]
    error_code: str = ""


@dataclass(frozen=True)
class BatchRecordResult:
    batch_content_sha256: str
    records: tuple[StoredToolRecord, ...]
    issues: tuple[AdapterIssue, ...]


@dataclass(frozen=True)
class ExpectedCreatedRecord:
    record_id: str
    memory_content_sha256: str


@dataclass(frozen=True)
class DeleteRecordItemResult:
    record_id: str
    status: Literal[
        "deleted",
        "not_found",
        "ownership_mismatch",
        "content_mismatch",
        "storage_error",
        "not_deleted",
    ]


@dataclass(frozen=True)
class DeleteRecordsResult:
    items: tuple[DeleteRecordItemResult, ...]
    deleted: bool


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _controlled_collection_access(memory: Any) -> Any:
    """本模块唯一允许接触 Vanna 私有 collection 的边界。"""

    return memory._get_collection()


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_attribute)


def _assert_no_reparse_component(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() and _is_reparse_point(current):
            raise ChromaToolMemoryAdapterError(
                "REPARSE_PATH_REJECTED", f"隔离路径包含链接或 reparse point：{current}"
            )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_storage_boundary(
    memory: Any, *, isolated_root: str | os.PathLike[str]
) -> None:
    if getattr(memory, "collection_name", None) != "tool_memories":
        raise ChromaToolMemoryAdapterError(
            "INVALID_COLLECTION_NAME", "collection 名称必须为 tool_memories"
        )

    raw_isolated = Path(isolated_root)
    raw_repository = REPOSITORY_ROOT
    raw_persist = Path(getattr(memory, "persist_directory", ""))
    if not raw_persist.is_absolute() or not raw_isolated.is_absolute() or not raw_repository.is_absolute():
        raise ChromaToolMemoryAdapterError("ABSOLUTE_PATH_REQUIRED", "所有边界路径必须是绝对路径")

    for candidate in (raw_isolated, raw_repository, raw_persist):
        _assert_no_reparse_component(candidate)

    if not raw_isolated.is_dir():
        raise ChromaToolMemoryAdapterError(
            "INVALID_ISOLATED_ROOT", "隔离根目录必须是已存在的目录"
        )
    if raw_persist.exists() and not raw_persist.is_dir():
        raise ChromaToolMemoryAdapterError(
            "INVALID_PERSIST_DIRECTORY", "Memory 持久化路径不能是普通文件"
        )

    isolated = raw_isolated.resolve(strict=True)
    repository = raw_repository.resolve(strict=True)
    persist = raw_persist.resolve(strict=False)
    if _is_within(isolated, repository):
        raise ChromaToolMemoryAdapterError(
            "ISOLATED_ROOT_INSIDE_REPOSITORY", "隔离根目录必须位于 Git 仓库外"
        )
    for protected_name in ("vanna_data", "agent_data"):
        protected = repository / protected_name
        if persist == protected or _is_within(persist, protected):
            raise ChromaToolMemoryAdapterError(
                "FORMAL_STORE_REJECTED", f"禁止访问正式 {protected_name}"
            )
    if persist == isolated or not _is_within(persist, isolated):
        raise ChromaToolMemoryAdapterError(
            "PERSIST_DIRECTORY_OUTSIDE_ISOLATED_ROOT", "Memory 目录必须位于隔离根目录内"
        )


def _issue(code: str, record_id: str, message: str) -> AdapterIssue:
    return AdapterIssue(code=code, record_id=record_id, message=message)


def _parse_json_object(value: Any, field: str, storage_id: str) -> tuple[dict[str, Any] | None, AdapterIssue | None]:
    if not isinstance(value, str):
        return None, _issue("INVALID_FIELD_TYPE", storage_id, f"{field} 必须是 JSON 字符串")
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None, _issue("INVALID_JSON", storage_id, f"{field} 无法解析")
    if not isinstance(parsed, dict):
        return None, _issue("INVALID_JSON_TYPE", storage_id, f"{field} 必须解析为对象")
    return parsed, None


def _has_legacy_shape(metadata: Mapping[str, Any]) -> bool:
    return any(
        field in metadata
        for field in ("question", "tool_name", "args_json", "success", "metadata_json")
    )


def _parse_stored_record(
    storage_id: Any, document: Any, metadata: Any
) -> StoredToolRecord:
    sid = storage_id if isinstance(storage_id, str) else str(storage_id)
    if not isinstance(storage_id, str):
        problem = _issue("INVALID_STORAGE_ID_TYPE", sid, "storage ID 必须是字符串")
        return StoredToolRecord(sid, None, None, "malformed_record", issues=(problem,))
    if not isinstance(metadata, dict):
        problem = _issue("INVALID_METADATA_TYPE", sid, "metadata 必须是对象")
        return StoredToolRecord(sid, document if isinstance(document, str) else None, None, "malformed_record", issues=(problem,))
    if metadata.get("is_text_memory") is True:
        if not isinstance(document, str):
            problem = _issue("INVALID_DOCUMENT_TYPE", sid, "Text Memory document 必须是字符串")
            return StoredToolRecord(sid, None, metadata, "malformed_record", issues=(problem,))
        return StoredToolRecord(sid, document, metadata, "text_memory")
    if not _has_legacy_shape(metadata) and not RECORD_ID_PATTERN.fullmatch(sid):
        return StoredToolRecord(sid, document if isinstance(document, str) else None, metadata, "unknown_record")

    problems: list[AdapterIssue] = []
    question = metadata.get("question")
    tool_name = metadata.get("tool_name")
    success = metadata.get("success")
    if not isinstance(document, str):
        problems.append(_issue("INVALID_DOCUMENT_TYPE", sid, "document 必须是字符串"))
    if not isinstance(question, str) or not question:
        problems.append(_issue("INVALID_FIELD_TYPE", sid, "question 必须是非空字符串"))
    if not isinstance(tool_name, str) or not tool_name:
        problems.append(_issue("INVALID_FIELD_TYPE", sid, "tool_name 必须是非空字符串"))
    if not isinstance(success, bool) or success is not True:
        problems.append(_issue("INVALID_SUCCESS", sid, "success 必须为布尔值 true"))
    args, args_problem = _parse_json_object(metadata.get("args_json"), "args_json", sid)
    compatibility, compatibility_problem = _parse_json_object(
        metadata.get("metadata_json"), "metadata_json", sid
    )
    if args_problem:
        problems.append(args_problem)
    if compatibility_problem:
        problems.append(compatibility_problem)
    if isinstance(document, str) and isinstance(question, str) and document != question:
        problems.append(_issue("DOCUMENT_QUESTION_MISMATCH", sid, "document 与 metadata.question 不一致"))
    if problems:
        return StoredToolRecord(
            sid, document if isinstance(document, str) else None, metadata,
            "malformed_record", compatibility_metadata=compatibility, issues=tuple(problems)
        )

    canonical = {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "question": question,
        "tool_name": tool_name,
        "args": args,
        "success": True,
    }
    identity = build_memory_identity_from_canonical_content(canonical)
    deterministic_storage_id = bool(RECORD_ID_PATTERN.fullmatch(sid))
    governance_present = all(field in metadata for field in CONTROLLED_METADATA_FIELDS[5:])
    classification: RecordClassification = "legacy_tool_record"

    if deterministic_storage_id and sid != identity.record_id:
        problems.append(_issue("CONTENT_ADDRESS_CONFLICT", sid, "确定性 ID 与重算内容身份不一致"))
    if governance_present:
        string_fields = (
            "record_schema_version", "memory_kind", "memory_content_sha256",
            "created_by_training_batch_id", "created_by_batch_content_sha256",
            "created_from_sample_id", "training_level", "train_decision",
        )
        for field in string_fields:
            if not isinstance(metadata.get(field), str) or not metadata[field]:
                problems.append(_issue("INVALID_GOVERNANCE_FIELD", sid, f"{field} 必须是非空字符串"))
        if metadata.get("record_schema_version") != RECORD_SCHEMA_VERSION:
            problems.append(_issue("INVALID_RECORD_SCHEMA_VERSION", sid, "record_schema_version 不受支持"))
        if metadata.get("memory_kind") != MEMORY_KIND:
            problems.append(_issue("INVALID_MEMORY_KIND", sid, "memory_kind 必须为 tool_usage"))
        if metadata.get("memory_content_sha256") != identity.memory_content_sha256:
            problems.append(_issue("CONTENT_DIGEST_MISMATCH", sid, "内容摘要与重算结果不一致"))
        for field in ("memory_content_sha256", "created_by_batch_content_sha256"):
            value = metadata.get(field)
            if isinstance(value, str) and not SHA256_PATTERN.fullmatch(value):
                problems.append(_issue("INVALID_SHA256", sid, f"{field} 不是 64 位小写十六进制"))
        if compatibility is not None:
            missing = [field for field in COMPATIBILITY_METADATA_FIELDS if field not in compatibility]
            if missing:
                problems.append(_issue("INCOMPLETE_COMPATIBILITY_METADATA", sid, ",".join(missing)))
            elif (
                compatibility["sample_id"] != metadata.get("created_from_sample_id")
                or compatibility["training_level"] != metadata.get("training_level")
                or compatibility["train_decision"] != metadata.get("train_decision")
                or compatibility["training_batch_id"] != metadata.get("created_by_training_batch_id")
                or compatibility["batch_content_sha256"] != metadata.get("created_by_batch_content_sha256")
                or compatibility["memory_content_sha256"] != identity.memory_content_sha256
                or not isinstance(compatibility["expected_tables"], list)
            ):
                problems.append(_issue("COMPATIBILITY_METADATA_MISMATCH", sid, "兼容 metadata 与顶层治理字段不一致"))
        if deterministic_storage_id and not problems:
            classification = "controlled_tool_record"

    if problems:
        classification = "malformed_record"
    return StoredToolRecord(
        storage_id=sid,
        document=document,
        metadata=metadata,
        classification=classification,
        canonical_content=identity.canonical_content,
        memory_content_sha256=identity.memory_content_sha256,
        derived_record_id=identity.record_id,
        compatibility_metadata=compatibility,
        issues=tuple(problems),
    )


def _extract_aligned_records(raw: Any, expected_count: int) -> tuple[StoredToolRecord, ...]:
    if not isinstance(raw, dict):
        raise ChromaToolMemoryAdapterError("INVALID_COLLECTION_RESULT", "collection.get() 未返回对象")
    ids = raw.get("ids")
    documents = raw.get("documents")
    metadatas = raw.get("metadatas")
    if not isinstance(ids, list) or not isinstance(documents, list) or not isinstance(metadatas, list):
        raise ChromaToolMemoryAdapterError("UNALIGNED_COLLECTION_RESULT", "ids/documents/metadatas 必须为列表")
    if len(ids) != len(documents) or len(ids) != len(metadatas) or len(ids) != expected_count:
        raise ChromaToolMemoryAdapterError("UNALIGNED_COLLECTION_RESULT", "count 与返回字段长度不一致")
    if len(ids) != len(set(ids)):
        raise ChromaToolMemoryAdapterError("DUPLICATE_STORAGE_ID", "collection.get() 返回重复实际 ID")
    return tuple(
        sorted(
            (_parse_stored_record(sid, doc, metadata) for sid, doc, metadata in zip(ids, documents, metadatas)),
            key=lambda record: record.storage_id,
        )
    )


def _metadata_for_item(item: MemoryWritePlanItem) -> dict[str, Any]:
    canonical = item.canonical_content
    metadata = {
        "question": canonical["question"],
        "tool_name": canonical["tool_name"],
        "args_json": _canonical_json(canonical["args"]),
        "success": True,
        "metadata_json": _canonical_json(item.compatibility_metadata),
    }
    metadata.update(item.governance_metadata)
    return metadata


def _record_matches_item(record: StoredToolRecord, item: MemoryWritePlanItem) -> bool:
    return (
        record.classification == "controlled_tool_record"
        and record.storage_id == item.record_id
        and _canonical_json(record.canonical_content) == _canonical_json(item.canonical_content)
        and record.memory_content_sha256 == item.memory_content_sha256
        and record.metadata == _metadata_for_item(item)
    )


class ChromaToolMemoryAdapter:
    """只允许仓库外隔离目录使用的精确 Tool Memory 适配层。"""

    def __init__(
        self,
        memory: Any,
        *,
        isolated_root: str | os.PathLike[str],
    ) -> None:
        _validate_storage_boundary(memory, isolated_root=isolated_root)
        if not isinstance(memory, ChromaAgentMemory):
            raise ChromaToolMemoryAdapterError(
                "INVALID_MEMORY_TYPE", "适配层只接受当前 ChromaAgentMemory 实例"
            )
        self._memory = memory
        self._collection = _controlled_collection_access(memory)
        if getattr(self._collection, "name", None) != "tool_memories":
            raise ChromaToolMemoryAdapterError(
                "COLLECTION_IDENTITY_MISMATCH", "底层 collection 名称不一致"
            )

    def inventory_tool_records(self) -> ToolRecordInventory:
        try:
            count_before = self._collection.count()
            raw = self._collection.get()
            count_after = self._collection.count()
        except Exception as error:  # noqa: BLE001
            raise ChromaToolMemoryAdapterError("STORAGE_ERROR", "全量清点失败") from error
        if not isinstance(count_before, int) or not isinstance(count_after, int):
            raise ChromaToolMemoryAdapterError("INVALID_COLLECTION_COUNT", "collection.count() 必须返回整数")
        if count_before != count_after:
            raise ChromaToolMemoryAdapterError("STORE_CHANGED_DURING_INVENTORY", "清点前后 count 不一致")
        records = _extract_aligned_records(raw, count_before)
        classification_counts = {
            name: sum(record.classification == name for record in records)
            for name in (
                "controlled_tool_record", "legacy_tool_record", "text_memory",
                "malformed_record", "unknown_record",
            )
        }
        derived: dict[str, list[str]] = {}
        legacy_ids: list[str] = []
        conflicts: list[str] = []
        issues: list[AdapterIssue] = []
        for record in records:
            issues.extend(record.issues)
            if record.derived_record_id:
                derived.setdefault(record.derived_record_id, []).append(record.storage_id)
            if record.classification == "legacy_tool_record" and record.derived_record_id:
                legacy_ids.append(record.storage_id)
            if any(problem.code == "CONTENT_ADDRESS_CONFLICT" for problem in record.issues):
                conflicts.append(record.storage_id)
        derived_frozen = {key: tuple(sorted(value)) for key, value in sorted(derived.items())}
        duplicates = {key: value for key, value in derived_frozen.items() if len(value) > 1}
        summary = {
            "records": [
                {
                    "storage_id": record.storage_id,
                    "classification": record.classification,
                    "derived_record_id": record.derived_record_id,
                    "memory_content_sha256": record.memory_content_sha256,
                    "issues": [asdict(problem) for problem in record.issues],
                }
                for record in records
            ],
            "store_count_before": count_before,
            "store_count_after": count_after,
            "classifications": classification_counts,
            "derived_record_ids": derived_frozen,
            "duplicate_existing_content": duplicates,
        }
        return ToolRecordInventory(
            records=records,
            store_count_before=count_before,
            store_count_after=count_after,
            classifications=classification_counts,
            derived_record_ids=derived_frozen,
            legacy_id_mismatches=tuple(sorted(legacy_ids)),
            duplicate_existing_content=duplicates,
            content_address_conflicts=tuple(sorted(conflicts)),
            issues=tuple(sorted(issues, key=lambda problem: (problem.record_id, problem.code))),
            inventory_sha256=_sha256_json(summary),
        )

    def get_exact_records(self, record_ids: Sequence[str]) -> tuple[ExactRecordResult, ...]:
        ids = tuple(record_ids)
        if not ids or len(ids) != len(set(ids)) or any(not RECORD_ID_PATTERN.fullmatch(value) for value in ids):
            raise ChromaToolMemoryAdapterError(
                "INVALID_RECORD_IDS", "record_ids 必须是非空、唯一的确定性 ID 列表"
            )
        try:
            raw = self._collection.get(ids=list(ids))
            returned_ids = raw.get("ids") if isinstance(raw, dict) else None
            if not isinstance(returned_ids, list):
                raise TypeError("invalid ids")
            records = _extract_aligned_records(raw, len(returned_ids))
        except Exception:  # noqa: BLE001
            return tuple(
                ExactRecordResult(record_id=value, status="storage_error", error_code="STORAGE_ERROR")
                for value in ids
            )
        by_id = {record.storage_id: record for record in records}
        results: list[ExactRecordResult] = []
        for value in ids:
            record = by_id.get(value)
            if record is None:
                results.append(ExactRecordResult(value, "missing"))
            elif record.classification != "controlled_tool_record":
                code = record.issues[0].code if record.issues else "INVALID_CONTROLLED_RECORD"
                results.append(ExactRecordResult(value, "malformed", record, code))
            else:
                results.append(ExactRecordResult(value, "found", record))
        return tuple(results)

    def inspect_plan_records(self, plan: MemoryWritePlan) -> PlanPreflightResult:
        if not isinstance(plan, MemoryWritePlan):
            raise ChromaToolMemoryAdapterError("INVALID_PLAN", "plan 类型无效")
        inventory = self.inventory_tool_records()
        by_storage_id = {record.storage_id: record for record in inventory.records}
        controlled: dict[str, ExistingRecordSnapshot] = {}
        absent: list[str] = []
        legacy: dict[str, tuple[str, ...]] = {}
        duplicate: dict[str, tuple[str, ...]] = {}
        malformed = sorted(
            record.storage_id for record in inventory.records if record.classification == "malformed_record"
        )
        issues = list(inventory.issues)
        for item in plan.items:
            actual_ids = inventory.derived_record_ids.get(item.record_id, ())
            if len(actual_ids) > 1:
                duplicate[item.record_id] = actual_ids
                issues.append(_issue("DUPLICATE_EXISTING_CONTENT", item.record_id, ",".join(actual_ids)))
                continue
            if actual_ids and actual_ids[0] != item.record_id:
                legacy[item.record_id] = actual_ids
                issues.append(_issue("LEGACY_ID_MISMATCH", item.record_id, actual_ids[0]))
                continue
            record = by_storage_id.get(item.record_id)
            if record is None:
                absent.append(item.record_id)
                continue
            if record.classification != "controlled_tool_record" or not _record_matches_item(record, item):
                if item.record_id not in malformed:
                    malformed.append(item.record_id)
                issues.append(_issue("EXISTING_RECORD_CONFLICT", item.record_id, "已有确定性记录与计划不一致"))
                continue
            metadata = record.metadata or {}
            controlled[item.record_id] = ExistingRecordSnapshot(
                record_id=item.record_id,
                canonical_content=record.canonical_content or {},
                memory_content_sha256=record.memory_content_sha256 or "",
                created_by_training_batch_id=metadata["created_by_training_batch_id"],
                created_by_batch_content_sha256=metadata["created_by_batch_content_sha256"],
                created_from_sample_id=metadata["created_from_sample_id"],
            )
        if malformed:
            issues.append(_issue("MALFORMED_STORE_RECORDS", "", ",".join(sorted(set(malformed)))))
        executable = plan.executable and not legacy and not duplicate and not malformed
        summary = {
            "requested_record_ids": [item.record_id for item in plan.items],
            "controlled_existing_records": {
                key: asdict(value) for key, value in sorted(controlled.items())
            },
            "absent_record_ids": absent,
            "legacy_conflicts": legacy,
            "duplicate_content_conflicts": duplicate,
            "malformed_conflicts": sorted(set(malformed)),
            "store_count_before": inventory.store_count_before,
            "store_count_after": inventory.store_count_after,
            "executable": executable,
            "issues": [asdict(problem) for problem in sorted(issues, key=lambda value: (value.record_id, value.code))],
        }
        return PlanPreflightResult(
            requested_record_ids=tuple(item.record_id for item in plan.items),
            controlled_existing_records=controlled,
            absent_record_ids=tuple(absent),
            legacy_conflicts=legacy,
            duplicate_content_conflicts=duplicate,
            malformed_conflicts=tuple(sorted(set(malformed))),
            store_count_before=inventory.store_count_before,
            store_count_after=inventory.store_count_after,
            executable=executable,
            issues=tuple(sorted(issues, key=lambda value: (value.record_id, value.code))),
            store_preflight_sha256=_sha256_json(summary),
        )

    def add_planned_record(
        self, plan: MemoryWritePlan, plan_item: MemoryWritePlanItem
    ) -> AddRecordResult:
        if not isinstance(plan, MemoryWritePlan) or not plan.executable:
            return AddRecordResult(getattr(plan_item, "record_id", ""), "blocked", "PLAN_NOT_EXECUTABLE")
        if not isinstance(plan_item, MemoryWritePlanItem) or plan_item.status != "create":
            return AddRecordResult(getattr(plan_item, "record_id", ""), "blocked", "ITEM_NOT_CREATE")
        matching = [item for item in plan.items if item.record_id == plan_item.record_id]
        if len(matching) != 1 or matching[0] != plan_item:
            return AddRecordResult(plan_item.record_id, "blocked", "ITEM_NOT_IN_PLAN")
        preflight = self.inspect_plan_records(plan)
        exact_before = self.get_exact_records([plan_item.record_id])[0]
        if exact_before.status == "found":
            return AddRecordResult(
                plan_item.record_id,
                "existing_same" if _record_matches_item(exact_before.record, plan_item) else "existing_conflict",  # type: ignore[arg-type]
            )
        if exact_before.status == "malformed":
            return AddRecordResult(plan_item.record_id, "existing_conflict", exact_before.error_code)
        if exact_before.status == "storage_error":
            return AddRecordResult(plan_item.record_id, "storage_error", exact_before.error_code)
        if not preflight.executable:
            return AddRecordResult(plan_item.record_id, "blocked", "PREFLIGHT_BLOCKED")
        try:
            self._collection.add(
                ids=[plan_item.record_id],
                documents=[plan_item.canonical_content["question"]],
                metadatas=[_metadata_for_item(plan_item)],
            )
        except Exception:  # noqa: BLE001
            raced = self.get_exact_records([plan_item.record_id])[0]
            if raced.status == "found":
                return AddRecordResult(
                    plan_item.record_id,
                    "existing_same" if _record_matches_item(raced.record, plan_item) else "existing_conflict",  # type: ignore[arg-type]
                )
            if raced.status == "malformed":
                return AddRecordResult(plan_item.record_id, "existing_conflict", raced.error_code)
            return AddRecordResult(plan_item.record_id, "storage_error", "ADD_FAILED")
        written = self.get_exact_records([plan_item.record_id])[0]
        if written.status != "found" or not _record_matches_item(written.record, plan_item):  # type: ignore[arg-type]
            return AddRecordResult(plan_item.record_id, "storage_error", "POST_ADD_VERIFICATION_FAILED")
        return AddRecordResult(plan_item.record_id, "created")

    def list_records_by_batch_digest(self, batch_content_sha256: str) -> BatchRecordResult:
        if not SHA256_PATTERN.fullmatch(batch_content_sha256):
            raise ChromaToolMemoryAdapterError("INVALID_BATCH_DIGEST", "批次摘要格式无效")
        try:
            raw = self._collection.get(
                where={"created_by_batch_content_sha256": batch_content_sha256}
            )
            returned_ids = raw.get("ids") if isinstance(raw, dict) else None
            if not isinstance(returned_ids, list):
                raise TypeError("invalid ids")
            records = _extract_aligned_records(raw, len(returned_ids))
        except Exception as error:  # noqa: BLE001
            raise ChromaToolMemoryAdapterError("STORAGE_ERROR", "批次精确查询失败") from error
        issues: list[AdapterIssue] = []
        for record in records:
            issues.extend(record.issues)
            if record.classification != "controlled_tool_record":
                issues.append(_issue("INVALID_BATCH_RECORD", record.storage_id, "批次结果包含非受控记录"))
            elif (record.metadata or {}).get("created_by_batch_content_sha256") != batch_content_sha256:
                issues.append(_issue("BATCH_FILTER_MISMATCH", record.storage_id, "creator digest 与过滤条件不一致"))
        return BatchRecordResult(batch_content_sha256, records, tuple(issues))

    def delete_exact_created_records(
        self,
        expected_records: Sequence[ExpectedCreatedRecord],
        expected_batch_content_sha256: str,
    ) -> DeleteRecordsResult:
        records = tuple(expected_records)
        ids = tuple(record.record_id for record in records)
        if (
            not records
            or len(ids) != len(set(ids))
            or not SHA256_PATTERN.fullmatch(expected_batch_content_sha256)
            or any(
                not isinstance(record, ExpectedCreatedRecord)
                or not RECORD_ID_PATTERN.fullmatch(record.record_id)
                or not SHA256_PATTERN.fullmatch(record.memory_content_sha256)
                for record in records
            )
        ):
            raise ChromaToolMemoryAdapterError("INVALID_DELETE_EXPECTATION", "删除预期格式无效")
        exact = self.get_exact_records(ids)
        decisions: list[DeleteRecordItemResult] = []
        for expected, result in zip(records, exact):
            if result.status == "missing":
                decisions.append(DeleteRecordItemResult(expected.record_id, "not_found"))
            elif result.status != "found" or result.record is None:
                decisions.append(DeleteRecordItemResult(expected.record_id, "storage_error"))
            elif (result.record.metadata or {}).get("created_by_batch_content_sha256") != expected_batch_content_sha256:
                decisions.append(DeleteRecordItemResult(expected.record_id, "ownership_mismatch"))
            elif result.record.memory_content_sha256 != expected.memory_content_sha256:
                decisions.append(DeleteRecordItemResult(expected.record_id, "content_mismatch"))
            else:
                decisions.append(DeleteRecordItemResult(expected.record_id, "not_deleted"))
        if any(item.status != "not_deleted" for item in decisions):
            return DeleteRecordsResult(tuple(decisions), False)
        try:
            self._collection.delete(ids=list(ids))
        except Exception:  # noqa: BLE001
            return DeleteRecordsResult(
                tuple(DeleteRecordItemResult(value, "storage_error") for value in ids), False
            )
        after = self.get_exact_records(ids)
        if any(result.status != "missing" for result in after):
            return DeleteRecordsResult(
                tuple(DeleteRecordItemResult(value, "storage_error") for value in ids), False
            )
        return DeleteRecordsResult(
            tuple(DeleteRecordItemResult(value, "deleted") for value in ids), True
        )


__all__ = [
    "AddRecordResult",
    "BatchRecordResult",
    "ChromaToolMemoryAdapter",
    "ChromaToolMemoryAdapterError",
    "DeleteRecordItemResult",
    "DeleteRecordsResult",
    "ExactRecordResult",
    "ExpectedCreatedRecord",
    "PlanPreflightResult",
    "StoredToolRecord",
    "ToolRecordInventory",
]
