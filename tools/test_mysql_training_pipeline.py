"""MySQL 首批训练 Plan/Apply、幂等与失败隔离测试。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.mysql_lzh_monitor_training import (
    SOURCE_ID,
    TrainingError,
    apply_plan,
    build_desired_records,
    create_plan,
    inventory_store,
    write_materials,
)
from backend.sql_example_context_enhancer import ALLOWED_TRAINING_LEVELS


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    temp_root = Path(tempfile.mkdtemp(prefix="mysql-training-pipeline-"))
    formal = temp_root / "formal" / SOURCE_ID
    work = temp_root / "work"
    backups = temp_root / "backups"
    try:
        manifest = write_materials()
        records, summary = build_desired_records()
        categories = {
            category: sum(item.category == category for item in records)
            for category in ("ddl", "business_document", "sql_example")
        }
        results.append(
            (
                "训练材料数量固定",
                categories
                == {"ddl": 18, "business_document": 12, "sql_example": 18}
                and summary["total_record_count"] == 48
                and manifest["record_set_sha256"] == summary["record_set_sha256"],
                str(categories),
            )
        )
        results.append(
            (
                "MySQL SQL示例训练级别可被运行时注入",
                "level2_mysql_sql_examples" in ALLOWED_TRAINING_LEVELS,
                str(sorted(ALLOWED_TRAINING_LEVELS)),
            )
        )
        first_plan = create_plan(formal)
        results.append(
            (
                "首次 Plan 仅计划创建48条",
                len(first_plan["create_ids"]) == 48
                and not first_plan["update_ids"]
                and not first_plan["delete_ids"],
                first_plan["plan_sha256"],
            )
        )

        failure_closed = False
        try:
            apply_plan(
                expected_plan_sha256=first_plan["plan_sha256"],
                formal_store=formal,
                work_root=work,
                backup_root=backups,
                fail_after=1,
            )
        except TrainingError:
            failure_closed = not formal.exists()
        results.append(
            ("候选训练失败不影响正式库", failure_closed, str(formal))
        )

        applied = apply_plan(
            expected_plan_sha256=first_plan["plan_sha256"],
            formal_store=formal,
            work_root=work,
            backup_root=backups,
        )
        inventory = inventory_store(formal)
        results.append(
            (
                "Apply 发布完整正式库",
                applied["status"] == "applied"
                and len(inventory) == 48
                and all(
                    item["metadata"].get("source_id") == SOURCE_ID
                    for item in inventory.values()
                ),
                json.dumps(applied, ensure_ascii=False),
            )
        )

        repeat_plan = create_plan(formal)
        repeated = apply_plan(
            expected_plan_sha256=repeat_plan["plan_sha256"],
            formal_store=formal,
            work_root=work,
            backup_root=backups,
        )
        results.append(
            (
                "重复 Plan/Apply 幂等",
                not repeat_plan["create_ids"]
                and not repeat_plan["update_ids"]
                and not repeat_plan["delete_ids"]
                and len(repeat_plan["unchanged_ids"]) == 48
                and repeated == {
                    "status": "unchanged",
                    "plan_sha256": repeat_plan["plan_sha256"],
                    "count": 48,
                }
                and len(inventory_store(formal)) == 48,
                repeat_plan["plan_sha256"],
            )
        )

        forbidden = ("geom", "centre", "contact", "phone")
        pollutant_ddl = next(
            item.document
            for item in records
            if item.category == "ddl"
            and item.logical_name == "rs_pollutant_info"
        )
        all_documents = "\n".join(
            item.document
            for item in records
            if item.category == "business_document"
        )
        results.append(
            (
                "排除字段不进入DDL或业务文档",
                all(name not in pollutant_ddl for name in forbidden)
                and all(name not in all_documents for name in forbidden),
                "四个排除字段",
            )
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=False)

    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}: {name} | {detail}")
    failures = [name for name, passed, _ in results if not passed]
    print(f"TOTAL={len(results)} PASS={len(results) - len(failures)} FAIL={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
