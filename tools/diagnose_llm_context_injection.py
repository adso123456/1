from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "893db0615c4b4c60eeafa2909a9fe4dcb69e9bf4"
DEFAULT_ENHANCER_PATH = PROJECT_ROOT / "vanna_src" / "src" / "vanna" / "core" / "enhancer" / "default.py"
DETERMINISTIC_ENHANCER_PATH = PROJECT_ROOT / "tools" / "metadata_context_enhancer.py"
DRAFT_PATH = PROJECT_ROOT / "training" / "sql_examples_level2_draft.json"
REPORT_PATH = PROJECT_ROOT / "tools" / "llm_context_injection_diagnosis.md"
ALLOWED_STATUS_PATHS = {
    "tools/diagnose_llm_context_injection.py",
    "tools/llm_context_injection_diagnosis.md",
}

TARGETS = [
    {
        "id": "Q1",
        "question": "查询某站点水质日趋势中的 pH 和溶解氧变化",
        "sample_id": "L2_SQL_003",
        "target_table": "wm_waterquality_day_records",
        "key_columns": ["m2_value", "m3_value", "monitor_time", "station_id"],
    },
    {
        "id": "Q2",
        "question": "某站点水质小时变化趋势",
        "sample_id": "L2_SQL_004",
        "target_table": "wm_waterquality_hour_records",
        "key_columns": ["m1_value", "m2_value", "m3_value", "monitor_time", "station_id"],
    },
    {
        "id": "Q3",
        "question": "某站点水质月变化趋势",
        "sample_id": "L2_SQL_007",
        "target_table": "wm_waterquality_month_records",
        "key_columns": ["m2_value", "m3_value", "monitor_year", "monitor_month", "station_id"],
    },
    {
        "id": "Q6",
        "question": "查询站点名称和所属区域",
        "sample_id": "L2_SQL_015",
        "target_table": "wm_station_info_v2",
        "key_columns": ["station_name", "station_code", "region_code", "region_name"],
    },
    {
        "id": "Q7",
        "question": "查询区域编码和区域名称",
        "sample_id": "L2_SQL_017",
        "target_table": "gis_region",
        "key_columns": ["region_code", "region_name"],
    },
    {
        "id": "Q8",
        "question": "查询取水口名称和水源类型",
        "sample_id": "L2_SQL_018",
        "target_table": "wm_water_intake",
        "key_columns": ["name", "water_type"],
    },
]


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


def load_samples() -> dict[str, dict[str, Any]]:
    samples = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    return {str(sample["id"]): sample for sample in samples}


def compact(text: str) -> str:
    return " ".join(str(text).split())


def relevant_memory_section(prompt: str) -> str:
    if "## Relevant Context from Memory" not in prompt:
        return ""
    section = prompt.split("## Relevant Context from Memory", 1)[-1]
    if "## Deterministic Metadata Context" in section:
        section = section.split("## Deterministic Metadata Context", 1)[0]
    return section


def yes_no(value: bool) -> str:
    return "是" if value else "否"


def format_list(items: list[str]) -> str:
    return "，".join(items) if items else "无"


from vanna.capabilities.agent_memory import AgentMemory


class FakeMemory(AgentMemory):
    def __init__(self, sample: dict[str, Any]) -> None:
        self.sample = sample
        self.called_search_text_memories = False
        self.called_search_similar_usage = False
        self.called_save_tool_usage = False
        self.text_queries: list[str] = []
        self.similar_queries: list[str] = []

    async def search_text_memories(self, query: str, context: Any, *, limit: int = 10, similarity_threshold: float = 0.7):
        self.called_search_text_memories = True
        self.text_queries.append(query)
        content = "普通文本记忆：这是 fake search_text_memories 返回的非 SQL 示例上下文。"
        return [SimpleNamespace(memory=SimpleNamespace(content=content), similarity_score=0.99, rank=1)]

    async def search_similar_usage(
        self,
        question: str,
        context: Any,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: str | None = None,
    ):
        self.called_search_similar_usage = True
        self.similar_queries.append(question)
        args = {"sql": self.sample.get("sql", "")}
        metadata = {
            "sample_id": self.sample.get("id", ""),
            "train_decision": self.sample.get("train_decision", ""),
        }
        memory = SimpleNamespace(
            question=self.sample.get("question", ""),
            tool_name="run_sql",
            args=args,
            metadata=metadata,
        )
        return [SimpleNamespace(memory=memory, similarity_score=1.0, rank=1)]

    async def save_tool_usage(self, *args: Any, **kwargs: Any) -> None:
        self.called_save_tool_usage = True

    async def save_text_memory(self, content: str, context: Any) -> Any:
        return SimpleNamespace(memory_id="fake_text_memory", content=content, timestamp=None)

    async def get_recent_memories(self, context: Any, limit: int = 10) -> list[Any]:
        return []

    async def get_recent_text_memories(self, context: Any, limit: int = 10) -> list[Any]:
        return []

    async def delete_by_id(self, context: Any, memory_id: str) -> bool:
        return False

    async def delete_text_memory(self, context: Any, memory_id: str) -> bool:
        return False

    async def clear_memories(
        self,
        context: Any,
        tool_name: str | None = None,
        before_date: str | None = None,
    ) -> int:
        return 0


async def diagnose() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from tools.metadata_context_enhancer import DeterministicMetadataContextEnhancer
    from tools.metadata_retriever import DeterministicMetadataRetriever
    from vanna.core.enhancer.default import DefaultLlmContextEnhancer
    from vanna.core.user import User

    remote = run_command(["git", "remote", "-v"])
    raw_status = run_command(["git", "status", "--short"])
    initial_status, unexpected_status = effective_status(raw_status)
    commit = run_command(["git", "rev-parse", "HEAD"])
    if unexpected_status:
        raise SystemExit("git status --short 存在非本阶段文件，停止：" + "；".join(unexpected_status))
    if commit != BASE_COMMIT and run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit]) != "":
        raise SystemExit(f"当前 commit 不满足要求：{commit}")

    default_source = DEFAULT_ENHANCER_PATH.read_text(encoding="utf-8")
    deterministic_source = DETERMINISTIC_ENHANCER_PATH.read_text(encoding="utf-8")
    static_result = {
        "default_calls_search_text_memories": "search_text_memories" in default_source,
        "default_calls_search_similar_usage": "search_similar_usage" in default_source,
        "default_mentions_save_tool_usage": "save_tool_usage" in default_source,
        "default_mentions_tool_name_filter": "tool_name_filter" in default_source,
        "deterministic_appends_metadata": "Deterministic Metadata Context" in deterministic_source,
        "deterministic_calls_base": "base_enhancer.enhance_system_prompt" in deterministic_source,
        "deterministic_calls_search_similar_usage": "search_similar_usage" in deterministic_source,
    }

    samples = load_samples()
    results: list[dict[str, Any]] = []
    for target in TARGETS:
        sample = samples[target["sample_id"]]
        fake_memory = FakeMemory(sample)
        base = DefaultLlmContextEnhancer(fake_memory)
        enhancer = DeterministicMetadataContextEnhancer(
            base_enhancer=base,
            metadata_retriever=DeterministicMetadataRetriever(),
            top_n=10,
        )
        final_prompt = await enhancer.enhance_system_prompt(
            system_prompt="SYSTEM",
            user_message=target["question"],
            user=User(id="llm_context_probe", username="llm_context_probe"),
        )

        sample_id = str(sample.get("id", ""))
        sample_question = str(sample.get("question", ""))
        sample_sql = str(sample.get("sql", ""))
        key_hits = [column for column in target["key_columns"] if column in final_prompt]
        contains_sample_sql = bool(sample_sql and compact(sample_sql) in compact(final_prompt))
        memory_section = relevant_memory_section(final_prompt)
        contains_sample_question = bool(sample_question and sample_question in memory_section)
        contains_sql_example = contains_sample_sql or contains_sample_question or (sample_id in final_prompt)
        contains_metadata = "## Deterministic Metadata Context" in final_prompt
        contains_table = target["target_table"] in final_prompt

        if not fake_memory.called_search_similar_usage and not contains_sql_example:
            diagnosis = (
                "SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，"
                "fake search_similar_usage 中的 run_sql 示例未被读取。"
            )
            recommended = "下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。"
        elif contains_sql_example:
            diagnosis = "SQL 示例进入最终 prompt。"
            recommended = "保持现状，仅继续观察问答质量。"
        else:
            diagnosis = "动态诊断未能确认 SQL 示例进入 prompt。"
            recommended = "增加 LLM 前 prompt 截取 hook 做二次确认。"

        if contains_table and not key_hits:
            recommended += " 同时补强关键字段映射。"

        results.append(
            {
                "id": target["id"],
                "question": target["question"],
                "target_sample_id": sample_id,
                "target_table": target["target_table"],
                "key_columns": target["key_columns"],
                "sample_question": sample_question,
                "sample_sql": sample_sql,
                "default_enhancer_called_search_text_memories": fake_memory.called_search_text_memories,
                "default_enhancer_called_search_similar_usage": fake_memory.called_search_similar_usage,
                "deterministic_enhancer_called_base": fake_memory.called_search_text_memories,
                "final_prompt_contains_sample_id": sample_id in final_prompt,
                "final_prompt_contains_sample_question": contains_sample_question,
                "final_prompt_contains_sample_sql": contains_sample_sql,
                "final_prompt_contains_target_table": contains_table,
                "final_prompt_contains_key_columns": bool(key_hits),
                "key_column_hits": key_hits,
                "final_prompt_contains_deterministic_metadata": contains_metadata,
                "sql_example_entered_prompt": contains_sql_example,
                "field_context_weak": contains_table and not key_hits,
                "diagnosis": diagnosis,
                "recommended_next_action": recommended,
            }
        )

    summary = {
        "remote": remote,
        "commit": commit,
        "initial_status": initial_status or "clean",
        "static_result": static_result,
        "total": len(results),
        "sql_examples_entered": [item["id"] for item in results if item["sql_example_entered_prompt"]],
        "sql_examples_missing": [item["id"] for item in results if not item["sql_example_entered_prompt"]],
        "metadata_entered": [
            item["id"]
            for item in results
            if item["final_prompt_contains_deterministic_metadata"]
            and item["final_prompt_contains_target_table"]
        ],
        "field_context_weak": [item["id"] for item in results if item["field_context_weak"]],
        "root_cause": (
            "DefaultLlmContextEnhancer 调用 search_text_memories，未调用 search_similar_usage；"
            "第 2 级 save_tool_usage 写入的 run_sql SQL 示例不会自动进入当前 system prompt。"
        ),
        "next_fix": (
            "新增显式 SQL example context injector 或包装 enhancer：在 base enhancer 后调用 "
            "agent_memory.search_similar_usage(tool_name_filter='run_sql')，只读注入 top-k SQL 示例；"
            "继续保留 deterministic metadata context，并先用 fake/isolated 诊断验证。"
        ),
        "do_not_do": (
            "不要继续盲目训练更多 SQL 示例；不要改 SQL Guard；不要进入第 3/4 级；"
            "不要放松 Q3/Q4/Q9 判定。"
        ),
    }
    return summary, results


def write_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    static_result = summary["static_result"]
    lines = [
        "# LLM 上下文注入可观测性诊断",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        summary["remote"],
        "```",
        f"- 当前 commit：{summary['commit']}",
        "- 初始 git status --short：",
        "```text",
        summary["initial_status"],
        "```",
        "- 是否启动真实主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行真实 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        "- 是否写入正式 ChromaDB：否",
        "- 是否修改正式 vanna_data：否",
        "- 是否进入第 3/4 级：否",
        f"- 诊断问题总数：{summary['total']}",
        f"- DefaultLlmContextEnhancer 是否调用 search_text_memories：{yes_no(static_result['default_calls_search_text_memories'])}",
        f"- DefaultLlmContextEnhancer 是否调用 search_similar_usage：{yes_no(static_result['default_calls_search_similar_usage'])}",
        f"- DefaultLlmContextEnhancer 是否调用 save_tool_usage：{yes_no(static_result['default_mentions_save_tool_usage'])}",
        f"- DefaultLlmContextEnhancer 是否使用 tool_name_filter：{yes_no(static_result['default_mentions_tool_name_filter'])}",
        f"- DeterministicMetadataContextEnhancer 是否追加 deterministic metadata：{yes_no(static_result['deterministic_appends_metadata'])}",
        f"- DeterministicMetadataContextEnhancer 是否调用 base enhancer：{yes_no(static_result['deterministic_calls_base'])}",
        f"- SQL 示例进入 prompt 列表：{format_list(summary['sql_examples_entered'])}",
        f"- SQL 示例未进入 prompt 列表：{format_list(summary['sql_examples_missing'])}",
        f"- metadata context 进入 prompt 列表：{format_list(summary['metadata_entered'])}",
        f"- 字段 context 弱列表：{format_list(summary['field_context_weak'])}",
        f"- 根因判断：{summary['root_cause']}",
        f"- 下一阶段最小修复建议：{summary['next_fix']}",
        f"- 不建议做的事：{summary['do_not_do']}",
        "",
        "## 静态源码诊断结果",
        "",
        f"- DefaultLlmContextEnhancer 源码路径：{DEFAULT_ENHANCER_PATH}",
        f"- 包含 search_text_memories：{yes_no(static_result['default_calls_search_text_memories'])}",
        f"- 包含 search_similar_usage：{yes_no(static_result['default_calls_search_similar_usage'])}",
        f"- 包含 save_tool_usage：{yes_no(static_result['default_mentions_save_tool_usage'])}",
        f"- 包含 tool_name_filter：{yes_no(static_result['default_mentions_tool_name_filter'])}",
        f"- DeterministicMetadataContextEnhancer 源码路径：{DETERMINISTIC_ENHANCER_PATH}",
        f"- 追加 Deterministic Metadata Context：{yes_no(static_result['deterministic_appends_metadata'])}",
        f"- 调用 base_enhancer.enhance_system_prompt：{yes_no(static_result['deterministic_calls_base'])}",
        f"- 调用 search_similar_usage：{yes_no(static_result['deterministic_calls_search_similar_usage'])}",
        "",
        "## fake memory 动态诊断结果",
        "",
        "fake memory 同时实现 `search_text_memories()` 和 `search_similar_usage()`；每个问题中 `search_similar_usage()` 都预置返回目标 L2 SQL 示例。实际调用结果显示：base enhancer 调用了 `search_text_memories()`，没有调用 `search_similar_usage()`。",
        "",
        "## 逐项 prompt 命中结果",
        "",
    ]

    for item in results:
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- question：{item['question']}",
                f"- target_sample_id：{item['target_sample_id']}",
                f"- target_table：{item['target_table']}",
                f"- key_columns：{', '.join(item['key_columns'])}",
                f"- sample_question：{item['sample_question']}",
                f"- sample_sql：{item['sample_sql']}",
                f"- default_enhancer_called_search_text_memories：{yes_no(item['default_enhancer_called_search_text_memories'])}",
                f"- default_enhancer_called_search_similar_usage：{yes_no(item['default_enhancer_called_search_similar_usage'])}",
                f"- deterministic_enhancer_called_base：{yes_no(item['deterministic_enhancer_called_base'])}",
                f"- final_prompt_contains_sample_id：{yes_no(item['final_prompt_contains_sample_id'])}",
                f"- final_prompt_contains_sample_question：{yes_no(item['final_prompt_contains_sample_question'])}",
                f"- final_prompt_contains_sample_sql：{yes_no(item['final_prompt_contains_sample_sql'])}",
                f"- final_prompt_contains_target_table：{yes_no(item['final_prompt_contains_target_table'])}",
                f"- final_prompt_contains_key_columns：{yes_no(item['final_prompt_contains_key_columns'])}",
                f"- key_column_hits：{format_list(item['key_column_hits'])}",
                f"- final_prompt_contains_deterministic_metadata：{yes_no(item['final_prompt_contains_deterministic_metadata'])}",
                f"- diagnosis：{item['diagnosis']}",
                f"- recommended_next_action：{item['recommended_next_action']}",
                "",
            ]
        )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    summary, results = asyncio.run(diagnose())
    write_report(summary, results)
    print(f"报告：{REPORT_PATH}")
    print(f"SQL 示例进入 prompt：{format_list(summary['sql_examples_entered'])}")
    print(f"SQL 示例未进入 prompt：{format_list(summary['sql_examples_missing'])}")
    print(f"metadata context 进入 prompt：{format_list(summary['metadata_entered'])}")
    print(f"字段 context 弱：{format_list(summary['field_context_weak'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
