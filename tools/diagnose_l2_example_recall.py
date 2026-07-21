from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMAL_VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
DRAFT_PATH = PROJECT_ROOT / "training" / "sql_examples_level2_draft.json"
REPORT_PATH = PROJECT_ROOT / "tools" / "l2_example_recall_diagnosis.md"
BASE_COMMIT = "e1fa7e7f59b5aeae5d6c82e9dd4dc2e06e1ae161"
ALLOWED_STATUS_PATHS = {
    "tools/diagnose_l2_example_recall.py",
    "tools/l2_example_recall_diagnosis.md",
}

TARGETS = [
    {
        "id": "Q1",
        "question": "查询某站点水质日趋势中的 pH 和溶解氧变化",
        "sample_id": "L2_SQL_003",
        "target_table": "wm_waterquality_day_records",
        "key_columns": ["m2_value", "m3_value", "monitor_time", "station_id"],
        "nearby_ids": ["L2_SQL_001", "L2_SQL_002", "L2_SQL_003"],
    },
    {
        "id": "Q2",
        "question": "某站点水质小时变化趋势",
        "sample_id": "L2_SQL_004",
        "target_table": "wm_waterquality_hour_records",
        "key_columns": ["m1_value", "m2_value", "m3_value", "monitor_time", "station_id"],
        "nearby_ids": ["L2_SQL_004", "L2_SQL_005", "L2_SQL_006"],
    },
    {
        "id": "Q3",
        "question": "某站点水质月变化趋势",
        "sample_id": "L2_SQL_007",
        "target_table": "wm_waterquality_month_records",
        "key_columns": ["m2_value", "m3_value", "monitor_year", "monitor_month", "station_id"],
        "nearby_ids": ["L2_SQL_007", "L2_SQL_008"],
    },
    {
        "id": "Q6",
        "question": "查询站点名称和所属区域",
        "sample_id": "L2_SQL_015",
        "target_table": "wm_station_info_v2",
        "key_columns": ["station_name", "station_code", "region_code", "region_name"],
        "nearby_ids": ["L2_SQL_015", "L2_SQL_016"],
    },
    {
        "id": "Q7",
        "question": "查询区域编码和区域名称",
        "sample_id": "L2_SQL_017",
        "target_table": "gis_region",
        "key_columns": ["region_code", "region_name"],
        "nearby_ids": ["L2_SQL_017"],
    },
    {
        "id": "Q8",
        "question": "查询取水口名称和水源类型",
        "sample_id": "L2_SQL_018",
        "target_table": "wm_water_intake",
        "key_columns": ["name", "water_type"],
        "nearby_ids": ["L2_SQL_018"],
    },
]


@dataclass
class FingerprintEntry:
    size: int
    sha256: str


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root: Path) -> dict[str, FingerprintEntry]:
    result: dict[str, FingerprintEntry] = {}
    if not root.exists():
        return result
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        result[rel] = FingerprintEntry(size=path.stat().st_size, sha256=sha256_file(path))
    return result


def changed(before: dict[str, FingerprintEntry], after: dict[str, FingerprintEntry]) -> bool:
    if set(before) != set(after):
        return True
    return any(before[key] != after[key] for key in before)


def load_samples() -> dict[str, dict[str, Any]]:
    samples = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    return {str(sample["id"]): sample for sample in samples}


def setup_isolation() -> dict[str, Path]:
    root = Path(tempfile.mkdtemp(prefix="vanna_l2_recall_probe_"))
    temp_vanna = root / "vanna_data"
    shutil.copytree(FORMAL_VANNA_DATA_DIR, temp_vanna)
    return {"root": root, "vanna_data": temp_vanna}


def make_context(memory: Any) -> Any:
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    return ToolContext(
        user=User(id="l2_recall_probe", username="l2_recall_probe"),
        conversation_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        agent_memory=memory,
        metadata={"stage": "l2_example_recall_diagnosis"},
    )


def recall_status(target_in_top_k: bool, rank: int | None, target_table_found: bool) -> str:
    if not target_in_top_k:
        return "fail"
    if rank is not None and rank <= 5 and target_table_found:
        return "pass"
    return "weak"


def metadata_status(target_table: str, tables: list[str], key_hits: list[str]) -> str:
    if target_table not in tables:
        return "fail"
    if key_hits:
        return "pass"
    return "weak"


def format_bool(value: bool) -> str:
    return "是" if value else "否"


def compact_sql(sql: str) -> str:
    return " ".join(sql.split())


async def diagnose() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    remote = run_command(["git", "remote", "-v"])
    raw_initial_status = run_command(["git", "status", "--short"])
    initial_status, unexpected_status = effective_status(raw_initial_status)
    commit = run_command(["git", "rev-parse", "HEAD"])
    if unexpected_status:
        raise SystemExit("git status --short 存在非本阶段文件，停止：" + "；".join(unexpected_status))
    if commit != BASE_COMMIT and run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit]) != "":
        raise SystemExit(f"当前 commit 不满足要求：{commit}")

    formal_before = fingerprint(FORMAL_VANNA_DATA_DIR)
    isolation = setup_isolation()

    os.environ["VANNA_DATA_DIR"] = str(isolation["vanna_data"])
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from backend.memory import create_memory
    from backend.metadata_context_enhancer import DeterministicMetadataContextEnhancer
    from backend.metadata_retriever import DeterministicMetadataRetriever
    from vanna.core.enhancer.default import DefaultLlmContextEnhancer
    from vanna.core.user import User

    memory = create_memory()
    context = make_context(memory)
    samples = load_samples()
    retriever = DeterministicMetadataRetriever()
    metadata_enhancer = DeterministicMetadataContextEnhancer(
        base_enhancer=None,
        metadata_retriever=retriever,
        top_n=10,
    )
    full_enhancer = DeterministicMetadataContextEnhancer(
        base_enhancer=DefaultLlmContextEnhancer(memory),
        metadata_retriever=retriever,
        top_n=10,
    )

    results: list[dict[str, Any]] = []
    for target in TARGETS:
        sample = samples.get(target["sample_id"])
        approved_sample_exists = bool(sample and sample.get("train_decision") == "approved")
        sample_question = str(sample.get("question", "")) if sample else ""
        sample_sql = str(sample.get("sql", "")) if sample else ""

        recall_unavailable = ""
        recall_results = []
        try:
            search_results = await memory.search_similar_usage(
                question=target["question"],
                context=context,
                limit=10,
                similarity_threshold=0.0,
                tool_name_filter="run_sql",
            )
            for item in search_results:
                args = item.memory.args or {}
                metadata = item.memory.metadata or {}
                recall_text = "\n".join(
                    [
                        str(item.memory.question),
                        json.dumps(args, ensure_ascii=False),
                        json.dumps(metadata, ensure_ascii=False),
                    ]
                )
                recall_results.append(
                    {
                        "rank": item.rank,
                        "similarity": round(item.similarity_score, 6),
                        "question": item.memory.question,
                        "sample_id": metadata.get("sample_id", ""),
                        "sql": args.get("sql", ""),
                        "text": recall_text,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            recall_unavailable = str(exc)

        target_rank = None
        target_sql_text_found = False
        target_table_found = False
        for item in recall_results:
            text = item["text"]
            if item["sample_id"] == target["sample_id"] or sample_question in text:
                target_rank = item["rank"]
                recalled_sql = str(item.get("sql") or "")
                target_sql_text_found = bool(
                    sample_sql
                    and (
                        compact_sql(sample_sql) == compact_sql(recalled_sql)
                        or compact_sql(sample_sql) in compact_sql(text).replace("\\n", " ")
                    )
                )
                target_table_found = target["target_table"] in text
                break
        target_in_top_k = target_rank is not None

        candidates = retriever.retrieve(target["question"], top_n=10)
        candidate_tables = [item["table_name"] for item in candidates]
        matched_columns = [
            f"{item['table_name']}.{column['column_name']}"
            for item in candidates
            for column in item.get("matched_columns", [])
        ]
        metadata_prompt = await metadata_enhancer.enhance_system_prompt(
            system_prompt="SYSTEM",
            user_message=target["question"],
            user=User(id="l2_recall_probe", username="l2_recall_probe"),
        )
        full_prompt = await full_enhancer.enhance_system_prompt(
            system_prompt="SYSTEM",
            user_message=target["question"],
            user=User(id="l2_recall_probe", username="l2_recall_probe"),
        )

        key_hits = [column for column in target["key_columns"] if column in metadata_prompt]
        generic_hits = [
            column
            for column in matched_columns
            if any(token in column.rsplit(".", 1)[-1] for token in ["name", "code", "type"])
            and not column.startswith(target["target_table"] + ".")
        ]

        result_recall_status = "unavailable" if recall_unavailable else recall_status(
            target_in_top_k, target_rank, target_table_found
        )
        result_metadata_status = metadata_status(target["target_table"], candidate_tables, key_hits)

        if recall_unavailable:
            diagnosis = "Chroma tool usage 召回不可用，无法判断 L2 示例是否进入召回结果。"
            recommended = "先修复召回诊断接口可用性，再判断训练样本效果。"
        elif result_recall_status == "fail":
            diagnosis = "目标 approved SQL 示例未出现在 tool usage top-10 召回结果中。"
            recommended = "下一阶段检查 L2 写入集合、embedding 一致性和 Default enhancer 是否查询 tool usage。"
        elif result_metadata_status == "weak":
            diagnosis = "目标 SQL 示例可召回，但 deterministic metadata context 的关键字段提示不足。"
            recommended = "下一阶段补强 metadata context 字段映射，不新增训练。"
        elif result_metadata_status == "fail":
            diagnosis = "目标 SQL 示例可召回，但 deterministic candidate tables 未包含目标表。"
            recommended = "下一阶段修 P0 候选排序/意图规则。"
        else:
            diagnosis = "目标 SQL 示例和 metadata context 均可观察到，但仍需确认是否进入实际 LLM prompt。"
            recommended = "下一阶段增加 LLM 前 prompt 截取或 memory tool usage 注入可观测性。"

        results.append(
            {
                "id": target["id"],
                "question": target["question"],
                "expected_target_sample_id": target["sample_id"],
                "expected_target_table": target["target_table"],
                "expected_key_columns": target["key_columns"],
                "nearby_ids": target["nearby_ids"],
                "approved_sample_exists": approved_sample_exists,
                "approved_sample_question": sample_question,
                "approved_sample_sql": sample_sql,
                "recall_top_k": [
                    {
                        "rank": item["rank"],
                        "similarity": item["similarity"],
                        "sample_id": item["sample_id"],
                        "question": item["question"],
                        "sql": item["sql"],
                    }
                    for item in recall_results
                ],
                "recall_unavailable": recall_unavailable,
                "target_sample_in_top_k": target_in_top_k,
                "target_sample_rank": target_rank,
                "target_sql_text_found": target_sql_text_found,
                "target_table_found": target_table_found,
                "deterministic_candidate_tables": candidate_tables,
                "deterministic_matched_columns": matched_columns[:30],
                "metadata_context_contains_target_table": target["target_table"] in metadata_prompt,
                "metadata_context_contains_key_columns": bool(key_hits),
                "metadata_context_key_column_hits": key_hits,
                "llm_prompt_contains_target_sample_id": target["sample_id"] in full_prompt,
                "llm_prompt_contains_target_sql": bool(sample_sql and compact_sql(sample_sql) in compact_sql(full_prompt)),
                "llm_prompt_contains_target_table": target["target_table"] in full_prompt,
                "generic_interference_columns": generic_hits[:20],
                "recall_status": result_recall_status,
                "metadata_context_status": result_metadata_status,
                "diagnosis": diagnosis,
                "recommended_next_action": recommended,
            }
        )

    formal_after = fingerprint(FORMAL_VANNA_DATA_DIR)
    summary = {
        "remote": remote,
        "initial_status": initial_status or "clean",
        "commit": commit,
        "temp_root": str(isolation["root"]),
        "formal_vanna_changed": changed(formal_before, formal_after),
        "total": len(results),
        "recall_success": [item["id"] for item in results if item["recall_status"] == "pass"],
        "recall_weak": [item["id"] for item in results if item["recall_status"] == "weak"],
        "recall_failed": [item["id"] for item in results if item["recall_status"] in {"fail", "unavailable"}],
        "metadata_success": [item["id"] for item in results if item["metadata_context_status"] == "pass"],
        "metadata_weak": [item["id"] for item in results if item["metadata_context_status"] == "weak"],
        "metadata_failed": [item["id"] for item in results if item["metadata_context_status"] == "fail"],
        "waterquality_mapping_missing": any(
            item["id"] in {"Q1", "Q2", "Q3"} and not item["metadata_context_contains_key_columns"]
            for item in results
        ),
        "generic_interference": [
            item["id"] for item in results if item["generic_interference_columns"]
        ],
    }
    return summary, results


def format_list(items: list[str]) -> str:
    return "，".join(items) if items else "无"


def write_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    if summary["recall_failed"]:
        conclusion = "L2 approved SQL 示例存在召回失败，且默认 LLM prompt 未证明包含 tool usage SQL 示例。"
    elif summary["metadata_weak"] or summary["metadata_failed"]:
        conclusion = "L2 示例可召回，但 deterministic metadata context 仍有字段或候选弱项。"
    else:
        conclusion = "L2 示例召回和 metadata context 均通过静态可观测性检查。"

    next_step = (
        "下一阶段先确认 DefaultLlmContextEnhancer 是否会把 tool usage SQL 示例注入 LLM；"
        "若不会，应设计只读可观测 hook 或显式上下文注入方案，然后再做 P0/metadata context 最小修复。"
    )

    lines = [
        "# L2 SQL 示例召回可观测性诊断",
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
        f"- 临时目录：{summary['temp_root']}",
        "- 是否启动真实主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行真实 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        "- 是否写入正式 ChromaDB：否",
        f"- 是否修改正式 vanna_data：{format_bool(summary['formal_vanna_changed'])}",
        "- 是否进入第 3/4 级：否",
        f"- 诊断问题总数：{summary['total']}",
        f"- 召回成功列表：{format_list(summary['recall_success'])}",
        f"- 召回弱列表：{format_list(summary['recall_weak'])}",
        f"- 召回失败列表：{format_list(summary['recall_failed'])}",
        f"- metadata context 通过列表：{format_list(summary['metadata_success'])}",
        f"- metadata context 弱列表：{format_list(summary['metadata_weak'])}",
        f"- metadata context 失败列表：{format_list(summary['metadata_failed'])}",
        f"- 水质指标字段映射是否缺失：{format_bool(summary['waterquality_mapping_missing'])}",
        f"- generic name/code/type 干扰列表：{format_list(summary['generic_interference'])}",
        f"- 当前结论：{conclusion}",
        f"- 下一阶段建议：{next_step}",
        "",
        "## 关键观察",
        "",
        "- L2 训练写入使用 `save_tool_usage(..., tool_name='run_sql')`，召回应通过 `search_similar_usage(..., tool_name_filter='run_sql')` 观察。",
        "- Vanna 默认 `DefaultLlmContextEnhancer` 只调用 `search_text_memories`，不直接调用 `search_similar_usage`。因此 tool usage SQL 示例即使可召回，也未必进入 system prompt。",
        "- 本脚本没有启动服务、没有调用 LLM、没有连接数据库、没有执行 SQL。",
        "",
        "## 逐项诊断",
        "",
    ]

    for item in results:
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- question：{item['question']}",
                f"- expected_target_sample_id：{item['expected_target_sample_id']}",
                f"- expected_target_table：{item['expected_target_table']}",
                f"- expected_key_columns：{', '.join(item['expected_key_columns'])}",
                f"- approved_sample_exists：{format_bool(item['approved_sample_exists'])}",
                f"- approved_sample_question：{item['approved_sample_question']}",
                f"- approved_sample_sql：{item['approved_sample_sql']}",
                f"- recall_status：{item['recall_status']}",
                f"- recall_unavailable：{item['recall_unavailable'] or '无'}",
                f"- target_sample_in_top_k：{format_bool(item['target_sample_in_top_k'])}",
                f"- target_sample_rank：{item['target_sample_rank'] if item['target_sample_rank'] is not None else '无'}",
                f"- target_sql_text_found：{format_bool(item['target_sql_text_found'])}",
                f"- target_table_found：{format_bool(item['target_table_found'])}",
                f"- deterministic_candidate_tables：{', '.join(item['deterministic_candidate_tables'])}",
                f"- deterministic_matched_columns：{', '.join(item['deterministic_matched_columns']) or '无'}",
                f"- metadata_context_status：{item['metadata_context_status']}",
                f"- metadata_context_contains_target_table：{format_bool(item['metadata_context_contains_target_table'])}",
                f"- metadata_context_contains_key_columns：{format_bool(item['metadata_context_contains_key_columns'])}",
                f"- metadata_context_key_column_hits：{format_list(item['metadata_context_key_column_hits'])}",
                f"- llm_prompt_contains_target_sample_id：{format_bool(item['llm_prompt_contains_target_sample_id'])}",
                f"- llm_prompt_contains_target_sql：{format_bool(item['llm_prompt_contains_target_sql'])}",
                f"- llm_prompt_contains_target_table：{format_bool(item['llm_prompt_contains_target_table'])}",
                f"- generic_interference_columns：{', '.join(item['generic_interference_columns']) or '无'}",
                f"- diagnosis：{item['diagnosis']}",
                f"- recommended_next_action：{item['recommended_next_action']}",
                "",
                "recall_top_k：",
                "",
                "| rank | similarity | sample_id | question | sql |",
                "|---:|---:|---|---|---|",
            ]
        )
        for row in item["recall_top_k"]:
            sql = compact_sql(str(row["sql"])).replace("|", "\\|")
            question = str(row["question"]).replace("|", "\\|")
            lines.append(
                f"| {row['rank']} | {row['similarity']} | {row['sample_id'] or '无'} | {question} | {sql[:300]} |"
            )
        if not item["recall_top_k"]:
            lines.append("| - | - | - | - | 无召回结果 |")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    summary, results = asyncio.run(diagnose())
    write_report(summary, results)
    print(f"报告：{REPORT_PATH}")
    print(f"临时目录：{summary['temp_root']}")
    print(f"召回成功：{format_list(summary['recall_success'])}")
    print(f"召回弱：{format_list(summary['recall_weak'])}")
    print(f"召回失败：{format_list(summary['recall_failed'])}")
    print(f"metadata weak：{format_list(summary['metadata_weak'])}")
    print(f"formal_vanna_changed={summary['formal_vanna_changed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
