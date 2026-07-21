"""Level 3 P1 approved SQL 示例受控写入脚本。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = PROJECT_ROOT / "training"
DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p1_draft.json"
REVIEW_PATH = TRAINING_DIR / "sql_examples_level3_p1_review_result.json"
REPORT_PATH = TRAINING_DIR / "sql_examples_level3_p1_training_result.md"
VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
BACKUP_ROOT = PROJECT_ROOT.parents[1] / "_backup"

APPROVED_IDS = {
    "L3_P1_SQL_001", "L3_P1_SQL_002", "L3_P1_SQL_003", "L3_P1_SQL_006",
    "L3_P1_SQL_007", "L3_P1_SQL_008", "L3_P1_SQL_009", "L3_P1_SQL_011",
    "L3_P1_SQL_012", "L3_P1_SQL_013", "L3_P1_SQL_014", "L3_P1_SQL_015",
    "L3_P1_SQL_016", "L3_P1_SQL_017", "L3_P1_SQL_018", "L3_P1_SQL_019",
    "L3_P1_SQL_020", "L3_P1_SQL_021", "L3_P1_SQL_022", "L3_P1_SQL_023",
    "L3_P1_SQL_024",
}
FROZEN_IDS = {"L3_P1_SQL_004", "L3_P1_SQL_005", "L3_P1_SQL_010"}
EXPECTED_CHANGED = {
    "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin",
    "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin",
    "vanna_data/chroma.sqlite3",
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_config import create_memory
from backend.sql_guard import SQLGuard
from vanna.core.tool import ToolContext
from vanna.core.user import User


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip()


def load_json(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, list) and all(isinstance(item, dict) for item in value),
            f"{path.name} 必须是对象数组")
    return value


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, file_hash(path))
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def changed_paths(before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]]) -> set[str]:
    return {
        f"vanna_data/{relative}"
        for relative in set(before) | set(after)
        if before.get(relative) != after.get(relative)
    }


def query_result_files() -> set[str]:
    return {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "agent_data").rglob("query_results_*.csv")
        if path.is_file()
    }


def validate_single_table_select(sql: str) -> None:
    lowered = sql.lower()
    require(bool(re.match(r"^\s*select\b", sql, re.IGNORECASE)), "SQL 不是 SELECT")
    require(not re.search(r"\bselect\s+\*", sql, re.IGNORECASE), "SQL 包含 SELECT *")
    require(not re.search(r"\bjoin\b", sql, re.IGNORECASE), "SQL 包含 JOIN")
    require(not re.search(r"\b(with|union|intersect|except)\b", sql, re.IGNORECASE), "SQL 不是单表查询")
    require(len(re.findall(r"\bfrom\s+[a-z_][\w$]*", lowered)) == 1, "SQL 不是单表查询")
    limit = re.search(r"\blimit\s+(\d+)\b", sql, re.IGNORECASE)
    require(limit is not None and int(limit.group(1)) <= 100, "SQL 缺少 LIMIT 或 LIMIT 超过 100")
    require(not re.search(
        r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge|grant|revoke)\b",
        sql, re.IGNORECASE), "SQL 包含禁止操作")
    require("wm_waterquality_threshold" not in lowered, "SQL 使用冻结表")


def load_and_validate() -> list[dict[str, Any]]:
    draft = load_json(DRAFT_PATH)
    review = load_json(REVIEW_PATH)
    require(len(draft) == 24 and len(review) == 24, "draft/review_result 必须各有 24 条")
    draft_by_id = {str(item.get("id")): item for item in draft}
    review_by_id = {str(item.get("id")): item for item in review}
    require(len(draft_by_id) == 24 and len(review_by_id) == 24, "存在重复或缺失 id")
    require(set(draft_by_id) == set(review_by_id), "draft/review_result id 不一致")

    decisions = {sample_id: str(item.get("decision")) for sample_id, item in review_by_id.items()}
    actual_approved = {sample_id for sample_id, decision in decisions.items() if decision == "approved"}
    actual_frozen = {sample_id for sample_id, decision in decisions.items() if decision == "requires_manual_review"}
    require(actual_approved == APPROVED_IDS, "approved 集合不是精确 21 条")
    require(actual_frozen == FROZEN_IDS, "requires_manual_review 集合不是精确 3 条")
    require(not any(decision == "excluded" for decision in decisions.values()), "存在 excluded 样本")

    guard = SQLGuard()
    approved: list[dict[str, Any]] = []
    for sample_id in sorted(APPROVED_IDS):
        review_item = review_by_id[sample_id]
        draft_item = draft_by_id[sample_id]
        for field in ("question", "sql", "expected_tables", "expected_columns", "group", "priority"):
            require(review_item.get(field) == draft_item.get(field), f"{sample_id} 的 {field} 不一致")
        sql = str(review_item.get("sql") or "")
        validate_single_table_select(sql)
        guard_result = guard.validate(
            sql=sql,
            query=str(review_item.get("question") or ""),
            deterministic_candidate_tables=list(review_item.get("expected_tables") or []),
        )
        require(guard_result.passed and guard_result.severity == "ok",
                f"{sample_id} SQLGuard 未通过：{guard_result.reason}")
        approved.append({**review_item, "business_intent": draft_item.get("business_intent", "")})
    return approved


def make_context(memory: Any) -> ToolContext:
    return ToolContext(
        user=User(id="level3_p1_sql_trainer", username="level3_p1_sql_trainer"),
        conversation_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()),
        agent_memory=memory, metadata={"stage": "level3_p1_sql_examples_training"},
    )


async def write_approved(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    memory = create_memory()
    results: list[dict[str, Any]] = []
    for sample in samples:
        item = {"sample_id": sample["id"], "question": sample["question"], "status": "failed", "error": ""}
        try:
            await memory.save_tool_usage(
                question=sample["question"], tool_name="run_sql", args={"sql": sample["sql"]},
                context=make_context(memory), success=True,
                metadata={
                    "sample_id": sample["id"],
                    "training_level": "level3_p1_sql_examples",
                    "group": sample["group"],
                    "priority": "P1",
                    "train_decision": "approved",
                    "expected_tables": sample["expected_tables"],
                    "expected_columns": sample["expected_columns"],
                    "business_intent": sample["business_intent"],
                    "source_file": "training/sql_examples_level3_p1_review_result.json",
                },
            )
            item["status"] = "success"
        except Exception as exc:  # noqa: BLE001
            item["error"] = str(exc)
            results.append(item)
            break
        results.append(item)
    return results


def write_report(*, initial_status: str, backup_dir: Path,
                 before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]],
                 changed: set[str], results: list[dict[str, Any]], new_queries: set[str]) -> None:
    successes = sum(item["status"] == "success" for item in results)
    failures = sum(item["status"] == "failed" for item in results)
    lines = [
        "# Level 3 P1 approved SQL 示例受控写入结果", "", "## 汇总", "",
        f"- 当前工作目录：{PROJECT_ROOT}", f"- 当前 commit：{git_output('rev-parse', 'HEAD')}",
        "- 初始 git status --short：", "```text", initial_status, "```",
        "- 是否调用 vn.train()：否", "- 是否使用 memory.save_tool_usage()：是",
        "- 是否写入正式 ChromaDB：是", "- 批准写入数量：21",
        f"- 写入成功数量：{successes}", f"- 写入失败数量：{failures}",
        "- requires_manual_review 写入数量：0", "- excluded 写入数量：0",
        f"- 备份目录：{backup_dir}", f"- 正式 query_results 新增数量：{len(new_queries)}", "",
        "## 写入前后指纹", "", f"- 写入前文件数量：{len(before)}", f"- 写入后文件数量：{len(after)}",
        "", "### 写入前", "",
        *[f"- {path} | size={value[0]} | sha256={value[1]}" for path, value in before.items()],
        "", "### 写入后", "",
        *[f"- {path} | size={value[0]} | sha256={value[1]}" for path, value in after.items()],
        "", "## 变化文件列表", "", *[f"- {path}" for path in sorted(changed)],
        "", "## 逐样本写入状态", "", "| sample_id | question | status | error |", "|---|---|---|---|",
        *[f"| {item['sample_id']} | {item['question']} | {item['status']} | {item['error'] or '无'} |" for item in results],
        "", "## 结论", "",
        "21 条 approved 样本受控写入完成；3 条 requires_manual_review 样本未写入。"
        if successes == 21 and failures == 0 and not new_queries and changed == EXPECTED_CHANGED
        else "受控写入未达到验收条件。", "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Level 3 P1 SQL 示例受控写入")
    parser.add_argument("--initial-git-status", required=True)
    args = parser.parse_args()
    samples = load_and_validate()
    before = fingerprint(VANNA_DATA_DIR)
    query_before = query_result_files()
    backup_dir = BACKUP_ROOT / f"level3_p1_training_{datetime.now():%Y%m%d_%H%M%S}"
    require(not backup_dir.exists(), f"备份目录已存在：{backup_dir}")
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(VANNA_DATA_DIR, backup_dir)
    require(before == fingerprint(backup_dir), "备份指纹与正式 vanna_data 不一致")

    results = await write_approved(samples)
    after = fingerprint(VANNA_DATA_DIR)
    changed = changed_paths(before, after)
    new_queries = query_result_files() - query_before
    write_report(initial_status=args.initial_git_status, backup_dir=backup_dir, before=before,
                 after=after, changed=changed, results=results, new_queries=new_queries)
    successes = sum(item["status"] == "success" for item in results)
    failures = sum(item["status"] == "failed" for item in results)
    passed = successes == 21 and failures == 0 and len(results) == 21 and not new_queries and changed == EXPECTED_CHANGED
    print(json.dumps({
        "backup_dir": str(backup_dir), "success": successes, "failed": failures,
        "changed": sorted(changed), "new_query_results": sorted(new_queries), "passed": passed,
    }, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
