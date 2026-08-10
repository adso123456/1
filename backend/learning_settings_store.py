"""运行时学习开关的持久化与生效配置解析。

管理页可编辑 enabled / capture_enabled / judge_enabled / auto_publish，
保存到 agent_data/system/runtime_learning_settings.json（卷内，不进镜像）；
Service / Worker 每次操作前重新解析生效配置，改动即时生效，无需重启。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from config.learning_settings import OnlineLearningSettings
from config.settings import AGENT_DATA_DIR


LEARNING_SETTINGS_PATH = (
    Path(AGENT_DATA_DIR) / "system" / "runtime_learning_settings.json"
).resolve()
_KEYS = ("enabled", "capture_enabled", "judge_enabled", "auto_publish")


def _read_overrides() -> dict[str, bool]:
    if not LEARNING_SETTINGS_PATH.is_file():
        return {}
    try:
        payload = json.loads(LEARNING_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: bool(payload[key])
        for key in _KEYS
        if isinstance(payload.get(key), bool)
    }


def effective_learning_settings(
    environ: Mapping[str, str] | None = None,
) -> OnlineLearningSettings:
    """环境变量为默认值，卷内管理页覆盖值优先。"""
    base = OnlineLearningSettings.from_environment(environ)
    overrides = _read_overrides()
    if not overrides:
        return base
    return replace(base, **overrides)


def save_learning_settings(
    *,
    enabled: bool,
    capture_enabled: bool,
    judge_enabled: bool,
    auto_publish: bool,
) -> dict[str, bool]:
    payload = {
        "enabled": bool(enabled),
        "capture_enabled": bool(capture_enabled),
        "judge_enabled": bool(judge_enabled),
        "auto_publish": bool(auto_publish),
    }
    LEARNING_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LEARNING_SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(LEARNING_SETTINGS_PATH)
    return payload
