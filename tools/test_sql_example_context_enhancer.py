from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "sql_example_context_enhancer_test_result.md"
BASE_COMMIT = "944590cdf80eff70b458353a9af0039ae522237b"
ALLOWED_STATUS_PATHS = {
    "tools/sql_example_context_enhancer.py",
    "tools/test_sql_example_context_enhancer.py",
    "tools/sql_example_context_enhancer_test_result.md",
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_example_context_enhancer import SqlExampleContextEnhancer


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


def memory_item(
    *,
    sample_id: str,
    question: str,
    sql: str,
    train_decision: str = "approved",
    training_level: str = "level2_sql_examples",
    tool_name: str = "run_sql",
) -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(
            question=question,
            tool_name=tool_name,
            args={"sql": sql},
            metadata={
                "training_level": training_level,
                "sample_id": sample_id,
                "train_decision": train_decision,
            },
        )
    )


SAMPLES = {
    "L2_SQL_003": memory_item(
        sample_id="L2_SQL_003",
        question="查询某站点水质日趋势中的 pH 和溶解氧变化",
        sql=(
            "SELECT station_id, monitor_time, m2_value, m3_value "
            "FROM wm_waterquality_day_records "
            "WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL "
            "ORDER BY monitor_time LIMIT 100"
        ),
    ),
    "L2_SQL_007": memory_item(
        sample_id="L2_SQL_007",
        question="某站点水质月变化趋势",
        sql=(
            "SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level "
            "FROM wm_waterquality_month_records "
            "WHERE station_id = 1408 AND monitor_year >= 2025 "
            "ORDER BY monitor_year, monitor_month LIMIT 60"
        ),
    ),
    "L2_SQL_015": memory_item(
        sample_id="L2_SQL_015",
        question="查询站点名称和所属区域",
        sql=(
            "SELECT station_code, station_name, region_code, region_name, station_type "
            "FROM wm_station_info_v2 ORDER BY station_name LIMIT 50"
        ),
    ),
}


class FakeBaseEnhancer:
    def __init__(self) -> None:
        self.called = False

    async def enhance_system_prompt(
        self, system_prompt: str, user_message: str, user: Any
    ) -> str:
        self.called = True
        return system_prompt + "\nBASE_CONTEXT"

    async def enhance_user_messages(self, messages: list[Any], user: Any) -> list[Any]:
        return messages


class FakeMemory:
    def __init__(self, results: list[SimpleNamespace], *, honor_limit: bool = True) -> None:
        self.results = results
        self.honor_limit = honor_limit
        self.called = False
        self.last_tool_name_filter = ""
        self.last_limit = 0

    async def search_similar_usage(
        self,
        *,
        question: str,
        context: Any,
        limit: int = 10,
        tool_name_filter: str | None = None,
    ) -> list[SimpleNamespace]:
        self.called = True
        self.last_tool_name_filter = tool_name_filter or ""
        self.last_limit = limit
        if self.honor_limit:
            return self.results[:limit]
        return self.results


async def build_prompt(
    query: str,
    results: list[SimpleNamespace],
    *,
    top_k: int = 5,
    honor_limit: bool = True,
) -> tuple[str, SqlExampleContextEnhancer, FakeBaseEnhancer, FakeMemory]:
    base = FakeBaseEnhancer()
    memory = FakeMemory(results, honor_limit=honor_limit)
    enhancer = SqlExampleContextEnhancer(base_enhancer=base, memory=memory, top_k=top_k)
    prompt = await enhancer.enhance_system_prompt("SYSTEM", query, user=None)
    return prompt, enhancer, base, memory


async def test_base_called() -> TestResult:
    _, _, base, _ = await build_prompt("某站点水质月变化趋势", [SAMPLES["L2_SQL_007"]])
    return TestResult("会调用 base enhancer", base.called, "base_enhancer.enhance_system_prompt 被调用" if base.called else "base enhancer 未调用")


async def test_search_called() -> TestResult:
    _, _, _, memory = await build_prompt("某站点水质月变化趋势", [SAMPLES["L2_SQL_007"]])
    passed = memory.called and memory.last_tool_name_filter == "run_sql"
    return TestResult("会调用 search_similar_usage", passed, f"called={memory.called}, tool_name_filter={memory.last_tool_name_filter}")


async def test_q3_month_example() -> TestResult:
    prompt, _, _, _ = await build_prompt("某站点水质月变化趋势", [SAMPLES["L2_SQL_007"]])
    expected = ["L2_SQL_007", "wm_waterquality_month_records", "monitor_year", "monitor_month", "m2_value", "m3_value"]
    passed = all(item in prompt for item in expected)
    return TestResult("Q3 月趋势 approved 示例进入 prompt", passed, "关键内容均已出现" if passed else "缺少：" + ", ".join(item for item in expected if item not in prompt))


async def test_q1_day_example() -> TestResult:
    prompt, _, _, _ = await build_prompt("查询某站点水质日趋势中的 pH 和溶解氧变化", [SAMPLES["L2_SQL_003"]])
    expected = ["L2_SQL_003", "wm_waterquality_day_records", "m2_value", "m3_value"]
    passed = all(item in prompt for item in expected)
    return TestResult("Q1 日趋势 approved 示例进入 prompt", passed, "关键内容均已出现" if passed else "缺少：" + ", ".join(item for item in expected if item not in prompt))


async def test_q6_station_example() -> TestResult:
    prompt, _, _, _ = await build_prompt("查询站点名称和所属区域", [SAMPLES["L2_SQL_015"]])
    expected = ["L2_SQL_015", "wm_station_info_v2", "station_name", "region_name"]
    passed = all(item in prompt for item in expected)
    return TestResult("Q6 站点信息 approved 示例进入 prompt", passed, "关键内容均已出现" if passed else "缺少：" + ", ".join(item for item in expected if item not in prompt))


async def test_manual_review_filtered() -> TestResult:
    manual = [
        memory_item(sample_id="L2_SQL_011", question="排污口溯源责任主体统计", sql="SELECT primary_entity_name FROM rs_outlet_trace_v2 LIMIT 50", train_decision="requires_manual_review"),
        memory_item(sample_id="L2_SQL_012", question="查询排污口溯源企业和排放许可证", sql="SELECT outlet_name, primary_entity_name FROM rs_outlet_trace_v2 LIMIT 50", train_decision="requires_manual_review"),
        memory_item(sample_id="L2_SQL_019", question="查询水源地取水口供水能力", sql="SELECT name, daily_supply_capacity FROM wm_water_source_intake_v2 LIMIT 50", train_decision="requires_manual_review"),
    ]
    prompt, enhancer, _, _ = await build_prompt("排污口溯源", manual)
    passed = all(sample_id not in prompt for sample_id in ["L2_SQL_011", "L2_SQL_012", "L2_SQL_019"]) and enhancer.last_stats.injected_count == 0
    return TestResult("requires_manual_review 不进入 prompt", passed, f"injected_count={enhancer.last_stats.injected_count}")


async def test_guard_warning_filtered() -> TestResult:
    warning = memory_item(
        sample_id="L2_SQL_WARNING",
        question="查询水源地取水口供水能力",
        sql="SELECT name, daily_supply_capacity FROM wm_water_source_intake_v2 LIMIT 50",
    )
    prompt, enhancer, _, _ = await build_prompt("查询水源地取水口供水能力", [warning])
    passed = "L2_SQL_WARNING" not in prompt and enhancer.last_stats.injected_count == 0
    return TestResult("SQL Guard warning 不进入 prompt", passed, f"filtered={enhancer.last_stats.filtered}")


async def test_select_star_filtered() -> TestResult:
    select_star = memory_item(
        sample_id="L2_SQL_SELECT_STAR",
        question="查询取水口名称和水源类型",
        sql="SELECT * FROM wm_water_intake LIMIT 5",
    )
    prompt, enhancer, _, _ = await build_prompt("查询取水口名称和水源类型", [select_star])
    passed = "L2_SQL_SELECT_STAR" not in prompt and enhancer.last_stats.injected_count == 0
    return TestResult("SELECT * 不进入 prompt", passed, f"filtered={enhancer.last_stats.filtered}")


async def test_empty_recall_keeps_prompt() -> TestResult:
    prompt, enhancer, _, _ = await build_prompt("无召回", [])
    passed = prompt == "SYSTEM\nBASE_CONTEXT" and "Retrieved Approved SQL Examples" not in prompt and enhancer.last_stats.injected_count == 0
    return TestResult("无召回结果时不破坏原 prompt", passed, f"prompt={prompt!r}, injected_count={enhancer.last_stats.injected_count}")


async def test_top_k_limit() -> TestResult:
    results = [
        memory_item(
            sample_id=f"L2_SQL_TOP_{index}",
            question="查询站点名称和所属区域",
            sql="SELECT station_code, station_name, region_code, region_name FROM wm_station_info_v2 ORDER BY station_name LIMIT 50",
        )
        for index in range(10)
    ]
    prompt, enhancer, _, memory = await build_prompt("查询站点名称和所属区域", results, top_k=3, honor_limit=False)
    injected_ids = [f"L2_SQL_TOP_{index}" for index in range(10) if f"L2_SQL_TOP_{index}" in prompt]
    passed = memory.last_limit == 3 and enhancer.last_stats.injected_count == 3 and len(injected_ids) == 3
    return TestResult("top_k 限制生效", passed, f"last_limit={memory.last_limit}, injected_count={enhancer.last_stats.injected_count}, injected_ids={injected_ids}")


async def run_tests() -> tuple[list[TestResult], dict[str, Any]]:
    tests = [
        test_base_called,
        test_search_called,
        test_q3_month_example,
        test_q1_day_example,
        test_q6_station_example,
        test_manual_review_filtered,
        test_guard_warning_filtered,
        test_select_star_filtered,
        test_empty_recall_keeps_prompt,
        test_top_k_limit,
    ]
    results = [await test() for test in tests]
    passed = sum(1 for result in results if result.passed)

    raw_status = run_command(["git", "status", "--short"])
    initial_status, unexpected_status = effective_status(raw_status)
    commit = run_command(["git", "rev-parse", "HEAD"])
    if unexpected_status:
        raise SystemExit("git status --short 存在非本阶段文件，停止：" + "；".join(unexpected_status))
    if commit != BASE_COMMIT and run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit]) != "":
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
        "called_base_enhancer": any(result.name == "会调用 base enhancer" and result.passed for result in results),
        "called_search_similar_usage": any(result.name == "会调用 search_similar_usage" and result.passed for result in results),
        "tool_name_filter_run_sql": any(result.name == "会调用 search_similar_usage" and "tool_name_filter=run_sql" in result.reason for result in results),
        "approved_examples_enter_prompt": all(
            any(result.name.startswith(prefix) and result.passed for result in results)
            for prefix in ["Q1", "Q3", "Q6"]
        ),
        "manual_review_filtered": any(result.name == "requires_manual_review 不进入 prompt" and result.passed for result in results),
        "select_star_filtered": any(result.name == "SELECT * 不进入 prompt" and result.passed for result in results),
        "guard_warning_filtered": any(result.name == "SQL Guard warning 不进入 prompt" and result.passed for result in results),
    }
    return results, summary


def write_report(results: list[TestResult], summary: dict[str, Any]) -> None:
    lines = [
        "# SQL Example Context Enhancer 测试结果",
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
        f"- 是否调用 base enhancer：{'是' if summary['called_base_enhancer'] else '否'}",
        f"- 是否调用 search_similar_usage：{'是' if summary['called_search_similar_usage'] else '否'}",
        f"- tool_name_filter 是否为 run_sql：{'是' if summary['tool_name_filter_run_sql'] else '否'}",
        f"- approved 示例是否进入 prompt：{'是' if summary['approved_examples_enter_prompt'] else '否'}",
        f"- requires_manual_review 是否被过滤：{'是' if summary['manual_review_filtered'] else '否'}",
        f"- SELECT * 是否被过滤：{'是' if summary['select_star_filtered'] else '否'}",
        f"- SQL Guard warning/error 是否被过滤：{'是' if summary['guard_warning_filtered'] else '否'}",
        "- 是否启动真实主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行真实 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        "- 是否写入正式 ChromaDB：否",
        "- 是否修改正式 vanna_data：否",
        "- 是否进入第 3/4 级：否",
        f"- 当前结论：{'通过' if summary['failed'] == 0 else '未通过'}",
        "- 下一阶段建议：如需主服务接入，另起阶段用该 enhancer 包装现有 context enhancer，并先做 fake/isolated 验证。",
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
                "- 是否启动真实主服务：否",
                "- 是否连接数据库：否",
                "- 是否执行真实 SQL：否",
                "- 是否调用 DeepSeek：否",
                "",
            ]
        )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = asyncio.run(run_tests())
    write_report(results, summary)
    print(f"测试总数: {summary['total']}")
    print(f"通过数量: {summary['passed']}")
    print(f"失败数量: {summary['failed']}")
    print(f"失败用例列表: {', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
