"""PostgreSQL Runtime 中 SQL Example Enhancer 的离线集成测试。"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "sql_example_context_integration_test_result.md"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vanna.core.enhancer.default import DefaultLlmContextEnhancer

from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.metadata_context_enhancer import (
    DeterministicMetadataContextEnhancer,
)
from backend.postgresql_runtime_factory import _create_postgresql_agent
from backend.schema_preserving_sql import SchemaPreservingRunSqlTool
from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
from config.data_source_config import DataSourceConfig


@dataclass
class TestResult:
    name: str
    passed: bool
    reason: str


class FakeRetriever:
    def retrieve(self, question: str, top_n: int = 10) -> list[dict[str, Any]]:
        return []


class FakeSqlGuard:
    pass


def build_agent(*, disable_legacy_sql_examples: bool):
    root = Path(tempfile.gettempdir()).resolve()
    config = DataSourceConfig(
        source_id="postgresql-test",
        database_type="postgresql",
        sql_dialect="postgresql",
        connection_settings={
            "host": "offline.invalid",
            "port": 5433,
            "database": "offline",
            "user": "offline",
            "password": "offline-secret",
            "connect_timeout": 1,
        },
        metadata_path=(root / "stage4-metadata-not-opened.json").resolve(),
        memory_path=(root / "stage4-memory-not-opened").resolve(),
        read_only=True,
    )
    runner = object()
    memory = object()
    retriever = FakeRetriever()
    sql_guard = FakeSqlGuard()
    agent = _create_postgresql_agent(
        config,
        runner,
        memory,
        retriever,
        sql_guard,
        {
            "DEEPSEEK_API_KEY": "offline-test-key",
            "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": (
                "1" if disable_legacy_sql_examples else "0"
            ),
        },
    )
    return agent, runner, memory, retriever, sql_guard


def test_sql_example_enhancer_used() -> TestResult:
    agent, _, _, _, _ = build_agent(disable_legacy_sql_examples=False)
    enhancer = agent.llm_context_enhancer
    return TestResult(
        "PostgreSQL Agent 使用 SqlExampleContextEnhancer",
        isinstance(enhancer, SqlExampleContextEnhancer),
        type(enhancer).__name__,
    )


def test_enhancer_chain_order() -> TestResult:
    agent, _, memory, retriever, _ = build_agent(
        disable_legacy_sql_examples=False
    )
    sql_example = agent.llm_context_enhancer
    deterministic = getattr(sql_example, "base_enhancer", None)
    default = getattr(deterministic, "base_enhancer", None)
    passed = (
        isinstance(sql_example, SqlExampleContextEnhancer)
        and isinstance(deterministic, DeterministicMetadataContextEnhancer)
        and isinstance(default, DefaultLlmContextEnhancer)
        and deterministic.metadata_retriever is retriever
        and sql_example.memory is memory
    )
    return TestResult(
        "Enhancer 链顺序为 Default → Deterministic → SqlExample",
        passed,
        (
            f"{type(default).__name__} → {type(deterministic).__name__} "
            f"→ {type(sql_example).__name__}"
        ),
    )


def test_guarded_run_sql_registered() -> TestResult:
    agent, runner, _, _, _ = build_agent(
        disable_legacy_sql_examples=False
    )
    registered = agent.tool_registry._tools
    tool = registered.get("run_sql")
    passed = (
        tuple(registered) == ("run_sql",)
        and isinstance(tool, GuardedRunSqlTool)
        and isinstance(tool.inner_tool, SchemaPreservingRunSqlTool)
        and tool.inner_tool.sql_runner is runner
    )
    return TestResult(
        "ToolRegistry 仅注册 GuardedRunSqlTool",
        passed,
        f"registered={tuple(registered)}, tool={type(tool).__name__}",
    )


def test_no_raw_run_sql_bypass() -> TestResult:
    agent, _, _, _, _ = build_agent(disable_legacy_sql_examples=False)
    registered = tuple(agent.tool_registry._tools.values())
    passed = all(
        not isinstance(tool, SchemaPreservingRunSqlTool)
        for tool in registered
    ) and any(isinstance(tool, GuardedRunSqlTool) for tool in registered)
    return TestResult(
        "裸 SchemaPreservingRunSqlTool 未独立注册",
        passed,
        ", ".join(type(tool).__name__ for tool in registered),
    )


def test_shared_sql_guard() -> TestResult:
    agent, _, _, _, sql_guard = build_agent(
        disable_legacy_sql_examples=False
    )
    guarded_tool = agent.tool_registry._tools["run_sql"]
    enhancer = agent.llm_context_enhancer
    passed = (
        guarded_tool.sql_guard is sql_guard
        and enhancer.sql_guard is sql_guard
    )
    return TestResult(
        "GuardedRunSqlTool 与 SqlExampleContextEnhancer 共用 SQLGuard",
        passed,
        (
            f"guarded={guarded_tool.sql_guard is sql_guard}, "
            f"enhancer={enhancer.sql_guard is sql_guard}"
        ),
    )


def test_legacy_disabled_falls_back_to_deterministic() -> TestResult:
    agent, _, _, retriever, _ = build_agent(
        disable_legacy_sql_examples=True
    )
    enhancer = agent.llm_context_enhancer
    passed = (
        isinstance(enhancer, DeterministicMetadataContextEnhancer)
        and not isinstance(enhancer, SqlExampleContextEnhancer)
        and enhancer.metadata_retriever is retriever
        and isinstance(enhancer.base_enhancer, DefaultLlmContextEnhancer)
    )
    return TestResult(
        "关闭 legacy SQL examples 时回退 deterministic enhancer",
        passed,
        type(enhancer).__name__,
    )


def run_tests() -> tuple[list[TestResult], dict[str, Any]]:
    tests = [
        test_sql_example_enhancer_used,
        test_enhancer_chain_order,
        test_guarded_run_sql_registered,
        test_no_raw_run_sql_bypass,
        test_shared_sql_guard,
        test_legacy_disabled_falls_back_to_deterministic,
    ]
    results = [test() for test in tests]
    passed = sum(result.passed for result in results)
    return results, {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "failed_cases": [
            result.name for result in results if not result.passed
        ],
    }


def write_report(results: list[TestResult], summary: dict[str, Any]) -> None:
    lines = [
        "# SQL Example Context Enhancer Runtime 集成测试",
        "",
        f"- 总数：{summary['total']}",
        f"- 通过：{summary['passed']}",
        f"- 失败：{summary['failed']}",
        "- 执行 SQL：否",
        "- 调用 LLM：否",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result.name}",
                "",
                f"- 结果：{'PASS' if result.passed else 'FAIL'}",
                f"- 证据：{result.reason}",
                "",
            ]
        )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = run_tests()
    write_report(results, summary)
    for result in results:
        print(
            f"[{'PASS' if result.passed else 'FAIL'}] "
            f"{result.name}: {result.reason}"
        )
    print(
        f"total={summary['total']} passed={summary['passed']} "
        f"failed={summary['failed']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
