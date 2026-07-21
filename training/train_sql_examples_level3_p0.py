"""第 3 级 P0 approved SQL 示例受控写入脚本。"""

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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = PROJECT_ROOT / "training"
DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p0_draft.json"
REVIEW_PATH = TRAINING_DIR / "sql_examples_level3_p0_review_result.json"
REPORT_PATH = TRAINING_DIR / "sql_examples_level3_p0_training_result.md"
VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
BACKUP_ROOT = PROJECT_ROOT.parents[1] / "_backup"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_config import create_memory
from backend.sql_guard import SQLGuard
from vanna.core.tool import ToolContext
from vanna.core.user import User


@dataclass(frozen=True)
class FingerprintEntry:
    path: str
    size: int
    sha256: str


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip()


def load_json_list(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"{path.name} 必须是对象数组")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_fingerprint(root: Path, *, label: str) -> dict[str, FingerprintEntry]:
    entries: dict[str, FingerprintEntry] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        entries[relative] = FingerprintEntry(
            path=f"{label}/{relative}",
            size=path.stat().st_size,
            sha256=file_sha256(path),
        )
    return entries


def fingerprint_summary(entries: dict[str, FingerprintEntry]) -> tuple[int, int]:
    return len(entries), sum(entry.size for entry in entries.values())


def changed_files(
    before: dict[str, FingerprintEntry], after: dict[str, FingerprintEntry]
) -> list[str]:
    changed: list[str] = []
    for relative in sorted(set(before) | set(after)):
        if relative not in before or relative not in after:
            changed.append((after.get(relative) or before[relative]).path)
            continue
        if before[relative].size != after[relative].size or before[relative].sha256 != after[relative].sha256:
            changed.append(after[relative].path)
    return changed


def format_fingerprint(entries: dict[str, FingerprintEntry]) -> list[str]:
    return [
        f"- {entry.path} | size={entry.size} | sha256={entry.sha256}"
        for entry in entries.values()
    ] or ["- 无"]


def list_query_result_files() -> set[str]:
    agent_data = PROJECT_ROOT / "agent_data"
    return {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in agent_data.rglob("query_results_*.csv")
        if path.is_file()
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def is_safe_select(sql: str) -> bool:
    return (
        bool(re.match(r"^\s*select\b", sql, re.IGNORECASE))
        and bool(re.search(r"\blimit\b", sql, re.IGNORECASE))
        and not bool(re.search(r"\bselect\s+\*", sql, re.IGNORECASE))
        and "wm_waterquality_threshold" not in sql.lower()
        and not bool(
            re.search(
                r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge|grant|revoke)\b",
                sql,
                re.IGNORECASE,
            )
        )
        and not bool(
            re.search(
                r"\b(information_schema|pg_catalog|sqlite_master|sqlite_schema)\b",
                sql,
                re.IGNORECASE,
            )
        )
    )


def load_and_validate_samples() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    draft = load_json_list(DRAFT_PATH)
    review = load_json_list(REVIEW_PATH)
    require(len(draft) == 18, f"draft 样本总数必须为 18，实际为 {len(draft)}")
    require(len(review) == 18, f"review_result 样本总数必须为 18，实际为 {len(review)}")

    draft_by_id = {str(sample.get("id")): sample for sample in draft}
    review_by_id = {str(sample.get("id")): sample for sample in review}
    require(len(draft_by_id) == 18, "draft 存在重复或缺失 id")
    require(len(review_by_id) == 18, "review_result 存在重复或缺失 id")
    require(set(draft_by_id) == set(review_by_id), "draft/review id 不完全一致")

    for sample_id, sample in draft_by_id.items():
        require(
            sample.get("question") == review_by_id[sample_id].get("question"),
            f"{sample_id} 的 draft/review question 不一致",
        )
        require(sample.get("train_decision") == "draft", f"{sample_id} train_decision 不是 draft")

    decisions = [sample.get("decision") for sample in review]
    require(decisions.count("approved") == 18, f"approved 数量必须为 18，实际为 {decisions.count('approved')}")
    require(decisions.count("requires_manual_review") == 0, "存在 requires_manual_review 样本")
    require(decisions.count("excluded") == 0, "存在 excluded 样本")

    guard = SQLGuard()
    for sample_id in sorted(draft_by_id):
        sample = draft_by_id[sample_id]
        sql = str(sample.get("sql") or "")
        require(is_safe_select(sql), f"{sample_id} 未满足 SELECT/LIMIT/安全 SQL 要求")
        guard_result = guard.validate(
            sql=sql,
            query=str(sample.get("question") or ""),
            deterministic_candidate_tables=list(sample.get("expected_tables") or []),
        )
        require(
            guard_result.passed and guard_result.severity == "ok",
            f"{sample_id} SQL Guard 未通过：{guard_result.reason}",
        )

    return [draft_by_id[sample_id] for sample_id in sorted(draft_by_id)], review_by_id


def make_context(memory: Any) -> ToolContext:
    return ToolContext(
        user=User(id="level3_p0_sql_trainer", username="level3_p0_sql_trainer"),
        conversation_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        agent_memory=memory,
        metadata={"stage": "level3_p0_sql_examples_training"},
    )


async def write_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    memory = create_memory()
    guard = SQLGuard()
    results: list[dict[str, Any]] = []

    for sample in samples:
        sample_id = str(sample["id"])
        question = str(sample["question"])
        sql = str(sample["sql"])
        guard_result = guard.validate(
            sql=sql,
            query=question,
            deterministic_candidate_tables=list(sample.get("expected_tables") or []),
        )
        result = {
            "sample_id": sample_id,
            "question": question,
            "tool_name": "run_sql",
            "training_level": "level3_p0_sql_examples",
            "expected_tables": list(sample.get("expected_tables") or []),
            "write_status": "failed",
            "error": "",
        }

        if not guard_result.passed or guard_result.severity != "ok":
            result["error"] = f"SQL Guard 未通过：{guard_result.reason}"
            results.append(result)
            break

        try:
            await memory.save_tool_usage(
                question=question,
                tool_name="run_sql",
                args={"sql": sql},
                context=make_context(memory),
                success=True,
                metadata={
                    "sample_id": sample_id,
                    "training_level": "level3_p0_sql_examples",
                    "group": sample["group"],
                    "priority": sample["priority"],
                    "train_decision": "approved",
                    "expected_tables": list(sample.get("expected_tables") or []),
                    "expected_columns": list(sample.get("expected_columns") or []),
                    "business_intent": sample["business_intent"],
                    "source_file": "training/sql_examples_level3_p0_draft.json",
                },
            )
            result["write_status"] = "success"
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
            results.append(result)
            break
        results.append(result)
    return results


def write_report(
    *,
    initial_status: str,
    base_commit: str,
    backup_dir: Path,
    before: dict[str, FingerprintEntry],
    after: dict[str, FingerprintEntry],
    changed: list[str],
    results: list[dict[str, Any]],
    new_query_results: set[str],
    vanna_change_expected: bool,
) -> None:
    before_count, before_size = fingerprint_summary(before)
    after_count, after_size = fingerprint_summary(after)
    success_count = sum(item["write_status"] == "success" for item in results)
    failed_count = sum(item["write_status"] == "failed" for item in results)
    skipped_count = 18 - len(results)
    all_success = success_count == 18 and failed_count == 0 and skipped_count == 0
    remote = git_output("remote", "-v")

    lines = [
        "# 第 3 级 P0 approved SQL 示例受控写入结果",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        remote,
        "```",
        f"- 当前 commit：{base_commit}",
        "- 初始 git status --short：",
        "```text",
        initial_status or "clean",
        "```",
        "- 修改/新增文件路径：training/train_sql_examples_level3_p0.py, training/sql_examples_level3_p0_training_result.md",
        "- 是否启动真实主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行真实 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否调用 vn.train()：否",
        "- 是否使用 memory.save_tool_usage：是",
        "- 是否写入正式 ChromaDB：是",
        "- 是否修改正式 vanna_data：是",
        f"- 备份目录：{backup_dir}",
        "- 是否只写 approved：是",
        "- requires_manual_review 写入数量：0",
        "- excluded 写入数量：0",
        "",
        "## 写入前指纹摘要",
        "",
        f"- 文件数量：{before_count}",
        f"- 总大小：{before_size}",
        *format_fingerprint(before),
        "",
        "## 写入后指纹摘要",
        "",
        f"- 文件数量：{after_count}",
        f"- 总大小：{after_size}",
        *format_fingerprint(after),
        "",
        "## 变化文件列表",
        "",
        *([f"- {path}" for path in changed] if changed else ["- 无"]),
        "",
        "## 写入统计",
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
        "| 样本总数 | 18 |",
        f"| 写入成功数量 | {success_count} |",
        f"| 写入失败数量 | {failed_count} |",
        f"| skipped 数量 | {skipped_count} |",
        "| requires_manual_review 写入数量 | 0 |",
        "| excluded 写入数量 | 0 |",
        "",
        "## 逐样本写入结果",
        "",
        "| sample_id | question | tool_name | training_level | expected_tables | write_status | error |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        "| {sample_id} | {question} | {tool_name} | {training_level} | {expected_tables} | {write_status} | {error} |".format(
            sample_id=item["sample_id"],
            question=item["question"],
            tool_name=item["tool_name"],
            training_level=item["training_level"],
            expected_tables=", ".join(item["expected_tables"]),
            write_status=item["write_status"],
            error=item["error"] or "无",
        )
        for item in results
    )
    lines.extend(
        [
            "",
            "## 写入后约束确认",
            "",
            f"- 正式 agent_data/query_results_*.csv 是否新增：{'是：' + ', '.join(sorted(new_query_results)) if new_query_results else '否'}",
            f"- vanna_data 变化是否符合预期：{'是' if vanna_change_expected else '否'}",
            "- 是否调用 vn.train()：否",
            "- 是否启动主服务：否",
            "- 是否连接数据库：否",
            "- 是否执行 SQL：否",
            "- 是否调用 DeepSeek：否",
            "",
            "## 当前结论",
            "",
            "通过：18 条 approved 样本均已写入。" if all_success and vanna_change_expected and not new_query_results else "不通过：存在写入失败、跳过或非预期文件变化。",
            "",
            "## 下一阶段建议",
            "",
            "在隔离环境中做第 3 级 P0 写入后最小问答验证；不进入第 4 级。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description="第 3 级 P0 SQL 示例受控写入")
    parser.add_argument("--initial-git-status", required=True)
    args = parser.parse_args()

    samples, _ = load_and_validate_samples()
    before = directory_fingerprint(VANNA_DATA_DIR, label="vanna_data")
    query_results_before = list_query_result_files()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"level3_p0_training_{timestamp}"
    require(not backup_dir.exists(), f"备份目录已存在：{backup_dir}")
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(VANNA_DATA_DIR, backup_dir)
    backup_fingerprint = directory_fingerprint(backup_dir, label="vanna_data")
    require(before == backup_fingerprint, "正式 vanna_data 备份指纹不一致，停止写入")

    results = await write_samples(samples)
    after = directory_fingerprint(VANNA_DATA_DIR, label="vanna_data")
    changed = changed_files(before, after)
    query_results_after = list_query_result_files()
    new_query_results = query_results_after - query_results_before
    vanna_change_expected = bool(changed) and all(path.startswith("vanna_data/") for path in changed)
    write_report(
        initial_status=args.initial_git_status,
        base_commit=git_output("rev-parse", "HEAD"),
        backup_dir=backup_dir,
        before=before,
        after=after,
        changed=changed,
        results=results,
        new_query_results=new_query_results,
        vanna_change_expected=vanna_change_expected,
    )

    success_count = sum(item["write_status"] == "success" for item in results)
    failed_count = sum(item["write_status"] == "failed" for item in results)
    skipped_count = 18 - len(results)
    passed = (
        success_count == 18
        and failed_count == 0
        and skipped_count == 0
        and not new_query_results
        and vanna_change_expected
    )
    print(f"备份目录：{backup_dir}")
    print(f"写入成功数量：{success_count}")
    print(f"写入失败数量：{failed_count}")
    print(f"skipped 数量：{skipped_count}")
    print(f"变化文件：{', '.join(changed) if changed else '无'}")
    print(f"报告：{REPORT_PATH}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
