"""纯静态验证标准训练等级命名契约。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.batch_schema import TrainingBatch
from training.sop.batch_validator import validate_training_batch


ACCEPTED_LEVELS = (
    "level2_sql_examples",
    "level3_p0_sql_examples",
    "level3_p1_sql_examples",
    "level3_p2_sql_examples",
    "level4_fixture_sql_examples",
    "level10_custom_sql_examples",
)
REJECTED_LEVELS = (
    "level0_sql_examples",
    "level2",
    "level2_examples",
    "level2__sql_examples",
    "level2_SQL_examples",
    "level2-sql-examples",
    "level2_ sql_examples",
    "sql_examples",
)


def make_batch(level: str = "level2_sql_examples") -> dict:
    return {
        "schema_version": "1.0",
        "training_batch_id": "level2-contract-test-20260715-01",
        "training_level": level,
        "status": "frozen",
        "source": "training level contract test",
        "expected_new_memory_count": 1,
        "samples": [
            {
                "sample_id": "CONTRACT_LEVEL_001",
                "question": "查询数据字典列表类型，最多返回一条",
                "tool_name": "run_sql",
                "args": {"sql": "SELECT list_type FROM ad_dict LIMIT 1"},
                "training_level": level,
                "train_decision": "approved",
                "review_reason": "纯契约测试",
                "source": "contract test",
                "expected_behavior": "返回一条列表类型",
                "expected_tables": ["ad_dict"],
            }
        ],
    }


def main() -> None:
    for level in ACCEPTED_LEVELS:
        TrainingBatch.model_validate(make_batch(level))

    for level in REJECTED_LEVELS:
        try:
            TrainingBatch.model_validate(make_batch(level))
        except ValidationError:
            continue
        raise AssertionError(f"应拒绝训练等级：{level}")

    standard = make_batch()
    assert validate_training_batch(standard).valid

    mismatch = deepcopy(standard)
    mismatch["samples"][0]["training_level"] = "level3_p0_sql_examples"
    result = validate_training_batch(mismatch)
    assert not result.valid
    assert [issue.code for issue in result.errors] == ["SAMPLE_LEVEL_MISMATCH"]

    print("TRAINING_LEVEL_CONTRACT_TEST: PASS")


if __name__ == "__main__":
    main()
