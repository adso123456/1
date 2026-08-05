"""运行时受控自学习（Runtime Learning）配置。

所有路径基于 PROJECT_ROOT 相对解析，支持受控环境变量覆盖，
不得依赖 Path.cwd() 或任何绝对目录。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from config.settings import AGENT_DATA_DIR, PROJECT_ROOT

# 默认候选库：PROJECT_ROOT/agent_data/learning_candidates.sqlite3
# （AGENT_DATA_DIR 可配置为其他目录；Docker 中 agent_data 已挂载为卷）
DEFAULT_LEARNING_CANDIDATE_DB = (
    Path(AGENT_DATA_DIR) / "learning_candidates.sqlite3"
)


def _env_str(
    environ: Mapping[str, str], name: str, default: str
) -> str:
    value = environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name)
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not (minimum <= parsed <= maximum):
        return default
    return parsed


def _env_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
) -> int:
    value = environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def resolve_learning_candidate_db(
    environ: Mapping[str, str] | None = None,
) -> Path:
    """解析候选库路径。LEARNING_CANDIDATE_DB 绝对路径优先，相对路径基于 PROJECT_ROOT。"""
    source = dict(os.environ if environ is None else environ)
    raw = source.get("LEARNING_CANDIDATE_DB")
    if raw is None or not raw.strip():
        return DEFAULT_LEARNING_CANDIDATE_DB
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class OnlineLearningSettings:
    """运行时学习全部开关与阈值。"""

    enabled: bool
    capture_enabled: bool
    judge_enabled: bool
    auto_publish: bool
    judge_min_confidence: float
    batch_size: int
    batch_max_wait_seconds: int
    worker_interval_seconds: int
    max_result_rows: int
    max_result_bytes: int
    max_judge_attempts: int
    candidate_db_path: Path

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "OnlineLearningSettings":
        source = dict(os.environ if environ is None else environ)
        return cls(
            enabled=_env_bool(
                source, "ONLINE_LEARNING_ENABLED", default=False
            ),
            capture_enabled=_env_bool(
                source, "ONLINE_LEARNING_CAPTURE_ENABLED", default=True
            ),
            judge_enabled=_env_bool(
                source, "ONLINE_LEARNING_JUDGE_ENABLED", default=True
            ),
            auto_publish=_env_bool(
                source, "ONLINE_LEARNING_AUTO_PUBLISH", default=False
            ),
            judge_min_confidence=_env_float(
                source,
                "ONLINE_LEARNING_JUDGE_MIN_CONFIDENCE",
                default=0.95,
                minimum=0.0,
                maximum=1.0,
            ),
            batch_size=_env_int(
                source,
                "ONLINE_LEARNING_BATCH_SIZE",
                default=10,
                minimum=1,
            ),
            batch_max_wait_seconds=_env_int(
                source,
                "ONLINE_LEARNING_BATCH_MAX_WAIT_SECONDS",
                default=600,
                minimum=1,
            ),
            worker_interval_seconds=_env_int(
                source,
                "ONLINE_LEARNING_WORKER_INTERVAL_SECONDS",
                default=30,
                minimum=1,
            ),
            max_result_rows=_env_int(
                source,
                "ONLINE_LEARNING_MAX_RESULT_ROWS",
                default=20,
                minimum=1,
            ),
            max_result_bytes=_env_int(
                source,
                "ONLINE_LEARNING_MAX_RESULT_BYTES",
                default=65536,
                minimum=1024,
            ),
            max_judge_attempts=_env_int(
                source,
                "ONLINE_LEARNING_MAX_JUDGE_ATTEMPTS",
                default=3,
                minimum=1,
            ),
            candidate_db_path=resolve_learning_candidate_db(source),
        )


# 以下为任务要求的全部配置项清单（.env.example 与文档同步使用）：
_ONLINE_LEARNING_ENV_VARS: tuple[str, ...] = (
    "ONLINE_LEARNING_ENABLED",
    "ONLINE_LEARNING_CAPTURE_ENABLED",
    "ONLINE_LEARNING_JUDGE_ENABLED",
    "ONLINE_LEARNING_AUTO_PUBLISH",
    "ONLINE_LEARNING_JUDGE_MIN_CONFIDENCE",
    "ONLINE_LEARNING_BATCH_SIZE",
    "ONLINE_LEARNING_BATCH_MAX_WAIT_SECONDS",
    "ONLINE_LEARNING_WORKER_INTERVAL_SECONDS",
    "ONLINE_LEARNING_MAX_RESULT_ROWS",
    "ONLINE_LEARNING_MAX_RESULT_BYTES",
    "ONLINE_LEARNING_MAX_JUDGE_ATTEMPTS",
    "LEARNING_CANDIDATE_DB",
)
