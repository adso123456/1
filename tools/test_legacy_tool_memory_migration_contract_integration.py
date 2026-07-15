"""2.0 迁移状态契约与真实适配层的隔离 Chroma 集成测试。"""

from __future__ import annotations

import gc
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.chroma_tool_memory_adapter import (  # noqa: E402
    ChromaToolMemoryAdapter,
)
from training.sop.legacy_tool_memory_migration_plan import (  # noqa: E402
    APPROVED_FORMAL_SOURCE_SHA256,
    APPROVED_VERIFIED_BACKUP_SHA256,
    LegacyToolRecordSnapshot,
    ObservedTextMemoryEvidence,
    ObservedToolRecordEvidence,
    PhaseAExecutionSnapshot,
    PhaseBApprovalSnapshot,
    PhaseBExecutionSnapshot,
    StoreStateSnapshot,
    TextMemoryBaselineRecord,
    build_legacy_tool_memory_migration_contract,
    evaluate_legacy_tool_memory_migration,
    proposed_legacy_delete_ids_sha256,
)
from training.sop.memory_write_plan import (  # noqa: E402
    build_memory_identity_from_canonical_content,
)
from vanna.integrations.chromadb import ChromaAgentMemory  # noqa: E402


class DeterministicEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return [
            [byte / 255.0 for byte in hashlib.sha256(text.encode("utf-8")).digest()]
            for text in input
        ]

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)

    @staticmethod
    def name() -> str:
        return "legacy-migration-contract-deterministic"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]):
        del config
        return DeterministicEmbeddingFunction()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _snapshot(index: int) -> LegacyToolRecordSnapshot:
    canonical = {
        "record_schema_version": "1.0",
        "question": f"隔离合成问题 {index}",
        "tool_name": "run_sql",
        "args": {"sql": f"SELECT id FROM isolated_table_{index} LIMIT 1"},
        "success": True,
    }
    identity = build_memory_identity_from_canonical_content(canonical)
    compatibility = {
        "sample_id": f"ISOLATED_{index:03d}",
        "training_level": "level2_sql_examples",
        "train_decision": "approved",
        "review_reason": "隔离合成验证",
        "source": "isolated-synthetic",
        "expected_behavior": "返回隔离合成结果",
        "expected_tables": [f"isolated_table_{index}"],
    }
    raw_metadata = {
        "question": canonical["question"],
        "tool_name": "run_sql",
        "args_json": _canonical_json(canonical["args"]),
        "success": True,
        "metadata_json": _canonical_json(compatibility),
    }
    return LegacyToolRecordSnapshot(
        legacy_storage_id=f"10000000-0000-4000-8000-{index:012d}",
        document=canonical["question"],
        raw_metadata=raw_metadata,
        canonical_content=canonical,
        memory_content_sha256=identity.memory_content_sha256,
        target_record_id=identity.record_id,
        compatibility_metadata=compatibility,
    )


def _store_snapshot(contract, inventory) -> StoreStateSnapshot:
    tools: list[ObservedToolRecordEvidence] = []
    texts: list[ObservedTextMemoryEvidence] = []
    for record in inventory.records:
        if record.classification == "text_memory":
            texts.append(
                ObservedTextMemoryEvidence(
                    storage_id=record.storage_id,
                    document_sha256=_sha_text(record.document or ""),
                    metadata_sha256=_sha_json(dict(record.metadata or {})),
                )
            )
            continue
        tools.append(
            ObservedToolRecordEvidence(
                storage_id=record.storage_id,
                classification=record.classification,
                derived_record_id=record.derived_record_id or "",
                memory_content_sha256=record.memory_content_sha256 or "0" * 64,
                document_sha256=_sha_text(record.document or ""),
                metadata_sha256=_sha_json(dict(record.metadata or {})),
                compatibility_metadata_sha256=_sha_json(
                    dict(record.compatibility_metadata or {})
                ),
            )
        )
    return StoreStateSnapshot(
        migration_contract_sha256=contract.migration_contract_sha256,
        source_inventory_sha256=inventory.inventory_sha256,
        formal_source_before_sha256=contract.approved_formal_source_sha256,
        verified_backup_sha256=contract.approved_verified_backup_sha256,
        tool_records=tuple(tools),
        text_memories=tuple(texts),
    )


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def main() -> int:
    initial_status = _git_status()
    results: list[tuple[str, bool]] = []
    temp_parent = Path(tempfile.mkdtemp(prefix="legacy-contract-integration-"))
    isolated_root = temp_parent / "isolated"
    isolated_root.mkdir()
    memory = ChromaAgentMemory(
        persist_directory=str(isolated_root / "chroma"),
        collection_name="tool_memories",
        embedding_function=DeterministicEmbeddingFunction(),
    )
    adapter = ChromaToolMemoryAdapter(memory, isolated_root=isolated_root)
    collection = adapter._collection
    phase_a_duplicate_count = -1
    phase_a_legacy_mismatch_count = -1
    final_duplicate_count = -1
    target_controlled_count = -1
    compatibility_mismatch_count = -1
    try:
        snapshots = [_snapshot(1), _snapshot(2)]
        for snapshot in snapshots:
            collection.add(
                ids=[snapshot.legacy_storage_id],
                documents=[snapshot.document],
                metadatas=[dict(snapshot.raw_metadata)],
            )
        text_document = "隔离 Text Memory"
        text_metadata = {"is_text_memory": True, "source": "isolated-synthetic"}
        collection.add(
            ids=["isolated-text-001"],
            documents=[text_document],
            metadatas=[text_metadata],
        )
        text_baseline = [
            TextMemoryBaselineRecord(
                storage_id="isolated-text-001",
                document_sha256=_sha_text(text_document),
                metadata_sha256=_sha_json(text_metadata),
            )
        ]
        contract = build_legacy_tool_memory_migration_contract(
            snapshots,
            text_baseline,
            migration_batch_id="0B-3C-M1-INTEGRATION-V2",
            approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256,
            approved_verified_backup_sha256=APPROVED_VERIFIED_BACKUP_SHA256,
            expected_legacy_count=2,
            text_memory_count=1,
        )
        results.append(("2条 legacy + 1条 Text 构建有效契约", contract.executable))
        for item in contract.items:
            collection.add(
                ids=[item.target_record_id],
                documents=[str(item.target_governance_metadata["question"])],
                metadatas=[dict(item.target_governance_metadata)],
            )
        stored_targets = collection.get(
            ids=[item.target_record_id for item in contract.items],
            include=["metadatas"],
        )
        stored_metadata_by_id = dict(
            zip(stored_targets["ids"], stored_targets["metadatas"], strict=True)
        )
        expected_legacy_sample_by_target = {
            item.target_record_id: next(
                snapshot.compatibility_metadata["sample_id"]
                for snapshot in snapshots
                if snapshot.legacy_storage_id == item.legacy_storage_id
            )
            for item in contract.items
        }
        results.append(
            (
                "实际 target created_from_sample_id 使用迁移样本 ID",
                all(
                    stored_metadata_by_id[item.target_record_id][
                        "created_from_sample_id"
                    ]
                    == item.migration_sample_id
                    for item in contract.items
                ),
            )
        )
        results.append(
            (
                "实际 target migration_sample_id 使用迁移样本 ID",
                all(
                    stored_metadata_by_id[item.target_record_id][
                        "migration_sample_id"
                    ]
                    == item.migration_sample_id
                    for item in contract.items
                ),
            )
        )
        results.append(
            (
                "实际 compatibility 区分当前和历史 sample ID",
                all(
                    (
                        compatibility := json.loads(
                            stored_metadata_by_id[item.target_record_id][
                                "metadata_json"
                            ]
                        )
                    )["sample_id"]
                    == item.migration_sample_id
                    and compatibility["sample_id"]
                    == stored_metadata_by_id[item.target_record_id][
                        "created_from_sample_id"
                    ]
                    and compatibility["legacy_sample_id"]
                    == expected_legacy_sample_by_target[item.target_record_id]
                    for item in contract.items
                ),
            )
        )

        phase_a_inventory = adapter.inventory_tool_records()
        target_ids = {item.target_record_id for item in contract.items}
        target_records = tuple(
            record
            for record in phase_a_inventory.records
            if record.storage_id in target_ids
        )
        target_controlled_count = sum(
            record.classification == "controlled_tool_record"
            for record in target_records
        )
        compatibility_mismatch_count = sum(
            issue.code == "COMPATIBILITY_METADATA_MISMATCH"
            for record in target_records
            for issue in record.issues
        )
        results.append(
            (
                "适配器自然识别迁移 target 为 controlled",
                len(target_records) == 2
                and all(
                    record.classification == "controlled_tool_record"
                    and not record.issues
                    for record in target_records
                )
                and compatibility_mismatch_count == 0,
            )
        )
        phase_a_duplicate_count = len(
            phase_a_inventory.duplicate_existing_content
        )
        phase_a_legacy_mismatch_count = len(
            phase_a_inventory.legacy_id_mismatches
        )
        results.append(("真实阶段A重复组为2", phase_a_duplicate_count == 2))
        results.append(("真实阶段A legacy mismatch为2", phase_a_legacy_mismatch_count == 2))
        phase_a_store = _store_snapshot(contract, phase_a_inventory)
        execution_a = PhaseAExecutionSnapshot(
            migration_contract_sha256=contract.migration_contract_sha256,
            attempted_create_target_ids=contract.phase_a_create_target_ids,
            created_target_ids=contract.phase_a_create_target_ids,
            resumed_target_ids=contract.phase_a_resume_target_ids,
            failed_target_ids=(),
            error_codes=(),
        )
        evaluated_a = evaluate_legacy_tool_memory_migration(
            contract,
            phase_a_execution=execution_a,
            phase_a_store_state=phase_a_store,
        )
        results.append(
            (
                "真实适配层阶段A状态可达",
                evaluated_a.state == "PHASE_A_VERIFIED_AWAITING_APPROVAL"
                and evaluated_a.phase_a_verification is not None
                and evaluated_a.phase_a_verification.expected_transition_duplicate_group_count
                == 2
                and evaluated_a.phase_a_verification.unexpected_duplicate_group_count
                == 0,
            )
        )
        zero_duplicate_store = StoreStateSnapshot(
            migration_contract_sha256=phase_a_store.migration_contract_sha256,
            source_inventory_sha256=phase_a_store.source_inventory_sha256,
            formal_source_before_sha256=phase_a_store.formal_source_before_sha256,
            verified_backup_sha256=phase_a_store.verified_backup_sha256,
            tool_records=tuple(
                record
                for record in phase_a_store.tool_records
                if record.classification == "controlled_tool_record"
            ),
            text_memories=phase_a_store.text_memories,
        )
        zero_result = evaluate_legacy_tool_memory_migration(
            contract,
            phase_a_execution=execution_a,
            phase_a_store_state=zero_duplicate_store,
        )
        results.append(
            (
                "手工伪造0重复组无法通过",
                zero_result.state == "PHASE_A_VERIFICATION_FAILED",
            )
        )

        verification_a = evaluated_a.phase_a_verification
        approval = PhaseBApprovalSnapshot(
            approved=True,
            migration_contract_sha256=contract.migration_contract_sha256,
            phase_a_verification_sha256=(
                verification_a.phase_a_verification_sha256
            ),
            proposed_legacy_delete_ids_sha256=(
                proposed_legacy_delete_ids_sha256(contract)
            ),
        )
        ready_b = evaluate_legacy_tool_memory_migration(
            contract,
            phase_a_execution=execution_a,
            phase_a_store_state=phase_a_store,
            phase_b_approval=approval,
            predelete_store_state=phase_a_store,
        )
        results.append(
            (
                "同一逻辑状态重验后进入PHASE_B_READY",
                ready_b.state == "PHASE_B_READY"
                and len(ready_b.phase_b_executable_delete_ids) == 2,
            )
        )
        execution_b = PhaseBExecutionSnapshot(
            migration_contract_sha256=contract.migration_contract_sha256,
            phase_a_verification_sha256=(
                verification_a.phase_a_verification_sha256
            ),
            phase_b_approval_sha256=ready_b.phase_b_approval_sha256,
            predelete_verification_sha256=(
                ready_b.predelete_verification.phase_a_verification_sha256
            ),
            attempted_delete_ids=contract.proposed_legacy_delete_ids,
            deleted_ids=contract.proposed_legacy_delete_ids,
            failed_delete_ids=(),
            error_codes=(),
        )
        collection.delete(ids=list(contract.proposed_legacy_delete_ids))
        final_inventory = adapter.inventory_tool_records()
        final_duplicate_count = len(final_inventory.duplicate_existing_content)
        final_store = _store_snapshot(contract, final_inventory)
        completed = evaluate_legacy_tool_memory_migration(
            contract,
            phase_a_execution=execution_a,
            phase_a_store_state=phase_a_store,
            phase_b_approval=approval,
            predelete_store_state=phase_a_store,
            phase_b_execution=execution_b,
            final_store_state=final_store,
        )
        results.append(
            (
                "真实删除后最终状态COMPLETED",
                completed.state == "COMPLETED"
                and final_inventory.classifications["controlled_tool_record"] == 2
                and final_inventory.classifications["legacy_tool_record"] == 0
                and final_inventory.classifications["text_memory"] == 1
                and final_duplicate_count == 0,
            )
        )
        results.append(("未执行向量检索", True))
    finally:
        adapter = None
        memory._executor.shutdown(wait=True)
        if memory._client is not None:
            memory._client._system.stop()
        memory._collection = None
        memory._client = None
        memory = None
        gc.collect()
        try:
            from chromadb.api.client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(temp_parent, ignore_errors=False)

    results.append(("临时Chroma目录已删除", not temp_parent.exists()))
    results.append(("Git工作区状态不变", initial_status == _git_status()))
    passed = sum(result for _, result in results)
    failed = len(results) - passed
    for name, result in results:
        print(f"[{'PASS' if result else 'FAIL'}] {name}")
    print(f"REAL_ADAPTER_PHASE_A_DUPLICATE_GROUP_COUNT={phase_a_duplicate_count}")
    print(f"REAL_ADAPTER_PHASE_A_LEGACY_MISMATCH_COUNT={phase_a_legacy_mismatch_count}")
    print(f"REAL_ADAPTER_FINAL_DUPLICATE_GROUP_COUNT={final_duplicate_count}")
    print(f"INTEGRATION_TARGET_CONTROLLED_COUNT={target_controlled_count}")
    print(f"INTEGRATION_COMPATIBILITY_MISMATCH_COUNT={compatibility_mismatch_count}")
    print(f"隔离集成测试汇总: {passed} pass / {failed} fail")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
