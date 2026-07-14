"""验证遗留入口保持路径存在并立即 fail-closed。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ("step4_agent.py", "step4_test2.py", "diag_tool_calls.py")
EXPECTED_MESSAGE = "旧入口已禁用，请使用 step4_server.py"
FORBIDDEN_TOKENS = (
    "RunSqlTool",
    "PostgresRunner",
    "OpenAILlmService",
    "create_memory",
    "opencode.ai",
    "OPENCODE_API_KEY",
)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    for filename in ENTRYPOINTS:
        path = PROJECT_ROOT / filename
        source = path.read_text(encoding="utf-8")
        forbidden = [token for token in FORBIDDEN_TOKENS if token in source]
        process = subprocess.run(
            [sys.executable, str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        passed = (
            process.returncode != 0
            and EXPECTED_MESSAGE in process.stdout
            and not forbidden
        )
        detail = (
            f"returncode={process.returncode}, forbidden={forbidden}, "
            f"stdout={process.stdout.strip()}"
        )
        results.append((filename, passed, detail))

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
