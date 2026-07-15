"""从验证备份的全新 audit-copy 生成匿名 expected_tables 恢复提案。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.chroma_tool_memory_adapter import (  # noqa: E402
    ChromaToolMemoryAdapter,
)
from training.sop.legacy_expected_tables_recovery import (  # noqa: E402
    APPROVED_FORMAL_SOURCE_SHA256,
    APPROVED_VERIFIED_BACKUP_SHA256,
    LegacyExpectedTablesRecord,
    RecoverySourceFacts,
    analyze_record_sql,
    build_expected_tables_recovery_proposal,
    build_recovery_environment,
    classify_expected_tables,
    evaluate_calibration_gate,
)
from training.sop.storage_snapshot import (  # noqa: E402
    build_directory_manifest,
    create_verified_copy,
)
from tools.sql_guard import SQLGuard  # noqa: E402
from vanna.integrations.chromadb import ChromaAgentMemory  # noqa: E402


class OfflineInventoryEmbeddingFunction:
    """只为离线打开既有 collection 提供接口；本任务不执行向量检索。"""

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return [
            [byte / 255.0 for byte in hashlib.sha256(text.encode("utf-8")).digest()]
            for text in input
        ]

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)

    @staticmethod
    def name() -> str:
        return "legacy-expected-tables-offline-inventory"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]):
        del config
        return OfflineInventoryEmbeddingFunction()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_from_inventory(item: Any) -> LegacyExpectedTablesRecord:
    canonical = dict(item.canonical_content or {})
    compatibility = dict(item.compatibility_metadata or {})
    state, stored = classify_expected_tables(
        compatibility.get("expected_tables"),
        field_present="expected_tables" in compatibility,
    )
    args = canonical.get("args")
    canonical_sql_present = isinstance(args, dict) and "sql" in args
    sql = args.get("sql", "") if isinstance(args, dict) else ""
    return LegacyExpectedTablesRecord(
        legacy_storage_id=item.storage_id,
        target_record_id=item.derived_record_id or "",
        memory_content_sha256=item.memory_content_sha256 or "",
        question=str(canonical.get("question", "")),
        sql=sql if isinstance(sql, str) else "",
        stored_expected_tables=stored,
        expected_tables_state=state,
        tool_name=str(canonical.get("tool_name", "")),
        success=canonical.get("success") is True,
        canonical_sql_present=canonical_sql_present,
    )


def _close_memory(memory: Any) -> None:
    memory._executor.shutdown(wait=True)
    if memory._client is not None:
        memory._client._system.stop()
    memory._collection = None
    memory._client = None
    gc.collect()
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:  # noqa: BLE001
        pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-source", type=Path, required=True)
    parser.add_argument("--verified-backup", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--metadata-index",
        type=Path,
        default=ROOT / "agent_data" / "column_metadata_index.json",
    )
    parser.add_argument("--base-commit", required=True)
    return parser.parse_args()


def _failure_code(exc: BaseException) -> str:
    value = str(exc)
    if value and value.replace("_", "").isalnum() and value.upper() == value:
        return value
    return "AUDIT_EXECUTION_FAILED"


def _remove_audit_copy(path: Path, remover=shutil.rmtree) -> None:
    if not path.exists():
        return
    try:
        remover(path)
    except BaseException as exc:  # noqa: BLE001
        raise RuntimeError("AUDIT_COPY_CLEANUP_FAILED") from exc
    if path.exists():
        raise RuntimeError("AUDIT_COPY_CLEANUP_FAILED")


def run_audit(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError("OUTPUT_DIRECTORY_ALREADY_EXISTS")
    evidence_dir = output_dir / "evidence"
    audit_copy = output_dir / "audit-copy"
    evidence_dir.mkdir(parents=True)
    memory = None
    failure: BaseException | None = None
    result = None
    formal_before = backup_before = formal_after = backup_after = None
    metadata_before_sha = metadata_after_sha = ""
    audit_removed = False
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        formal_before = build_directory_manifest(args.formal_source)
        backup_before = build_directory_manifest(args.verified_backup)
        _write_json(evidence_dir / "formal-source-current.json", formal_before.to_dict())
        _write_json(evidence_dir / "verified-backup-current.json", backup_before.to_dict())
        if formal_before.content_sha256 != APPROVED_FORMAL_SOURCE_SHA256:
            raise RuntimeError("FORMAL_SOURCE_DIGEST_MISMATCH")
        if backup_before.content_sha256 != APPROVED_VERIFIED_BACKUP_SHA256:
            raise RuntimeError("VERIFIED_BACKUP_DIGEST_MISMATCH")

        metadata_index = args.metadata_index.resolve()
        metadata_before_sha = _sha256_file(metadata_index)
        metadata_size = metadata_index.stat().st_size
        _write_json(evidence_dir / "metadata-index-before.json", {"sha256": metadata_before_sha, "size": metadata_size})

        copy_result = create_verified_copy(args.verified_backup, audit_copy, ROOT)
        audit_before = copy_result.destination
        _write_json(evidence_dir / "audit-copy-before.json", audit_before.to_dict())
        if audit_before.content_sha256 != APPROVED_VERIFIED_BACKUP_SHA256:
            raise RuntimeError("AUDIT_COPY_DIGEST_MISMATCH")

        memory = ChromaAgentMemory(
            persist_directory=str(audit_copy),
            collection_name="tool_memories",
            embedding_function=OfflineInventoryEmbeddingFunction(),
        )
        adapter = ChromaToolMemoryAdapter(memory, isolated_root=output_dir)
        inventory = adapter.inventory_tool_records()
        records = tuple(
            _record_from_inventory(item)
            for item in inventory.records
            if item.classification == "legacy_tool_record"
        )

        inventory_summary = {
            "audit_inventory_sha256": inventory.inventory_sha256,
            "store_count": inventory.store_count_after,
            "classifications": dict(inventory.classifications),
            "duplicate_group_count": len(inventory.duplicate_existing_content),
            "content_conflict_count": len(inventory.content_address_conflicts),
            "legacy_id_mismatch_count": len(inventory.legacy_id_mismatches),
        }
        _write_json(
            evidence_dir / "audit-inventory-summary.json", inventory_summary
        )
        _close_memory(memory)
        memory = None
        audit_after = build_directory_manifest(audit_copy)
        _write_json(evidence_dir / "audit-copy-after.json", audit_after.to_dict())

        metadata_after_sha = _sha256_file(metadata_index)
        formal_after = build_directory_manifest(args.formal_source)
        backup_after = build_directory_manifest(args.verified_backup)
        _write_json(evidence_dir / "metadata-index-after.json", {"sha256": metadata_after_sha, "size": metadata_index.stat().st_size})
        _write_json(evidence_dir / "formal-source-final.json", formal_after.to_dict())
        _write_json(evidence_dir / "verified-backup-final.json", backup_after.to_dict())

        sql_guard_sha = _sha256_file(ROOT / "tools" / "sql_guard.py")
        batch_validator_sha = _sha256_file(ROOT / "training" / "sop" / "batch_validator.py")
        recovery_module_sha = _sha256_file(ROOT / "training" / "sop" / "legacy_expected_tables_recovery.py")
        audit_entry_sha = _sha256_file(Path(__file__).resolve())
        facts = RecoverySourceFacts(
            formal_source_sha256_before=formal_before.content_sha256,
            formal_source_sha256_after=formal_after.content_sha256,
            verified_backup_sha256_before=backup_before.content_sha256,
            verified_backup_sha256_after=backup_after.content_sha256,
            audit_copy_sha256_before_open=audit_before.content_sha256,
            audit_copy_sha256_after_open=audit_after.content_sha256,
            audit_inventory_sha256=inventory.inventory_sha256,
            metadata_index_sha256_before=metadata_before_sha,
            metadata_index_sha256_after=metadata_after_sha,
            sql_guard_source_sha256=sql_guard_sha,
            batch_validator_source_sha256=batch_validator_sha,
            recovery_module_source_sha256=recovery_module_sha,
            audit_entry_source_sha256=audit_entry_sha,
            store_count=inventory.store_count_after,
            legacy_record_count=inventory.classifications["legacy_tool_record"],
            text_memory_count=inventory.classifications["text_memory"],
            controlled_record_count=inventory.classifications["controlled_tool_record"],
            malformed_record_count=inventory.classifications["malformed_record"],
            unknown_record_count=inventory.classifications["unknown_record"],
            duplicate_group_count=len(inventory.duplicate_existing_content),
            content_conflict_count=len(inventory.content_address_conflicts),
            legacy_id_mismatch_count=len(inventory.legacy_id_mismatches),
        )
        environment = build_recovery_environment(
            base_commit=args.base_commit,
            audit_inventory_sha256=inventory.inventory_sha256,
            metadata_index_sha256=metadata_before_sha,
            sql_guard_source_sha256=sql_guard_sha,
            batch_validator_source_sha256=batch_validator_sha,
            recovery_module_source_sha256=recovery_module_sha,
            audit_entry_source_sha256=audit_entry_sha,
        )
        guard = SQLGuard(index_path=metadata_index)
        calibration_records = tuple(
            record for record in records if record.expected_tables_state == "valid"
        )
        calibration_analyses = tuple(
            analyze_record_sql(record, guard) for record in calibration_records
        )

        calibration_passed, _, _ = evaluate_calibration_gate(
            records, calibration_analyses, facts, environment
        )
        recovery_analyses = ()
        if calibration_passed:
            recovery_records = tuple(
                record
                for record in records
                if record.expected_tables_state == "missing"
            )
            recovery_analyses = tuple(
                analyze_record_sql(record, guard) for record in recovery_records
            )
        analyses = calibration_analyses + recovery_analyses
        result = build_expected_tables_recovery_proposal(records, analyses, facts, environment)

        calibration_summary = {
        "record_count": result.calibration_record_count,
        "match_count": result.calibration_match_count,
        "mismatch_count": result.calibration_mismatch_count,
        "blocked_count": result.calibration_blocked_count,
        "result": "PASS" if result.calibration_match_count == 48 else "FAIL",
    }
        proposal_summary = {
        **result.to_public_dict(),
        "calibration_items": [],
        "recovery_items": [],
        "environment": environment.to_public_dict(),
        "metadata_index_size": metadata_size,
        "sql_guard_source_sha256": sql_guard_sha,
        "batch_validator_source_sha256": batch_validator_sha,
        "auto_approval_present": False,
    }
        blockers = [
        {
            "legacy_storage_id": item.legacy_storage_id,
            "target_record_id": item.target_record_id,
            "issue_codes": list(item.issue_codes),
        }
        for item in (*result.calibration_items, *result.recovery_items)
        if item.issue_codes
    ]
        if result.issue_codes:
            blockers.append({"issue_codes": list(result.issue_codes)})
        _write_json(evidence_dir / "calibration-summary.json", calibration_summary)
        _write_json(
        evidence_dir / "calibration-items.json",
        [item.to_public_dict() for item in result.calibration_items],
    )
        _write_json(evidence_dir / "recovery-proposal-summary.json", proposal_summary)
        _write_json(
        evidence_dir / "recovery-proposal-items.json",
        [item.to_public_dict() for item in result.recovery_items],
    )
        _write_json(evidence_dir / "recovery-blockers.json", blockers)
    except BaseException as exc:  # noqa: BLE001 - 失败证据只记录稳定代码和类型
        failure = exc
    finally:
        if memory is not None:
            try:
                _close_memory(memory)
            except BaseException as exc:  # noqa: BLE001
                failure = failure or exc
        if audit_copy.exists():
            try:
                _remove_audit_copy(audit_copy)
            except BaseException:  # noqa: BLE001
                failure = RuntimeError("AUDIT_COPY_CLEANUP_FAILED")
        audit_removed = not audit_copy.exists()

    try:
        formal_final = build_directory_manifest(args.formal_source).content_sha256
        backup_final = build_directory_manifest(args.verified_backup).content_sha256
        metadata_final = _sha256_file(args.metadata_index.resolve())
    except BaseException as exc:  # noqa: BLE001
        failure = failure or exc
        formal_final = backup_final = metadata_final = ""
    if not audit_removed:
        failure = RuntimeError("AUDIT_COPY_CLEANUP_FAILED")
    unchanged = (
        formal_before is not None and formal_final == formal_before.content_sha256
        and backup_before is not None and backup_final == backup_before.content_sha256
        and bool(metadata_before_sha) and metadata_final == metadata_before_sha
    )
    if result is None or not result.proposal_ready or not unchanged:
        failure = failure or RuntimeError("AUDIT_COMPLETION_GATE_FAILED")
    if failure is not None:
        failure_summary = {
            "failure_code": _failure_code(failure),
            "exception_type": type(failure).__name__,
            "audit_copy_removed": audit_removed,
            "formal_source_final_sha256": formal_final,
            "verified_backup_final_sha256": backup_final,
            "metadata_index_final_sha256": metadata_final,
        }
        try:
            _write_json(evidence_dir / "audit-failure-summary.json", failure_summary)
        except BaseException:  # noqa: BLE001
            pass
        print(json.dumps(failure_summary, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(proposal_summary, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    return run_audit(_parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
