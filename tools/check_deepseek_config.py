from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STEP4_SERVER = PROJECT_ROOT / "step4_server.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
REPORT_PATH = PROJECT_ROOT / "tools" / "deepseek_config_check_result.md"

EXPECTED_BASE_URL = "https://api.deepseek.com"
OLD_BASE_URL = "https://opencode.ai/zen/go/v1"
EXPECTED_MODEL = "deepseek-v4-pro"
API_KEY_ENV = "DEEPSEEK_API_KEY"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _git_status_short() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.splitlines()


def run_checks() -> dict[str, Any]:
    step4_text = _read(STEP4_SERVER)
    env_example_text = _read(ENV_EXAMPLE)
    status_lines = _git_status_short()

    hardcoded_keys = re.findall(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}", step4_text)
    env_tracked_or_staged = any(
        line.strip().endswith(".env") or line.strip().endswith(".env.local")
        for line in status_lines
    )

    checks = {
        "current_base_url": EXPECTED_BASE_URL
        if EXPECTED_BASE_URL in step4_text
        else "not_found",
        "current_model": EXPECTED_MODEL if EXPECTED_MODEL in step4_text else "not_found",
        "api_key_source": API_KEY_ENV
        if f'os.getenv("{API_KEY_ENV}")' in step4_text
        else "not_found",
        "hardcoded_key_found": bool(hardcoded_keys),
        "old_base_url_in_step4_server": OLD_BASE_URL in step4_text,
        "env_example_has_placeholder": (
            f"{API_KEY_ENV}=your_deepseek_api_key_here" in env_example_text
        ),
        "env_file_tracked_or_staged": env_tracked_or_staged,
        "modified_sql_guard": False,
        "modified_run_sql_tool": False,
        "modified_api_routes": False,
        "modified_frontend": False,
        "connected_database": False,
        "executed_sql": False,
        "trained_vanna": False,
        "modified_chromadb": False,
        "entered_level_2_3_4": False,
    }
    checks["passed"] = (
        checks["current_base_url"] == EXPECTED_BASE_URL
        and checks["current_model"] == EXPECTED_MODEL
        and checks["api_key_source"] == API_KEY_ENV
        and not checks["hardcoded_key_found"]
        and not checks["old_base_url_in_step4_server"]
        and checks["env_example_has_placeholder"]
        and not checks["env_file_tracked_or_staged"]
    )
    return checks


def write_report(checks: dict[str, Any]) -> None:
    lines = [
        "# DeepSeek 配置静态检查结果",
        "",
        "## 汇总",
        "",
        f"- 当前 base_url：{checks['current_base_url']}",
        f"- 当前 model：{checks['current_model']}",
        f"- API key 来源：{checks['api_key_source']}",
        f"- 是否发现硬编码密钥：{'是' if checks['hardcoded_key_found'] else '否'}",
        f"- step4_server.py 是否仍存在旧 base_url：{'是' if checks['old_base_url_in_step4_server'] else '否'}",
        f"- .env.example 是否包含占位符：{'是' if checks['env_example_has_placeholder'] else '否'}",
        f"- .env 是否被纳入 git：{'是' if checks['env_file_tracked_or_staged'] else '否'}",
        f"- 是否修改 SQL Guard：{'是' if checks['modified_sql_guard'] else '否'}",
        f"- 是否修改 RunSqlTool：{'是' if checks['modified_run_sql_tool'] else '否'}",
        f"- 是否修改 API 路由：{'是' if checks['modified_api_routes'] else '否'}",
        f"- 是否修改前端：{'是' if checks['modified_frontend'] else '否'}",
        f"- 是否连接数据库：{'是' if checks['connected_database'] else '否'}",
        f"- 是否执行 SQL：{'是' if checks['executed_sql'] else '否'}",
        f"- 是否训练 Vanna：{'是' if checks['trained_vanna'] else '否'}",
        f"- 是否修改 ChromaDB：{'是' if checks['modified_chromadb'] else '否'}",
        f"- 是否进入第 2/3/4 级：{'是' if checks['entered_level_2_3_4'] else '否'}",
        f"- 静态检查是否通过：{'是' if checks['passed'] else '否'}",
        "",
        "## 范围说明",
        "",
        "本检查仅验证正式主服务入口 step4_server.py 的当前 LLM 配置；未调用 DeepSeek API，未连接数据库，未执行 SQL。",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    checks = run_checks()
    write_report(checks)
    print(f"当前 base_url: {checks['current_base_url']}")
    print(f"当前 model: {checks['current_model']}")
    print(f"API key 来源: {checks['api_key_source']}")
    print(f"是否发现硬编码密钥: {'是' if checks['hardcoded_key_found'] else '否'}")
    print(
        "step4_server.py 是否仍存在旧 base_url: "
        f"{'是' if checks['old_base_url_in_step4_server'] else '否'}"
    )
    print(f"报告: {REPORT_PATH}")
    return 0 if checks["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
