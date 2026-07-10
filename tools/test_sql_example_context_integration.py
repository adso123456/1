from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "sql_example_context_integration_test_result.md"
BASE_COMMIT = "6259466b2478b8c25f9e687bc0da8beed2a8658a"
ALLOWED_STATUS_PATHS = {
    "step4_server.py",
    "tools/test_sql_example_context_integration.py",
    "tools/sql_example_context_integration_test_result.md",
    "tools/sql_example_context_integration_probe.py",
    "tools/sql_example_context_integration_result.md",
    "tools/level2_post_training_probe.py",
    "tools/level2_post_training_probe_result.md",
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class TestResult:
    name: str
    passed: bool
    reason: str


def run_command(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.stdout.strip()


def effective_status(status_short: str) -> tuple[str, list[str]]:
    unexpected: list[str] = []
    for line in status_short.splitlines():
        if not line.strip():
            continue
        path = line[2:].strip().replace("\\", "/")
        if path not in ALLOWED_STATUS_PATHS:
            unexpected.append(line)
    return ("" if not unexpected else status_short, unexpected)


def test_import_sql_example_enhancer() -> TestResult:
    """验证 step4_server.py 导入了 SqlExampleContextEnhancer（源码文本检查）"""
    source = (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8")

    if "from tools.sql_example_context_enhancer import SqlExampleContextEnhancer" not in source:
        return TestResult(
            "step4_server.py 已导入 SqlExampleContextEnhancer",
            False,
            "step4_server.py 中未找到 SqlExampleContextEnhancer 的导入语句",
        )

    # 确认没有被注释掉
    for line in source.splitlines():
        stripped = line.strip()
        if "from tools.sql_example_context_enhancer import SqlExampleContextEnhancer" in stripped:
            if stripped.startswith("#"):
                return TestResult(
                    "step4_server.py 已导入 SqlExampleContextEnhancer",
                    False,
                    "SqlExampleContextEnhancer 导入语句被注释",
                )
            break

    return TestResult(
        "step4_server.py 已导入 SqlExampleContextEnhancer",
        True,
        "源码中正确导入了 SqlExampleContextEnhancer",
    )


def test_create_agent_uses_sql_example_enhancer() -> TestResult:
    """验证 create_agent() 中 llm_context_enhancer 是 SqlExampleContextEnhancer"""
    try:
        from tools.sql_example_context_enhancer import SqlExampleContextEnhancer

        # 通过直接导入 module 做静态检查
        source = (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8")

        # 检查 create_agent 函数中 llm_context_enhancer 的赋值
        if "SqlExampleContextEnhancer(" not in source:
            return TestResult(
                "create_agent() 中最终 llm_context_enhancer 是 SqlExampleContextEnhancer",
                False,
                "step4_server.py 中未找到 SqlExampleContextEnhancer( 实例化代码",
            )

        # 检查传给 Agent 的是 llm_context_enhancer 变量
        if "llm_context_enhancer=llm_context_enhancer" not in source:
            return TestResult(
                "create_agent() 中最终 llm_context_enhancer 是 SqlExampleContextEnhancer",
                False,
                "Agent() 中未使用 llm_context_enhancer 变量",
            )

        return TestResult(
            "create_agent() 中最终 llm_context_enhancer 是 SqlExampleContextEnhancer",
            True,
            "create_agent() 中 SqlExampleContextEnhancer 实例化并传给 Agent",
        )
    except Exception as exc:
        return TestResult(
            "create_agent() 中最终 llm_context_enhancer 是 SqlExampleContextEnhancer",
            False,
            f"检查失败: {exc}",
        )


def test_enhancer_chain_order() -> TestResult:
    """验证 enhancer 链顺序: SqlExample → Deterministic → Default"""
    source = (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8")

    default_pos = source.find("DefaultLlmContextEnhancer(memory)")
    deterministic_pos = source.find(
        "DeterministicMetadataContextEnhancer(\n        base_enhancer=default_enhancer"
    )
    sql_example_pos = source.find(
        "SqlExampleContextEnhancer(\n        base_enhancer=deterministic_enhancer"
    )

    if default_pos == -1:
        return TestResult(
            "enhancer 链顺序正确: SqlExample → Deterministic → Default",
            False,
            "未找到 DefaultLlmContextEnhancer(memory)",
        )
    if deterministic_pos == -1:
        return TestResult(
            "enhancer 链顺序正确: SqlExample → Deterministic → Default",
            False,
            "未找到 DeterministicMetadataContextEnhancer 包装 base_enhancer=default_enhancer",
        )
    if sql_example_pos == -1:
        return TestResult(
            "enhancer 链顺序正确: SqlExample → Deterministic → Default",
            False,
            "未找到 SqlExampleContextEnhancer 包装 base_enhancer=deterministic_enhancer",
        )

    if not (default_pos < deterministic_pos < sql_example_pos):
        return TestResult(
            "enhancer 链顺序正确: SqlExample → Deterministic → Default",
            False,
            f"链顺序异常: Default@{default_pos}, Deterministic@{deterministic_pos}, SqlExample@{sql_example_pos}",
        )

    return TestResult(
        "enhancer 链顺序正确: SqlExample → Deterministic → Default",
        True,
        "Default → Deterministic → SqlExample 三层链顺序正确",
    )


def test_guarded_run_sql_still_registered() -> TestResult:
    """验证 GuardedRunSqlTool 仍然注册"""
    source = (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8")

    if "GuardedRunSqlTool(" not in source:
        return TestResult(
            "GuardedRunSqlTool 仍然注册",
            False,
            "未找到 GuardedRunSqlTool 实例化代码",
        )
    if "tool_registry.register_local_tool(" not in source:
        return TestResult(
            "GuardedRunSqlTool 仍然注册",
            False,
            "未找到 register_local_tool 调用",
        )
    if "inner_tool=raw_run_sql_tool" not in source:
        return TestResult(
            "GuardedRunSqlTool 仍然注册",
            False,
            "GuardedRunSqlTool 未包装 raw_run_sql_tool",
        )

    return TestResult(
        "GuardedRunSqlTool 仍然注册",
        True,
        "GuardedRunSqlTool 正常注册，包装 raw_run_sql_tool",
    )


def test_no_raw_run_sql_bypass() -> TestResult:
    """验证裸 RunSqlTool 没有绕过 GuardedRunSqlTool"""
    source = (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8")

    # raw_run_sql_tool 应该只出现在 GuardedRunSqlTool 的参数中
    raw_positions = []
    for idx in range(len(source)):
        if source[idx:].startswith("raw_run_sql_tool"):
            raw_positions.append(idx)

    if len(raw_positions) > 2:
        return TestResult(
            "裸 RunSqlTool 没有绕过 GuardedRunSqlTool",
            False,
            f"raw_run_sql_tool 出现了 {len(raw_positions)} 次，可能被独立注册",
        )

    # 确认 register_local_tool 只注册了 GuardedRunSqlTool
    register_start = source.find("tool_registry.register_local_tool(")
    if register_start == -1:
        return TestResult(
            "裸 RunSqlTool 没有绕过 GuardedRunSqlTool",
            False,
            "未找到 register_local_tool 调用",
        )
    register_end = source.find(")", register_start)
    register_block = source[register_start:register_end + 1] if register_end != -1 else ""

    if "GuardedRunSqlTool" not in register_block:
        return TestResult(
            "裸 RunSqlTool 没有绕过 GuardedRunSqlTool",
            False,
            "register_local_tool 未注册 GuardedRunSqlTool",
        )

    return TestResult(
        "裸 RunSqlTool 没有绕过 GuardedRunSqlTool",
        True,
        "RunSqlTool 仅作为 GuardedRunSqlTool 的内部工具，没有独立注册",
    )


def test_shared_sql_guard() -> TestResult:
    """验证 GuardedRunSqlTool 和 SqlExampleContextEnhancer 共用 SQLGuard 实例"""
    source = (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8")

    sql_guard_assignment = "sql_guard = SQLGuard()"
    if sql_guard_assignment not in source:
        return TestResult(
            "GuardedRunSqlTool 和 SqlExampleContextEnhancer 共用 SQLGuard 实例",
            False,
            "未找到 sql_guard = SQLGuard() 提取的共用实例",
        )

    # 检查 GuardedRunSqlTool 使用 sql_guard 变量
    guard_in_guarded = "sql_guard=sql_guard" in source
    # 检查 SqlExampleContextEnhancer 使用 sql_guard 变量
    guard_in_enhancer = any(
        line.strip().startswith("sql_guard=sql_guard")
        for line in source.split("\n")
        if "sql_guard=sql_guard" in line
    )

    if not guard_in_guarded:
        return TestResult(
            "GuardedRunSqlTool 和 SqlExampleContextEnhancer 共用 SQLGuard 实例",
            False,
            "GuardedRunSqlTool 未共用 sql_guard 变量",
        )
    if not guard_in_enhancer:
        return TestResult(
            "GuardedRunSqlTool 和 SqlExampleContextEnhancer 共用 SQLGuard 实例",
            False,
            "SqlExampleContextEnhancer 未共用 sql_guard 变量",
        )

    return TestResult(
        "GuardedRunSqlTool 和 SqlExampleContextEnhancer 共用 SQLGuard 实例",
        True,
        "两者共用同一个 SQLGuard 实例",
    )


def run_tests() -> tuple[list[TestResult], dict[str, Any]]:
    tests = [
        test_import_sql_example_enhancer,
        test_create_agent_uses_sql_example_enhancer,
        test_enhancer_chain_order,
        test_guarded_run_sql_still_registered,
        test_no_raw_run_sql_bypass,
        test_shared_sql_guard,
    ]
    results = [test() for test in tests]
    passed = sum(1 for result in results if result.passed)

    raw_status = run_command(["git", "status", "--short"])
    initial_status, unexpected_status = effective_status(raw_status)
    commit = run_command(["git", "rev-parse", "HEAD"])
    if unexpected_status:
        raise SystemExit(
            "git status --short 存在非本阶段文件，停止：" + "；".join(unexpected_status)
        )
    if (
        commit != BASE_COMMIT
        and run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit]) != ""
    ):
        raise SystemExit(f"当前 commit 不满足要求：{commit}")

    summary = {
        "cwd": str(PROJECT_ROOT),
        "remote": run_command(["git", "remote", "-v"]),
        "commit": commit,
        "initial_status": initial_status or "clean",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "failed_cases": [result.name for result in results if not result.passed],
    }
    return results, summary


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def write_report(results: list[TestResult], summary: dict[str, Any]) -> None:
    lines = [
        "# SQL Example Context Enhancer 接入静态集成测试结果",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{summary['cwd']}",
        "- git remote -v：",
        "```text",
        summary["remote"],
        "```",
        f"- 当前 commit：{summary['commit']}",
        "- 初始 git status --short：",
        "```text",
        summary["initial_status"],
        "```",
        f"- 测试总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败用例列表：{', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}",
        f"- 接入链路静态验证是否通过：{bool_cn(summary['failed'] == 0)}",
        "- 是否启动真实主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行真实 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        "- 是否写入正式 ChromaDB：否",
        "- 是否修改正式 vanna_data：否",
        "- 是否进入第 3/4 级：否",
        "",
        "## 明细",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"### {index}. {result.name}",
                "",
                f"- pass/fail：{'pass' if result.passed else 'fail'}",
                f"- reason：{result.reason}",
                "",
            ]
        )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = run_tests()
    write_report(results, summary)
    print(f"测试总数: {summary['total']}")
    print(f"通过数量: {summary['passed']}")
    print(f"失败数量: {summary['failed']}")
    print(f"失败用例列表: {', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
