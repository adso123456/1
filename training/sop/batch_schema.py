"""版本化训练批次 JSON 数据模型。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


TRAINING_BATCH_ID_PATTERN = (
    r"^level[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9]{8}-[0-9]{2}$"
)
TRAINING_LEVEL_PATTERN = r"^level[1-9][0-9]*(?:_[a-z0-9]+)*_sql_examples$"
SAMPLE_ID_PATTERN = r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$"


class StrictContractModel(BaseModel):
    """禁止类型隐式转换和未知字段的契约基类。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class RunSqlArgs(StrictContractModel):
    sql: str


class TrainingSample(StrictContractModel):
    sample_id: Annotated[str, Field(pattern=SAMPLE_ID_PATTERN)]
    question: str
    tool_name: str
    args: RunSqlArgs
    training_level: str
    train_decision: str
    review_reason: str
    source: str
    expected_behavior: str
    expected_tables: list[str]


class TrainingBatch(StrictContractModel):
    schema_version: str
    training_batch_id: Annotated[str, Field(pattern=TRAINING_BATCH_ID_PATTERN)]
    training_level: Annotated[str, Field(pattern=TRAINING_LEVEL_PATTERN)]
    status: str
    source: str
    expected_new_memory_count: int
    samples: list[TrainingSample]
