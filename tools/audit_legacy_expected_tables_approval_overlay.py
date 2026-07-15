"""生成 M2B 人工批准、expected_tables overlay 与只读迁移 bundle。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.chroma_tool_memory_adapter import ChromaToolMemoryAdapter
from training.sop.legacy_expected_tables_approval_overlay import (
    APPROVED_FORMAL_SOURCE_SHA256, APPROVED_RECOVERY_ENVIRONMENT_SHA256,
    APPROVED_RECOVERY_PROPOSAL_SHA256, MIGRATION_BATCH_ID,
    apply_expected_tables_overlay, build_expected_tables_overlay,
    build_human_recovery_approval, build_m2b_environment,
    build_migration_bundle_with_approved_overlay,
)
from training.sop.legacy_expected_tables_recovery import (
    CalibrationItem, RecoveryEnvironment, RecoveryProposalItem,
    recompute_recovery_environment_sha256,
)
from training.sop.legacy_tool_memory_migration_plan import (
    LegacyToolRecordSnapshot, TextMemoryBaselineRecord,
    build_legacy_tool_memory_migration_contract,
)
from training.sop.storage_snapshot import build_directory_manifest, create_verified_copy
from tools.audit_legacy_expected_tables_recovery import (
    OfflineInventoryEmbeddingFunction, _close_memory,
)
from vanna.integrations.chromadb import ChromaAgentMemory

REQUIRED_R1_FILES = (
    "formal-source-current.json", "formal-source-final.json",
    "verified-backup-current.json", "verified-backup-final.json",
    "audit-copy-before.json", "audit-copy-after.json", "audit-inventory-summary.json",
    "metadata-index-before.json", "metadata-index-after.json",
    "calibration-summary.json", "calibration-items.json",
    "recovery-proposal-summary.json", "recovery-proposal-items.json",
    "recovery-blockers.json",
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_r1_evidence(evidence: Path) -> dict[str, Any]:
    if any(not (evidence / name).is_file() for name in REQUIRED_R1_FILES):
        raise RuntimeError("R1_ARCHIVE_FILE_MISSING")
    summary = _load(evidence / "recovery-proposal-summary.json")
    calibration = _load(evidence / "calibration-items.json")
    recovery = _load(evidence / "recovery-proposal-items.json")
    blockers = _load(evidence / "recovery-blockers.json")
    environment = RecoveryEnvironment(**summary["environment"])
    if recompute_recovery_environment_sha256(environment) != environment.recovery_environment_sha256:
        raise RuntimeError("R1_ENVIRONMENT_SHA256_MISMATCH")
    for row in calibration:
        item = CalibrationItem(**{**row, "stored_expected_tables": tuple(row["stored_expected_tables"]), "derived_expected_tables": tuple(row["derived_expected_tables"]), "issue_codes": tuple(row["issue_codes"])})
        material = asdict(item); digest = material.pop("calibration_item_sha256")
        if _sha(material) != digest:
            raise RuntimeError("R1_CALIBRATION_ITEM_SHA256_MISMATCH")
    for row in recovery:
        item = RecoveryProposalItem(**{**row, "proposed_expected_tables": tuple(row["proposed_expected_tables"]), "issue_codes": tuple(row["issue_codes"])})
        material = asdict(item); digest = material.pop("recovery_item_sha256")
        if _sha(material) != digest:
            raise RuntimeError("R1_RECOVERY_ITEM_SHA256_MISMATCH")
    fixed = {
        "state": "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL", "calibration_record_count": 48,
        "calibration_match_count": 48, "calibration_mismatch_count": 0,
        "calibration_blocked_count": 0, "recovery_candidate_count": 16,
        "recovery_proposed_count": 16, "recovery_blocked_count": 0,
        "issue_codes": [], "auto_approval_present": False,
    }
    if any(summary.get(key) != value for key, value in fixed.items()) or blockers:
        raise RuntimeError("R1_ARCHIVE_INVARIANT_MISMATCH")
    material = {
        "recovery_environment_sha256": summary["recovery_environment_sha256"],
        "state": summary["state"], "calibration_items": calibration,
        "recovery_items": recovery, "issue_codes": summary["issue_codes"],
        **{key: summary[key] for key in fixed if key not in ("state", "issue_codes", "auto_approval_present")},
    }
    if _sha(material) != summary["recovery_proposal_sha256"]:
        raise RuntimeError("R1_PROPOSAL_SHA256_MISMATCH")
    if summary["recovery_environment_sha256"] != APPROVED_RECOVERY_ENVIRONMENT_SHA256 or summary["recovery_proposal_sha256"] != APPROVED_RECOVERY_PROPOSAL_SHA256:
        raise RuntimeError("R1_APPROVED_DIGEST_MISMATCH")
    return {"environment_sha256": summary["recovery_environment_sha256"], "proposal_sha256": summary["recovery_proposal_sha256"], "items": recovery}


def _record_snapshots(inventory: Any) -> tuple[tuple[LegacyToolRecordSnapshot, ...], tuple[TextMemoryBaselineRecord, ...]]:
    legacy = []
    texts = []
    for item in inventory.records:
        if item.classification == "legacy_tool_record":
            legacy.append(LegacyToolRecordSnapshot(
                legacy_storage_id=item.storage_id, document=item.document or "",
                raw_metadata=dict(item.metadata or {}), canonical_content=dict(item.canonical_content or {}),
                memory_content_sha256=item.memory_content_sha256 or "", target_record_id=item.derived_record_id or "",
                compatibility_metadata=dict(item.compatibility_metadata or {}),
            ))
        elif item.classification == "text_memory":
            texts.append(TextMemoryBaselineRecord(item.storage_id, hashlib.sha256((item.document or "").encode()).hexdigest(), _sha(dict(item.metadata or {}))))
    return tuple(legacy), tuple(texts)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-source", type=Path, required=True)
    parser.add_argument("--verified-backup", type=Path, required=True)
    parser.add_argument("--metadata-index", type=Path, required=True)
    parser.add_argument("--r1-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def run_audit(args: argparse.Namespace, dependencies: dict[str, Any] | None = None) -> int:
    deps = dependencies or {}
    output = args.output_dir.resolve()
    evidence = output / "evidence"
    migration_copy = output / "migration-audit-copy"
    fresh_dir = output / "recovery-revalidation"
    if output.exists():
        raise RuntimeError("OUTPUT_DIRECTORY_ALREADY_EXISTS")
    evidence.mkdir(parents=True)
    memory = None
    failure = None
    formal_before = backup_before = None
    metadata_before = _sha_file(args.metadata_index)
    try:
        archive = verify_r1_evidence(args.r1_archive)
        _write(evidence / "r1-archive-verification.json", {"valid": True, **{key: value for key, value in archive.items() if key != "items"}, "item_count": len(archive["items"])})
        formal_before = build_directory_manifest(args.formal_source)
        backup_before = build_directory_manifest(args.verified_backup)
        if formal_before.content_sha256 != APPROVED_FORMAL_SOURCE_SHA256 or backup_before.content_sha256 != APPROVED_FORMAL_SOURCE_SHA256:
            raise RuntimeError("SOURCE_DIGEST_MISMATCH")
        command = [sys.executable, str(ROOT / "tools" / "audit_legacy_expected_tables_recovery.py"), "--formal-source", str(args.formal_source), "--verified-backup", str(args.verified_backup), "--output-dir", str(fresh_dir), "--metadata-index", str(args.metadata_index), "--base-commit", "c5b88b1ae491850f18536700d29635aa255b6e2a"]
        runner = deps.get("fresh_runner", subprocess.run)
        completed = runner(command, cwd=ROOT, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError("FRESH_RECOVERY_REVALIDATION_FAILED")
        fresh = verify_r1_evidence(fresh_dir / "evidence")
        if fresh != archive:
            raise RuntimeError("FRESH_RECOVERY_REVALIDATION_MISMATCH")
        _write(evidence / "fresh-recovery-revalidation.json", {"valid": True, "environment_sha256": fresh["environment_sha256"], "proposal_sha256": fresh["proposal_sha256"], "items_match_archive": True})

        copy_builder = deps.get("copy_builder", create_verified_copy)
        copy_result = copy_builder(args.verified_backup, migration_copy, ROOT)
        if copy_result.destination.content_sha256 != APPROVED_FORMAL_SOURCE_SHA256:
            raise RuntimeError("MIGRATION_AUDIT_COPY_DIGEST_MISMATCH")
        memory_factory = deps.get("memory_factory", ChromaAgentMemory)
        memory = memory_factory(persist_directory=str(migration_copy), collection_name="tool_memories", embedding_function=OfflineInventoryEmbeddingFunction())
        inventory = ChromaToolMemoryAdapter(memory, isolated_root=output).inventory_tool_records()
        legacy, texts = _record_snapshots(inventory)
        _close_memory(memory); memory = None
        copy_after = build_directory_manifest(migration_copy)
        if len(legacy) != 64 or len(texts) != 8:
            raise RuntimeError("MIGRATION_INVENTORY_COUNT_MISMATCH")
        approval = build_human_recovery_approval(archive["items"])
        source_hashes = {
            "recovery_module_source_sha256": _sha_file(ROOT / "training/sop/legacy_expected_tables_recovery.py"),
            "recovery_audit_source_sha256": _sha_file(ROOT / "tools/audit_legacy_expected_tables_recovery.py"),
            "migration_contract_module_source_sha256": _sha_file(ROOT / "training/sop/legacy_tool_memory_migration_plan.py"),
            "overlay_module_source_sha256": _sha_file(ROOT / "training/sop/legacy_expected_tables_approval_overlay.py"),
            "m2b_audit_source_sha256": _sha_file(Path(__file__).resolve()),
        }
        environment = build_m2b_environment(
            formal_source_sha256=formal_before.content_sha256, verified_backup_sha256=backup_before.content_sha256,
            source_inventory_sha256=inventory.inventory_sha256,
            recovery_environment_sha256=archive["environment_sha256"], recovery_proposal_sha256=archive["proposal_sha256"],
            approval_evidence_sha256=approval.approval_evidence_sha256, **source_hashes,
        )
        overlay = build_expected_tables_overlay(archive["items"], approval, environment)
        application = apply_expected_tables_overlay(legacy, overlay)
        direct = build_legacy_tool_memory_migration_contract(legacy, texts, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256, expected_legacy_count=64, text_memory_count=8, existing_targets={})
        bundle = build_migration_bundle_with_approved_overlay(legacy, texts, recovery_archive=archive, fresh_revalidation=fresh, approval=approval, environment=environment, overlay=overlay, source_inventory_sha256=inventory.inventory_sha256, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256, existing_targets={})
        if direct.executable or not bundle.bundle_ready:
            raise RuntimeError("MIGRATION_CONTRACT_GATE_MISMATCH")
        contract = bundle.migration_contract
        _write(evidence / "human-approval-evidence.json", asdict(approval))
        _write(evidence / "m2b-environment.json", asdict(environment))
        _write(evidence / "expected-tables-overlay.json", asdict(overlay))
        _write(evidence / "overlay-application-summary.json", {key: value for key, value in asdict(application).items() if key not in ("snapshots", "item_evidence")})
        _write(evidence / "overlay-application-items.json", [asdict(item) for item in application.item_evidence])
        _write(evidence / "formal-source-before.json", formal_before.to_dict())
        _write(evidence / "verified-backup-before.json", backup_before.to_dict())
        _write(evidence / "migration-audit-copy-before.json", copy_result.destination.to_dict())
        _write(evidence / "migration-audit-copy-after.json", copy_after.to_dict())
        _write(evidence / "migration-inventory-summary.json", {"source_inventory_sha256": inventory.inventory_sha256, "store_count": inventory.store_count_after, "classifications": dict(inventory.classifications)})
        _write(evidence / "migration-contract-summary.json", {"migration_schema_version": contract.migration_schema_version, "migration_batch_id": contract.migration_batch_id, "migration_source_content_sha256": contract.migration_source_content_sha256, "migration_contract_sha256": contract.migration_contract_sha256, "item_count": len(contract.items), "create_count": len(contract.phase_a_create_target_ids), "resume_count": len(contract.phase_a_resume_target_ids), "rollback_count": len(contract.phase_a_rollback_candidate_ids), "delete_count": len(contract.proposed_legacy_delete_ids), "issue_codes": [issue.code for issue in contract.issues], "executable": contract.executable, "recovered_target_count": 16, "ordinary_target_count": 48})
        overlay_by_id = {item.target_record_id: item for item in overlay.items}
        _write(evidence / "migration-contract-items.json", [{"migration_sample_id": item.migration_sample_id, "legacy_storage_id": item.legacy_storage_id, "target_record_id": item.target_record_id, "memory_content_sha256": item.memory_content_sha256, "target_document_sha256": item.target_document_sha256, "target_top_level_metadata_sha256": item.target_top_level_metadata_sha256, "target_compatibility_metadata_sha256": item.target_compatibility_metadata_sha256, "migration_item_sha256": item.migration_item_sha256, "phase_a_action": item.phase_a_action, "issue_codes": [issue.code for issue in item.issues], "executable": item.executable, "expected_tables": item.target_compatibility_metadata["expected_tables"], "overlay_applied": item.target_record_id in overlay_by_id, "overlay_item_sha256": overlay_by_id[item.target_record_id].overlay_item_sha256 if item.target_record_id in overlay_by_id else ""} for item in contract.items])
        _write(evidence / "migration-bundle-summary.json", {key: value for key, value in asdict(bundle).items() if key != "migration_contract"})
        _write(evidence / "m2b-blockers.json", [])
    except BaseException as exc:  # noqa: BLE001
        failure = exc
    finally:
        if memory is not None:
            try: _close_memory(memory)
            except BaseException: pass
        if migration_copy.exists():
            try: shutil.rmtree(migration_copy)
            except BaseException: failure = RuntimeError("AUDIT_COPY_CLEANUP_FAILED")
    formal_after = build_directory_manifest(args.formal_source)
    backup_after = build_directory_manifest(args.verified_backup)
    metadata_after = _sha_file(args.metadata_index)
    _write(evidence / "formal-source-after.json", formal_after.to_dict())
    _write(evidence / "verified-backup-after.json", backup_after.to_dict())
    if failure or migration_copy.exists() or formal_before is None or formal_after.content_sha256 != formal_before.content_sha256 or backup_after.content_sha256 != backup_before.content_sha256 or metadata_after != metadata_before:
        code = "AUDIT_COPY_CLEANUP_FAILED" if migration_copy.exists() else (str(failure) if failure and str(failure).isupper() else "M2B_AUDIT_FAILED")
        _write(evidence / "m2b-blockers.json", [{"failure_code": code, "exception_type": type(failure).__name__ if failure else "RuntimeError"}])
        print(json.dumps({"state": "SOURCE_BLOCKED", "failure_code": code}, sort_keys=True))
        return 1
    print(json.dumps({"state": bundle.state, "bundle_sha256": bundle.bundle_sha256, "migration_contract_sha256": contract.migration_contract_sha256}, sort_keys=True))
    return 0


def main() -> int:
    return run_audit(_args())


if __name__ == "__main__":
    raise SystemExit(main())
