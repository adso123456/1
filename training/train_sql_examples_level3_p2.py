"""Level 3 P2 approved SQL 示例受控写入脚本。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = PROJECT_ROOT / "training"
DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p2_draft.json"
REVIEW_PATH = TRAINING_DIR / "sql_examples_level3_p2_review_result.json"
REPORT_PATH = TRAINING_DIR / "sql_examples_level3_p2_training_result.md"
VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"

APPROVED_IDS = {
    "L3_P2_SQL_001", "L3_P2_SQL_002", "L3_P2_SQL_003",
    "L3_P2_SQL_004", "L3_P2_SQL_005", "L3_P2_SQL_006",
    "L3_P2_SQL_007", "L3_P2_SQL_008", "L3_P2_SQL_011",
}
EXCLUDED_IDS = {"L3_P2_SQL_009", "L3_P2_SQL_010"}
EXPECTED_CHANGED = {
    "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin",
    "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin",
    "vanna_data/chroma.sqlite3",
}
FORBIDDEN_OPERATIONS = re.compile(
    r"\b(?:insert|update|delete|drop|alter|create|truncate|comment)\b", re.I
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.memory import create_memory
from backend.sql_guard import SQLGuard
from vanna.core.tool import ToolContext
from vanna.core.user import User


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    ).stdout.strip()


def fingerprint(base: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(base).as_posix(): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def changed_paths(
    before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]]
) -> set[str]:
    paths = set(before) | set(after)
    return {f"vanna_data/{path}" for path in paths if before.get(path) != after.get(path)}


def query_result_files() -> set[str]:
    return {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "agent_data").rglob("query_results_*.csv")
    }


def load_and_validate() -> list[dict[str, Any]]:
    review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    require(len(review) == 11 and len(draft) == 11, "P2 draft/review 必须各为 11 条")
    review_by_id = {item["id"]: item for item in review}
    draft_by_id = {item["id"]: item for item in draft}
    approved_ids = {item["id"] for item in review if item.get("decision") == "approved"}
    excluded_ids = {item["id"] for item in review if item.get("decision") == "excluded"}
    manual_ids = {
        item["id"] for item in review
        if item.get("decision") == "requires_manual_review"
    }
    require(approved_ids == APPROVED_IDS, f"approved 集合不一致：{sorted(approved_ids)}")
    require(excluded_ids == EXCLUDED_IDS, f"excluded 集合不一致：{sorted(excluded_ids)}")
    require(not manual_ids, f"存在 requires_manual_review：{sorted(manual_ids)}")

    guard = SQLGuard()
    approved: list[dict[str, Any]] = []
    for sample_id in sorted(APPROVED_IDS):
        review_item = review_by_id[sample_id]
        draft_item = draft_by_id[sample_id]
        for field in (
            "question", "sql", "expected_tables", "expected_columns",
            "join_keys", "group", "priority",
        ):
            require(
                review_item.get(field) == draft_item.get(field),
                f"{sample_id} 的 {field} 与 draft 不一致",
            )
        sql = str(review_item.get("sql") or "")
        require(bool(re.match(r"^\s*select\b", sql, re.I)), f"{sample_id} 不是 SELECT")
        require(not FORBIDDEN_OPERATIONS.search(sql), f"{sample_id} 包含 DDL/DML")
        join_keys = list(review_item.get("join_keys") or [])
        require(join_keys, f"{sample_id} 缺少已确认 JOIN 键")
        require(
            len(re.findall(r"\bjoin\b", sql, re.I)) >= len(join_keys),
            f"{sample_id} JOIN 边数量不足",
        )
        guard_result = guard.validate(
            sql=sql,
            query=str(review_item.get("question") or ""),
            deterministic_candidate_tables=list(review_item.get("expected_tables") or []),
        )
        require(
            guard_result.passed and guard_result.severity == "ok",
            f"{sample_id} SQLGuard 未通过：{guard_result.reason}",
        )
        approved.append(
            {**review_item, "business_intent": draft_item.get("business_intent", "")}
        )
    return approved


def make_context(memory: Any) -> ToolContext:
    return ToolContext(
        user=User(id="level3_p2_sql_trainer", username="level3_p2_sql_trainer"),
        conversation_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        agent_memory=memory,
        metadata={"stage": "level3_p2_sql_examples_training"},
    )


async def write_approved(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    memory = create_memory()
    results: list[dict[str, Any]] = []
    for sample in samples:
        result = {
            "sample_id": sample["id"], "question": sample["question"],
            "status": "failed", "error": "",
        }
        try:
            await memory.save_tool_usage(
                question=sample["question"],
                tool_name="run_sql",
                args={"sql": sample["sql"]},
                context=make_context(memory),
                success=True,
                metadata={
                    "sample_id": sample["id"],
                    "training_level": "level3_p2_sql_examples",
                    "group": "P2",
                    "priority": "P2",
                    "train_decision": "approved",
                    "expected_tables": sample["expected_tables"],
                    "expected_columns": sample["expected_columns"],
                    "join_keys": sample["join_keys"],
                    "business_intent": sample["business_intent"],
                    "source_file": "training/sql_examples_level3_p2_review_result.json",
                },
            )
            result["status"] = "success"
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
            results.append(result)
            break
        results.append(result)
    return results


def write_report(
    *, initial_status: str, backup_dir: Path,
    before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]],
    changed: set[str], results: list[dict[str, Any]], new_queries: set[str],
) -> None:
    successes = sum(item["status"] == "success" for item in results)
    failures = sum(item["status"] == "failed" for item in results)
    lines = [
        "# Level 3 P2 approved SQL 示例受控写入结果", "",
        "## 汇总", "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        f"- 当前 commit：{git_output('rev-parse', 'HEAD')}",
        "- 初始 git status --short：", "```text", initial_status, "```",
        "- 是否调用 vn.train()：否",
        "- 是否调用 memory.save_tool_usage()：是",
        "- 计划写入：9",
        f"- 成功：{successes}",
        f"- 失败：{failures}",
        "- excluded 写入：0",
        f"- 备份目录：{backup_dir}",
        f"- 正式 agent_data/query_results 新增：{len(new_queries)}", "",
        "## 写入前后指纹", "",
        f"- 写入前文件数：{len(before)}",
        f"- 写入后文件数：{len(after)}", "",
        "### 写入前", "",
        *[f"- {path} | size={value[0]} | sha256={value[1]}" for path, value in before.items()],
        "", "### 写入后", "",
        *[f"- {path} | size={value[0]} | sha256={value[1]}" for path, value in after.items()],
        "", "## 正式变化文件", "",
        *[f"- {path}" for path in sorted(changed)],
        "", "## 逐样本写入状态", "",
        "| sample_id | question | status | error |", "|---|---|---|---|",
        *[
            f"| {item['sample_id']} | {item['question']} | {item['status']} | {item['error'] or '无'} |"
            for item in results
        ],
        "", "## 结论", "",
        (
            "9 条 approved P2 样本受控写入完成；2 条 excluded 样本未写入。"
            if successes == 9 and failures == 0 and not new_queries
            and changed == EXPECTED_CHANGED
            else "P2 受控写入未达到验收条件。"
        ), "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Level 3 P2 SQL 示例受控写入")
    parser.add_argument("--initial-git-status", required=True)
    parser.add_argument("--backup-dir", required=True)
    args = parser.parse_args()

    samples = load_and_validate()
    backup_dir = Path(args.backup_dir).resolve()
    require(backup_dir.is_dir(), f"备份目录不存在：{backup_dir}")
    before = fingerprint(VANNA_DATA_DIR)
    require(before == fingerprint(backup_dir), "备份指纹与正式 vanna_data 不一致")
    query_before = query_result_files()

    results = await write_approved(samples)
    after = fingerprint(VANNA_DATA_DIR)
    changed = changed_paths(before, after)
    new_queries = query_result_files() - query_before
    write_report(
        initial_status=args.initial_git_status, backup_dir=backup_dir,
        before=before, after=after, changed=changed, results=results,
        new_queries=new_queries,
    )
    successes = sum(item["status"] == "success" for item in results)
    failures = sum(item["status"] == "failed" for item in results)
    passed = (
        len(results) == 9 and successes == 9 and failures == 0
        and not new_queries and changed == EXPECTED_CHANGED
    )
    print(json.dumps({
        "backup_dir": str(backup_dir), "success": successes,
        "failed": failures, "excluded_written": 0,
        "changed": sorted(changed),
        "new_query_results": sorted(new_queries), "passed": passed,
    }, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
