from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DRAFT_PATH = PROJECT_ROOT / "training" / "sql_examples_level2_draft.json"
REPORT_PATH = PROJECT_ROOT / "training" / "sql_examples_level2_training_result.md"
VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
BLOCKED_IDS = {"L2_SQL_011", "L2_SQL_012", "L2_SQL_019"}
EXPECTED_APPROVED_COUNT = 16
EXPECTED_REVIEW_COUNT = 3
EXPECTED_EXCLUDED_COUNT = 0

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.memory import create_memory
from backend.sql_guard import SQLGuard
from vanna.core.registry import ToolRegistry
from vanna.core.tool import ToolContext
from vanna.core.user import User


@dataclass
class FingerprintEntry:
    path: str
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chroma_fingerprint() -> dict[str, FingerprintEntry]:
    if not VANNA_DATA_DIR.exists():
        return {}
    entries: dict[str, FingerprintEntry] = {}
    for path in sorted(p for p in VANNA_DATA_DIR.rglob("*") if p.is_file()):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        entries[rel] = FingerprintEntry(
            path=rel,
            size=path.stat().st_size,
            sha256=file_sha256(path),
        )
    return entries


def changed_files(
    before: dict[str, FingerprintEntry], after: dict[str, FingerprintEntry]
) -> list[str]:
    changed: list[str] = []
    for path in sorted(set(before) | set(after)):
        if path not in before or path not in after:
            changed.append(path)
            continue
        if before[path].sha256 != after[path].sha256 or before[path].size != after[path].size:
            changed.append(path)
    return changed


def format_fingerprint(entries: dict[str, FingerprintEntry]) -> list[str]:
    lines: list[str] = []
    for entry in entries.values():
        lines.append(f"- {entry.path} | size={entry.size} | sha256={entry.sha256}")
    return lines or ["- 无"]


def is_safe_select(sql: str) -> tuple[bool, str]:
    if not re.match(r"^\s*select\b", sql, flags=re.I):
        return False, "SQL 不是 SELECT"
    if not re.search(r"\blimit\b", sql, flags=re.I):
        return False, "SQL 缺少 LIMIT"
    if re.search(r"select\s+\*", sql, flags=re.I):
        return False, "SQL 包含 SELECT *"
    if re.search(
        r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge|grant|revoke)\b",
        sql,
        flags=re.I,
    ):
        return False, "SQL 包含 DDL/DML 或禁止操作"
    if re.search(r"\b(information_schema|pg_catalog|sqlite_master|sqlite_schema)\b", sql, flags=re.I):
        return False, "SQL 访问系统表"
    return True, "ok"


def load_and_validate_samples() -> tuple[list[dict[str, Any]], list[str], list[str], int, int, int]:
    samples = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    approved = [sample for sample in samples if sample.get("train_decision") == "approved"]
    review_ids = [
        str(sample.get("id"))
        for sample in samples
        if sample.get("train_decision") == "requires_manual_review"
    ]
    excluded_ids = [
        str(sample.get("id"))
        for sample in samples
        if sample.get("train_decision") == "excluded"
    ]

    approved_count = len(approved)
    review_count = len(review_ids)
    excluded_count = len(excluded_ids)
    if approved_count != EXPECTED_APPROVED_COUNT:
        raise RuntimeError(f"approved 数量应为 16，实际为 {approved_count}")
    if review_count != EXPECTED_REVIEW_COUNT:
        raise RuntimeError(f"requires_manual_review 数量应为 3，实际为 {review_count}")
    if excluded_count != EXPECTED_EXCLUDED_COUNT:
        raise RuntimeError(f"excluded 数量应为 0，实际为 {excluded_count}")
    if sorted(review_ids) != sorted(BLOCKED_IDS):
        raise RuntimeError(
            "requires_manual_review 样本应为 L2_SQL_011/L2_SQL_012/L2_SQL_019，实际为 "
            + ", ".join(review_ids)
        )

    train_ids = [str(sample.get("id")) for sample in approved]
    blocked_in_train = sorted(BLOCKED_IDS & set(train_ids))
    if blocked_in_train:
        raise RuntimeError("禁止训练样本进入训练列表：" + ", ".join(blocked_in_train))

    guard = SQLGuard()
    for sample in approved:
        sample_id = str(sample.get("id"))
        question = str(sample.get("question") or "")
        sql = str(sample.get("sql") or "")
        safe, reason = is_safe_select(sql)
        if not safe:
            raise RuntimeError(f"{sample_id} SQL 安全检查失败：{reason}")
        guard_result = guard.validate(sql=sql, query=question)
        if not guard_result.passed or guard_result.severity != "ok":
            raise RuntimeError(
                f"{sample_id} SQL Guard 不满足训练条件："
                f"passed={guard_result.passed}, severity={guard_result.severity}, reason={guard_result.reason}"
            )
    return approved, review_ids, excluded_ids, approved_count, review_count, excluded_count


def make_context(memory: Any) -> ToolContext:
    return ToolContext(
        user=User(id="level2_sql_trainer", username="level2_sql_trainer"),
        conversation_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        agent_memory=memory,
        metadata={"stage": "level2_sql_examples_training"},
    )


async def train_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    memory = create_memory()
    registry = ToolRegistry()
    _ = registry
    guard = SQLGuard()
    results: list[dict[str, Any]] = []

    for sample in samples:
        sample_id = str(sample["id"])
        question = str(sample["question"])
        sql = str(sample["sql"])
        guard_result = guard.validate(sql=sql, query=question)
        result: dict[str, Any] = {
            "id": sample_id,
            "question": question,
            "used_tables": guard_result.used_tables,
            "sql_guard": guard_result.to_dict(),
            "write_success": False,
            "error": "",
        }
        print(f"TRAIN {sample_id}")
        print(f"question={question}")
        print(f"used_tables={', '.join(guard_result.used_tables)}")
        print(f"severity={guard_result.severity}")
        if not guard_result.passed or guard_result.severity != "ok":
            result["error"] = "SQL Guard 不满足训练条件"
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
                    "training_level": "level2_sql_examples",
                    "sample_id": sample_id,
                    "source": "training/sql_examples_level2_draft.json",
                    "train_decision": "approved",
                },
            )
            result["write_success"] = True
            print("write_success=True")
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
            print(f"write_success=False error={exc}")
            results.append(result)
            break
        results.append(result)

    return results


def write_report(
    *,
    args: argparse.Namespace,
    remote: str,
    base_commit: str,
    before: dict[str, FingerprintEntry],
    after: dict[str, FingerprintEntry],
    changed: list[str],
    approved_count: int,
    train_ids: list[str],
    review_ids: list[str],
    excluded_ids: list[str],
    train_results: list[dict[str, Any]],
    final_status: str,
) -> None:
    all_success = len(train_results) == len(train_ids) and all(
        item["write_success"] for item in train_results
    )
    lines = [
        "# 第 2 级 SQL 示例训练写入结果",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        remote,
        "```",
        "- 初始 git status --short：",
        "```text",
        args.initial_status or "clean",
        "```",
        f"- 当前基础 commit：{base_commit}",
        f"- 备份目录：{args.backup_dir}",
        f"- approved 样本总数：{approved_count}",
        f"- 实际训练样本总数：{len(train_results)}",
        f"- 实际训练样本 ID 列表：{', '.join(train_ids)}",
        "- 明确未训练样本 ID：L2_SQL_011, L2_SQL_012, L2_SQL_019",
        f"- requires_manual_review 样本：{', '.join(review_ids) if review_ids else '无'}",
        f"- excluded 样本：{', '.join(excluded_ids) if excluded_ids else '无'}",
        f"- 是否训练 requires_manual_review：{'是' if any(item['id'] in review_ids for item in train_results) else '否'}",
        f"- 是否训练 excluded：{'是' if any(item['id'] in excluded_ids for item in train_results) else '否'}",
        "- 是否执行真实 SQL：否",
        "- 是否连接数据库：否",
        "- 是否启动真实主服务：否",
        "- 是否写入 ChromaDB：是",
        "- 是否修改数据库结构：否",
        "- 是否进入第 3/4 级：否",
        f"- 训练是否全部成功：{'是' if all_success else '否'}",
        "",
        "## 训练前 ChromaDB 指纹",
        "",
        *format_fingerprint(before),
        "",
        "## 训练后 ChromaDB 指纹",
        "",
        *format_fingerprint(after),
        "",
        "## ChromaDB 变化文件列表",
        "",
        *(f"- {path}" for path in changed),
        "",
        "## 训练明细",
        "",
    ]

    for item in train_results:
        guard = item["sql_guard"]
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- id：{item['id']}",
                f"- question：{item['question']}",
                f"- used_tables：{', '.join(item['used_tables']) or '无'}",
                f"- SQL Guard result：passed={guard['passed']}；severity={guard['severity']}；reason={guard['reason']}",
                f"- 写入结果：{'成功' if item['write_success'] else '失败'}",
                f"- 错误信息：{item['error'] or '无'}",
                "",
            ]
        )

    lines.extend(
        [
            "## 训练后 Git 状态",
            "",
            "```text",
            final_status or "clean",
            "```",
            "",
            "## 结论",
            "",
            "- 当前结论：第 2 级 approved SQL 示例已受控写入 ChromaDB；requires_manual_review 与 excluded 样本未训练。",
            "- 下一步建议：先做最小问答验证，确认 P0 上下文、SQL Guard 和新写入 SQL 示例的协同效果；不要进入第 3/4 级，除非另起阶段审批。",
            f"- 回滚方式：如需回滚，将备份目录中的 vanna_data 覆盖回 {PROJECT_ROOT / 'vanna_data'}",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--initial-status", default="")
    args = parser.parse_args()

    remote = run_command(["git", "remote", "-v"])
    base_commit = run_command(["git", "rev-parse", "HEAD"])
    samples, review_ids, excluded_ids, approved_count, _, _ = load_and_validate_samples()
    train_ids = [str(sample["id"]) for sample in samples]

    print("待训练样本 ID:")
    for sample_id in train_ids:
        print(sample_id)
    print("禁止训练样本 ID:")
    for sample_id in sorted(BLOCKED_IDS):
        print(sample_id)

    before = chroma_fingerprint()
    train_results = await train_samples(samples)
    after = chroma_fingerprint()
    changed = changed_files(before, after)
    final_status = run_command(["git", "status", "--short"])
    write_report(
        args=args,
        remote=remote,
        base_commit=base_commit,
        before=before,
        after=after,
        changed=changed,
        approved_count=approved_count,
        train_ids=train_ids,
        review_ids=review_ids,
        excluded_ids=excluded_ids,
        train_results=train_results,
        final_status=final_status,
    )

    all_success = len(train_results) == len(train_ids) and all(
        item["write_success"] for item in train_results
    )
    print(f"approved 样本总数: {approved_count}")
    print(f"实际训练样本总数: {len(train_results)}")
    print("实际训练样本 ID 列表: " + ", ".join(train_ids))
    print("ChromaDB 变化文件列表: " + (", ".join(changed) if changed else "无"))
    print(f"训练是否全部成功: {'是' if all_success else '否'}")
    print(f"报告: {REPORT_PATH}")
    return 0 if all_success else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
