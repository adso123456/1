"""LLM 运行时配置：管理页可运行时更换 API Key / 模型 / Base URL。

配置持久化到 agent_data/system/llm_settings.json（卷内），不进镜像；
保存后同步写入 os.environ，并让 RuntimeManager 失效重建 Agent，
下一次问数即使用新配置。仅支持 OpenAI 兼容格式接口。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from config.settings import AGENT_DATA_DIR, PROJECT_ROOT, resolve_project_path


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
LLM_SETTINGS_PATH = (
    Path(AGENT_DATA_DIR) / "system" / "llm_settings.json"
).resolve()
_MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")


class LLMSettingsError(ValueError):
    """LLM 配置不合法。"""


def _read_settings() -> dict[str, str]:
    if not LLM_SETTINGS_PATH.is_file():
        return {}
    try:
        payload = json.loads(LLM_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: str(payload[key]).strip()
        for key in ("api_key", "model", "base_url")
        if isinstance(payload.get(key), str) and str(payload[key]).strip()
    }


def _validate_model(model: str) -> str:
    model = str(model or "").strip()
    if not model or not _MODEL_RE.match(model):
        raise LLMSettingsError("模型名称不能为空，且只能包含字母、数字、点、下划线、冒号、斜杠和短横线")
    return model


def _validate_base_url(base_url: str) -> str:
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        return DEFAULT_BASE_URL
    lowered = base_url.lower()
    if not lowered.startswith(("http://", "https://")):
        raise LLMSettingsError("Base URL 必须是 http(s) 地址（仅支持 OpenAI 兼容格式接口）")
    return base_url


def _write_settings(payload: dict[str, str]) -> None:
    LLM_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LLM_SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(LLM_SETTINGS_PATH)


def apply_settings_to_environ(environ: dict[str, str] | None = None) -> None:
    """启动时把已保存的配置并入环境变量（文件优先于 .env / 宿主机环境）。"""
    settings = _read_settings()
    if not settings:
        return
    target = os.environ if environ is None else environ
    if settings.get("api_key"):
        target["DEEPSEEK_API_KEY"] = settings["api_key"]
    if settings.get("model"):
        target["DEEPSEEK_MODEL"] = settings["model"]
    if settings.get("base_url"):
        target["DEEPSEEK_BASE_URL"] = settings["base_url"]


def settings_view(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """对外视图：绝不回显 API Key 明文。"""
    source = dict(os.environ if environ is None else environ)
    saved = _read_settings()
    return {
        "api_key_set": bool(
            saved.get("api_key")
            or source.get("DEEPSEEK_API_KEY", "").strip()
        ),
        "model": saved.get("model")
        or source.get("DEEPSEEK_MODEL", "").strip()
        or DEFAULT_MODEL,
        "base_url": saved.get("base_url")
        or source.get("DEEPSEEK_BASE_URL", "").strip()
        or DEFAULT_BASE_URL,
        "persisted": bool(saved),
    }


def save_llm_settings(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """保存运行时 LLM 配置并立即同步到环境变量。"""
    current = _read_settings()
    if api_key is not None:
        key = str(api_key or "").strip()
        if not key:
            raise LLMSettingsError("API Key 不能为空")
        current["api_key"] = key
    if model is not None:
        current["model"] = _validate_model(model)
    if base_url is not None:
        current["base_url"] = _validate_base_url(base_url)
    if not current.get("model"):
        current["model"] = DEFAULT_MODEL
    if not current.get("base_url"):
        current["base_url"] = DEFAULT_BASE_URL
    if not current.get("api_key"):
        raise LLMSettingsError("API Key 不能为空")
    _write_settings(current)
    os.environ["DEEPSEEK_API_KEY"] = current["api_key"]
    os.environ["DEEPSEEK_MODEL"] = current["model"]
    os.environ["DEEPSEEK_BASE_URL"] = current["base_url"]
    return settings_view()
