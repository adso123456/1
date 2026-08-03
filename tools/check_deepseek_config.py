from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from test_report_output import resolve_test_report_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_SOURCES = (
    PROJECT_ROOT / "backend" / "agent_assembly.py",
    PROJECT_ROOT / "backend" / "tracing_llm_service.py",
)
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
REPORT_PATH = resolve_test_report_path("deepseek_config_check_result.md")

EXPECTED_BASE_URL = "https://api.deepseek.com"
OLD_BASE_URL = "https://opencode.ai/zen/go/v1"
EXPECTED_MODEL = "deepseek-v4-pro"
API_KEY_ENV = "DEEPSEEK_API_KEY"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _tracked_env_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", ".env", ".env.local"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def run_checks() -> dict[str, Any]:
    config_text = "\n".join(_read(path) for path in CONFIG_SOURCES)
    env_example_text = _read(ENV_EXAMPLE)
    hardcoded_keys = re.findall(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}", config_text)

    checks = {
        "current_base_url": EXPECTED_BASE_URL if EXPECTED_BASE_URL in config_text else "not_found",
        "current_model": EXPECTED_MODEL if EXPECTED_MODEL in config_text else "not_found",
        "api_key_source": API_KEY_ENV
        if re.search(r'(?:getenv|source\.get)\(["\']DEEPSEEK_API_KEY["\']\)', config_text)
        else "not_found",
        "hardcoded_key_found": bool(hardcoded_keys),
        "old_base_url_found": OLD_BASE_URL in config_text,
        "env_example_has_placeholder": bool(
            re.search(r"^DEEPSEEK_API_KEY=(?:replace|your)_", env_example_text, re.MULTILINE)
        ),
        "tracked_env_files": _tracked_env_files(),
    }
    checks["passed"] = (
        checks["current_base_url"] == EXPECTED_BASE_URL
        and checks["current_model"] == EXPECTED_MODEL
        and checks["api_key_source"] == API_KEY_ENV
        and not checks["hardcoded_key_found"]
        and not checks["old_base_url_found"]
        and checks["env_example_has_placeholder"]
        and not checks["tracked_env_files"]
    )
    return checks


def write_report(checks: dict[str, Any]) -> None:
    lines = [
        "# DeepSeek 配置静态检查",
        "",
        f"- base_url：{checks['current_base_url']}",
        f"- model：{checks['current_model']}",
        f"- API Key 来源：{checks['api_key_source']}",
        f"- 发现硬编码密钥：{'是' if checks['hardcoded_key_found'] else '否'}",
        f"- 发现旧网关：{'是' if checks['old_base_url_found'] else '否'}",
        f"- `.env.example` 包含占位符：{'是' if checks['env_example_has_placeholder'] else '否'}",
        f"- Git 跟踪真实 `.env`：{', '.join(checks['tracked_env_files']) or '否'}",
        f"- 结论：{'通过' if checks['passed'] else '未通过'}",
        "",
        "本检查只读取当前 Agent 装配、LLM 服务与环境变量模板，不调用模型、数据库或正式资产。",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    checks = run_checks()
    write_report(checks)
    print(f"base_url: {checks['current_base_url']}")
    print(f"model: {checks['current_model']}")
    print(f"API key source: {checks['api_key_source']}")
    print(f"report: {REPORT_PATH}")
    return 0 if checks["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
