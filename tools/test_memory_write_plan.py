"""0B-3B run_sql Tool Memory 确定性写入计划合成测试。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import (
    ExistingRecordSnapshot,
    ExecutionLedgerEntry,
    MemoryWritePlanError,
    build_memory_identity_from_canonical_content,
    build_memory_write_plan,
)


FIXTURE = PROJECT_ROOT / "tools" / "fixtures" / "training_sop" / "valid_batch.json"


class FakeGuard:
    """只为合成批次返回 SQL 中出现的表名，不连接任何外部系统。"""

    def validate(self, sql: str, query: str) -> SimpleNamespace:
        del query
        tables = [
            match.split(".")[-1].strip('"').lower()
            for match in re.findall(
                r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", sql, re.I
            )
        ]
        return SimpleNamespace(
            passed=True,
            severity="ok",
            reason="synthetic guard passed",
            used_tables=sorted(set(tables)),
        )


def load_batch() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def validated_digest(batch: dict[str, Any]) -> str:
    result = validate_training_batch(batch, sql_guard=FakeGuard())
    if not result.valid or not result.batch_content_sha256:
        raise AssertionError(result.to_dict())
    return result.batch_content_sha256


def make_plan(
    batch: dict[str, Any],
    existing: dict[str, ExistingRecordSnapshot] | None = None,
):
    return build_memory_write_plan(
        batch,
        approved_batch_content_sha256=validated_digest(batch),
        existing_records=existing,
        sql_guard=FakeGuard(),
    )


def snapshot_for(
    item,
    plan,
    *,
    training_batch_id: str | None = None,
    batch_content_sha256: str | None = None,
    sample_id: str | None = None,
    canonical_content: dict[str, Any] | None = None,
    memory_content_sha256: str | None = None,
) -> ExistingRecordSnapshot:
    return ExistingRecordSnapshot(
        record_id=item.record_id,
        canonical_content=canonical_content or copy.deepcopy(item.canonical_content),
        memory_content_sha256=memory_content_sha256 or item.memory_content_sha256,
        created_by_training_batch_id=training_batch_id or plan.training_batch_id,
        created_by_batch_content_sha256=(
            batch_content_sha256 or plan.batch_content_sha256
        ),
        created_from_sample_id=sample_id or item.sample_id,
    )


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=True,
    ).stdout


def expect_error(callback, code: str) -> tuple[bool, str]:
    try:
        callback()
    except MemoryWritePlanError as error:
        return error.code == code, str(error)
    except Exception as error:  # noqa: BLE001
        return False, f"unexpected {type(error).__name__}: {error}"
    return False, "未抛出异常"


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    status_before = git_status()

    batch = load_batch()
    first = make_plan(batch)
    results.append(
        (
            "合法批次生成可执行首次计划",
            first.executable and len(first.items) == 2,
            first.write_plan_sha256,
        )
    )
    results.append(
        (
            "首次计划全部为 create",
            first.create_count == 2
            and first.resume_same_batch_count == 0
            and all(item.status == "create" for item in first.items),
            f"create={first.create_count}; resume={first.resume_same_batch_count}",
        )
    )
    first_item = first.items[0]
    results.append(
        (
            "正式记录 ID 严格使用内容摘要",
            first_item.record_id
            == "toolmem-v1-" + first_item.memory_content_sha256
            and set(first_item.canonical_content)
            == {
                "record_schema_version",
                "question",
                "tool_name",
                "args",
                "success",
            },
            first_item.record_id,
        )
    )
    expected_delivery = hashlib.sha256(
        (
            first.batch_content_sha256
            + first_item.sample_id
            + first_item.record_id
        ).encode("utf-8")
    ).hexdigest()
    results.append(
        (
            "delivery item ID 覆盖批次摘要样本 ID 和记录 ID",
            first_item.delivery_item_sha256 == expected_delivery,
            first_item.delivery_item_sha256,
        )
    )

    repeated = make_plan(copy.deepcopy(batch))
    results.append(
        (
            "相同输入的计划摘要确定一致",
            first.write_plan_sha256 == repeated.write_plan_sha256
            and first.to_dict() == repeated.to_dict(),
            repeated.write_plan_sha256,
        )
    )

    digest_rejected, digest_detail = expect_error(
        lambda: build_memory_write_plan(
            batch,
            approved_batch_content_sha256="0" * 64,
            sql_guard=FakeGuard(),
        ),
        "BATCH_DIGEST_MISMATCH",
    )
    results.append(("批准摘要不匹配时拒绝", digest_rejected, digest_detail))

    wrong_expected_count = copy.deepcopy(batch)
    wrong_expected_count["expected_new_memory_count"] = 3
    count_contract_rejected, count_contract_detail = expect_error(
        lambda: build_memory_write_plan(
            wrong_expected_count,
            approved_batch_content_sha256="0" * 64,
            sql_guard=FakeGuard(),
        ),
        "BATCH_INVALID",
    )
    results.append(
        (
            "批次 expected_new_memory_count 契约错误时拒绝",
            count_contract_rejected,
            count_contract_detail,
        )
    )

    sql_equivalent = copy.deepcopy(batch)
    sql_equivalent["samples"][0]["args"]["sql"] = (
        "  " + batch["samples"][0]["args"]["sql"] + ";  "
    )
    sql_equivalent_plan = make_plan(sql_equivalent)
    results.append(
        (
            "SQL 外部空白和末尾分号不改变 record ID",
            first.items[0].record_id == sql_equivalent_plan.items[0].record_id,
            sql_equivalent_plan.items[0].record_id,
        )
    )

    question_equivalent = copy.deepcopy(batch)
    question_equivalent["samples"][0]["question"] = (
        "  " + batch["samples"][0]["question"] + "  "
    )
    question_equivalent_plan = make_plan(question_equivalent)
    results.append(
        (
            "问题首尾空白不改变 record ID",
            first.items[0].record_id == question_equivalent_plan.items[0].record_id,
            question_equivalent_plan.items[0].record_id,
        )
    )

    sql_changed = copy.deepcopy(batch)
    sql_changed["samples"][0]["args"]["sql"] = (
        "SELECT outlet_name, area_name FROM rs_outlet "
        "ORDER BY area_name DESC LIMIT 10"
    )
    sql_changed_plan = make_plan(sql_changed)
    results.append(
        (
            "SQL 语义变化改变 record ID",
            first.items[0].record_id != sql_changed_plan.items[0].record_id,
            sql_changed_plan.items[0].record_id,
        )
    )

    question_changed = copy.deepcopy(batch)
    question_changed["samples"][0]["question"] = "测试区域排污口按区域倒序如何展示？"
    question_changed_plan = make_plan(question_changed)
    results.append(
        (
            "问题语义变化改变 record ID",
            first.items[0].record_id != question_changed_plan.items[0].record_id,
            question_changed_plan.items[0].record_id,
        )
    )

    changed_tool_content = copy.deepcopy(first.items[0].canonical_content)
    changed_tool_content["tool_name"] = "visualize_data"
    changed_tool_identity = build_memory_identity_from_canonical_content(
        changed_tool_content
    )
    results.append(
        (
            "tool_name 参与 Memory 内容身份",
            first.items[0].record_id != changed_tool_identity.record_id,
            changed_tool_identity.record_id,
        )
    )

    batch_id_changed = copy.deepcopy(batch)
    batch_id_changed["training_batch_id"] = "level4-fixture-20260714-02"
    batch_id_plan = make_plan(batch_id_changed)
    results.append(
        (
            "批次编号变化不改变 record ID",
            first.items[0].record_id == batch_id_plan.items[0].record_id,
            batch_id_plan.items[0].record_id,
        )
    )
    results.append(
        (
            "批次编号变化改变批次摘要和 delivery item",
            first.batch_content_sha256 != batch_id_plan.batch_content_sha256
            and first.items[0].delivery_item_sha256
            != batch_id_plan.items[0].delivery_item_sha256,
            batch_id_plan.items[0].delivery_item_sha256,
        )
    )

    sample_id_changed = copy.deepcopy(batch)
    sample_id_changed["samples"][0]["sample_id"] = "L4_FIXTURE_SQL_101"
    sample_id_plan = make_plan(sample_id_changed)
    results.append(
        (
            "sample_id 变化不改变 record ID",
            first.items[0].record_id == sample_id_plan.items[0].record_id,
            sample_id_plan.items[0].record_id,
        )
    )
    results.append(
        (
            "sample_id 变化改变批次摘要和 delivery item",
            first.batch_content_sha256 != sample_id_plan.batch_content_sha256
            and first.items[0].delivery_item_sha256
            != sample_id_plan.items[0].delivery_item_sha256,
            sample_id_plan.items[0].delivery_item_sha256,
        )
    )

    governance_changed = copy.deepcopy(batch)
    governance_changed["samples"][0]["source"] = "另一纯测试来源"
    governance_changed["samples"][0]["review_reason"] = "另一静态审查理由"
    governance_changed["samples"][0]["expected_behavior"] = "另一预期测试行为"
    governance_plan = make_plan(governance_changed)
    results.append(
        (
            "来源审查原因和预期行为不改变 record ID",
            first.items[0].record_id == governance_plan.items[0].record_id,
            governance_plan.items[0].record_id,
        )
    )
    results.append(
        (
            "治理字段变化改变批次摘要和 delivery item",
            first.batch_content_sha256 != governance_plan.batch_content_sha256
            and first.items[0].delivery_item_sha256
            != governance_plan.items[0].delivery_item_sha256,
            governance_plan.items[0].delivery_item_sha256,
        )
    )

    expected_governance_keys = {
        "record_schema_version",
        "memory_kind",
        "memory_content_sha256",
        "created_by_training_batch_id",
        "created_by_batch_content_sha256",
        "created_from_sample_id",
        "training_level",
        "train_decision",
    }
    expected_compatibility_keys = {
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
    }
    results.append(
        (
            "治理 metadata 字段完整",
            set(first.items[0].governance_metadata) == expected_governance_keys,
            str(sorted(first.items[0].governance_metadata)),
        )
    )
    results.append(
        (
            "兼容 metadata_json 字段完整",
            set(first.items[0].compatibility_metadata)
            == expected_compatibility_keys,
            str(sorted(first.items[0].compatibility_metadata)),
        )
    )

    resume_snapshots = {
        item.record_id: snapshot_for(item, first) for item in first.items
    }
    resume_plan = make_plan(batch, resume_snapshots)
    resume_repeated = make_plan(
        batch, dict(reversed(list(resume_snapshots.items())))
    )
    results.append(
        (
            "同批次精确既有记录为 resume_same_batch",
            resume_plan.executable
            and resume_plan.create_count == 0
            and resume_plan.resume_same_batch_count == 2
            and all(item.status == "resume_same_batch" for item in resume_plan.items),
            resume_plan.write_plan_sha256,
        )
    )
    results.append(
        (
            "相同既有记录状态的恢复计划摘要一致",
            resume_plan.write_plan_sha256 == resume_repeated.write_plan_sha256
            and resume_plan.write_plan_sha256 != first.write_plan_sha256,
            resume_repeated.write_plan_sha256,
        )
    )

    mixed_resume = make_plan(
        batch,
        {first.items[0].record_id: snapshot_for(first.items[0], first)},
    )
    results.append(
        (
            "恢复计划 create 加 resume 等于批准总数",
            mixed_resume.executable
            and mixed_resume.create_count == 1
            and mixed_resume.resume_same_batch_count == 1
            and mixed_resume.create_count + mixed_resume.resume_same_batch_count
            == mixed_resume.expected_new_memory_count,
            mixed_resume.write_plan_sha256,
        )
    )

    other_snapshot = snapshot_for(
        first.items[0],
        first,
        training_batch_id="level4-fixture-20260713-99",
        batch_content_sha256="f" * 64,
    )
    other_plan = make_plan(batch, {first.items[0].record_id: other_snapshot})
    results.append(
        (
            "其他批次精确既有记录阻断",
            not other_plan.executable
            and other_plan.items[0].status == "preexisting_other_batch"
            and other_plan.preexisting_other_batch_count == 1,
            str([issue.code for issue in other_plan.issues]),
        )
    )
    results.append(
        (
            "首次计划新增数量不符时阻断",
            any(
                issue.code == "FIRST_EXECUTION_COUNT_MISMATCH"
                for issue in other_plan.issues
            ),
            str([issue.code for issue in other_plan.issues]),
        )
    )

    corrupt_content = copy.deepcopy(first.items[0].canonical_content)
    corrupt_content["question"] = "损坏内容"
    conflict_snapshot = snapshot_for(
        first.items[0], first, canonical_content=corrupt_content
    )
    conflict_plan = make_plan(
        batch, {first.items[0].record_id: conflict_snapshot}
    )
    results.append(
        (
            "同 ID 内容不一致为 conflict 并阻断",
            not conflict_plan.executable
            and conflict_plan.items[0].status == "conflict"
            and conflict_plan.conflict_count == 1,
            str([issue.code for issue in conflict_plan.issues]),
        )
    )

    duplicate_batch = copy.deepcopy(batch)
    duplicate_batch["samples"][1]["question"] = duplicate_batch["samples"][0][
        "question"
    ]
    duplicate_batch["samples"][1]["args"]["sql"] = duplicate_batch["samples"][0][
        "args"
    ]["sql"]
    duplicate_plan = make_plan(duplicate_batch)
    results.append(
        (
            "批次内不同 sample_id 的相同内容稳定拒绝",
            not duplicate_plan.executable
            and any(
                issue.code == "DUPLICATE_MEMORY_CONTENT_IN_BATCH"
                for issue in duplicate_plan.issues
            ),
            str([issue.code for issue in duplicate_plan.issues]),
        )
    )

    plan_payload = first.to_dict()
    serialized_plan = json.dumps(
        plan_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    forbidden_keys = {
        "generated_at",
        "timestamp",
        "machine_name",
        "username",
        "absolute_path",
        "uuid",
        "random",
    }

    def collect_keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            keys = set(value)
            for item in value.values():
                keys.update(collect_keys(item))
            return keys
        if isinstance(value, list):
            keys: set[str] = set()
            for item in value:
                keys.update(collect_keys(item))
            return keys
        return set()

    results.append(
        (
            "计划 JSON 不含运行时路径时间或随机字段",
            not (collect_keys(plan_payload) & forbidden_keys)
            and str(PROJECT_ROOT) not in serialized_plan,
            str(sorted(collect_keys(plan_payload) & forbidden_keys)),
        )
    )

    ledger = ExecutionLedgerEntry(
        execution_id="synthetic-execution-001",
        batch_content_sha256=first.batch_content_sha256,
        write_plan_sha256=first.write_plan_sha256,
        record_id=first.items[0].record_id,
        sample_id=first.items[0].sample_id,
        planned_status=first.items[0].status,
        execution_status="planned",
        created_this_attempt=False,
        error_code="",
    )
    results.append(
        (
            "执行账本模型仅保存调用者提供的数据",
            ledger.execution_status == "planned" and not ledger.created_this_attempt,
            ledger.execution_id,
        )
    )

    forbidden_modules = sorted(
        name
        for name in sys.modules
        if name == "chromadb"
        or name.startswith("chromadb.")
        or name == "sqlite3"
        or name.startswith("sqlite3.")
        or name == "psycopg2"
        or name.startswith("psycopg2.")
        or name.startswith("vanna.capabilities.agent_memory")
        or name.startswith("vanna.integrations.chromadb")
    )
    results.append(
        (
            "测试未导入存储数据库或 AgentMemory 实现",
            not forbidden_modules,
            str(forbidden_modules),
        )
    )

    status_after = git_status()
    results.append(
        (
            "测试前后 Git 工作区状态不变",
            status_before == status_after,
            f"before={status_before!r}; after={status_after!r}",
        )
    )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
