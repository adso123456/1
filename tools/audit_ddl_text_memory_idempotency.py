"""审计 DDL Text Memory 写入链，并仅在显式隔离目录中复现重复写入。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.ddl_memory_identity import (  # noqa: E402
    DdlMemoryIdentityInput,
    build_ddl_memory_identity,
    normalize_ddl,
)


REPOSITORY_CHROMA = (PROJECT_ROOT / "vanna_data").resolve()
FORMAL_CHROMA = Path(r"E:\3\_runtime\vanna-level1\vanna_data").resolve()
EVIDENCE_ROOT = Path(r"E:\3\_training_backups").resolve()
COLLECTION_NAME = "tool_memories"
SAMPLE_SIZE = 25


def _historical_effective_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """旧记录分组时排除运行时字段；不冒充 v1 有效 Metadata。"""
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"timestamp", "content"}
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_isolated_path(path: Path) -> Path:
    target = path.expanduser().resolve()
    if _is_within(target, FORMAL_CHROMA):
        raise ValueError(f"隔离目录禁止位于正式 Chroma 内：{target}")
    if _is_within(target, REPOSITORY_CHROMA):
        raise ValueError(f"隔离目录禁止位于仓库 Chroma 内：{target}")
    if _is_within(target, PROJECT_ROOT):
        raise ValueError(f"隔离目录必须位于仓库外：{target}")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"隔离目录必须全新或为空：{target}")
    return target


def validate_evidence_path(path: Path) -> Path:
    target = path.expanduser().resolve()
    if not _is_within(target, EVIDENCE_ROOT):
        raise ValueError(f"Evidence 必须位于 {EVIDENCE_ROOT} 下")
    if target.name != "evidence" or not re.fullmatch(
        r"f6-1(?:a(?:-r1)?|b)-\d{8}-\d{6}", target.parent.name
    ):
        raise ValueError(
            "Evidence 路径必须为 f6-1a[-r1]- 或 f6-1b-<YYYYMMDD-HHMMSS>\\evidence"
        )
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Evidence 目录必须全新或为空：{target}")
    return target


def static_audit() -> dict[str, Any]:
    return {
        "ddl_source": "agent_data/column_metadata_index.json",
        "ddl_source_loader": "train_step3.load_metadata_index",
        "ddl_constructor": [
            "train_step3.group_tables",
            "train_step3.build_table_ddl",
            "train_step3.build_all_table_ddls",
        ],
        "write_entry": "train_step3._run_training",
        "memory_factory": "agent_config.create_memory",
        "memory_api": "ChromaAgentMemory.save_text_memory",
        "chroma_api": "Collection.upsert",
        "collection_name": COLLECTION_NAME,
        "current_id_strategy": "Vanna ChromaAgentMemory._create_memory_id 生成 UUID4；调用方不提供 ID",
        "metadata_fields": ["content", "timestamp", "is_text_memory"],
        "duplicate_check": False,
        "root_cause": (
            "save_text_memory 每次生成新 UUID4，随后按该新 ID 调用 upsert；"
            "调用链没有按逻辑对象、内容或 metadata 查重，因此相同内容再次执行会新增记录。"
        ),
        "other_write_entry_points": {
            "historical_train_step3": (
                "提交 66fe0c2 中 train_step3.main 从 PostgreSQL 只读提取 6 表 DDL，"
                "并直接调用同一 save_text_memory；该版本仍可从 Git 历史恢复执行，但不在当前工作树。"
            ),
            "generic_save_text_memory_tool": (
                "vanna_src/src/vanna/tools/agent_memory.py:SaveTextMemoryTool 可调用同一 Memory API；"
                "当前 step4_server.py 未注册该工具，当前生产服务不可达。"
            ),
        },
        "recommended_single_entry": (
            "后续由 F6-1C/F6-1D 的 plan/apply 受控入口独占 DDL 写入；"
            "train_step3 仅保留输入生成或改为调用该受控入口。"
        ),
        "formal_chroma_client_open_attempt_count_by_script": 0,
    }


def _group_records(
    documents: Sequence[str], metadatas: Sequence[Mapping[str, Any]]
) -> tuple[int, int, int, int]:
    effective_groups: dict[str, int] = defaultdict(int)
    exact_groups: dict[str, int] = defaultdict(int)
    for document, metadata in zip(documents, metadatas):
        normalized = normalize_ddl(document)
        effective_key = json.dumps(
            [normalized, _historical_effective_metadata(metadata)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        exact_key = json.dumps(
            [normalized, dict(metadata)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        effective_groups[effective_key] += 1
        exact_groups[exact_key] += 1

    duplicate_sizes = [size for size in effective_groups.values() if size > 1]
    exact_duplicate_sizes = [size for size in exact_groups.values() if size > 1]
    return (
        len(duplicate_sizes),
        sum(duplicate_sizes),
        len(exact_duplicate_sizes),
        sum(exact_duplicate_sizes),
    )


def validate_reproduction_result(result: Mapping[str, Any]) -> None:
    """强制校验 F6-1A 隔离复现的完整预期结果。"""
    expected = {
        "before_count": 0,
        "first_run_created": SAMPLE_SIZE,
        "after_first_count": SAMPLE_SIZE,
        "second_run_created": SAMPLE_SIZE,
        "after_second_count": SAMPLE_SIZE * 2,
        "duplicate_group_count": SAMPLE_SIZE,
        "duplicate_record_count": SAMPLE_SIZE * 2,
        "duplicate_excess_record_count": SAMPLE_SIZE,
        "unique_memory_id_count": SAMPLE_SIZE * 2,
        "collection_name": COLLECTION_NAME,
    }
    failures = [
        f"{key}: expected={expected_value!r}, actual={result.get(key)!r}"
        for key, expected_value in expected.items()
        if result.get(key) != expected_value
    ]
    if failures:
        raise ValueError("复现结果验收失败：" + "; ".join(failures))


async def reproduce(isolated_chroma: Path, evidence_dir: Path) -> dict[str, Any]:
    isolated_chroma.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)

    import train_step3

    records = train_step3.load_metadata_index()
    tables = train_step3.group_tables(records)
    generated, _ = train_step3.build_all_table_ddls(tables)
    selected = sorted(generated, key=lambda item: item["table"])[:SAMPLE_SIZE]
    if len(selected) != SAMPLE_SIZE:
        raise RuntimeError(f"代表性 DDL 不足 {SAMPLE_SIZE} 条")

    os.environ["VANNA_DATA_DIR"] = str(isolated_chroma)
    from agent_config import create_memory
    from vanna.core.registry import ToolRegistry
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    memory = create_memory()
    collection = memory._get_collection()
    user = User(id="f6-1a-auditor", username="f6-1a-auditor")
    registry = ToolRegistry()

    def context() -> ToolContext:
        return ToolContext(
            user=user,
            conversation_id="f6-1a-isolated-reproduction",
            request_id="f6-1a-isolated-reproduction",
            agent_memory=memory,
            tool_registry=registry,
        )

    before_count = collection.count()
    first_ids = []
    for item in selected:
        result = await memory.save_text_memory(content=item["ddl"], context=context())
        first_ids.append(result.memory_id)
    after_first_count = collection.count()

    second_ids = []
    for item in selected:
        result = await memory.save_text_memory(content=item["ddl"], context=context())
        second_ids.append(result.memory_id)
    after_second_count = collection.count()

    stored = collection.get(include=["documents", "metadatas"])
    documents = list(stored.get("documents") or [])
    metadatas = list(stored.get("metadatas") or [])
    duplicate_groups, duplicate_records, exact_groups, exact_records = _group_records(
        documents, metadatas
    )

    result = {
        "audit_timestamp": datetime.now().astimezone().isoformat(),
        "selection_rule": (
            "当前 Level 1 的 115 条真实生成结果按 table 字段升序排列，稳定选取前 25 条；"
            "历史约 25 条输入文件在当前仓库中不存在。"
        ),
        "selected_table_names": [item["table"] for item in selected],
        "selected_content_sha256": [
            hashlib.sha256(normalize_ddl(item["ddl"]).encode("utf-8")).hexdigest()
            for item in selected
        ],
        "before_count": before_count,
        "first_run_created": after_first_count - before_count,
        "after_first_count": after_first_count,
        "second_run_created": after_second_count - after_first_count,
        "after_second_count": after_second_count,
        "duplicate_group_count": duplicate_groups,
        "duplicate_record_count": duplicate_records,
        "duplicate_excess_record_count": duplicate_records - duplicate_groups,
        "unique_memory_id_count": len(set(first_ids + second_ids)),
        "exact_storage_metadata_duplicate_group_count": exact_groups,
        "exact_storage_metadata_duplicate_record_count": exact_records,
        "grouping_rule": (
            "规范化 document + effective metadata 分组；effective metadata 排除每次写入生成的 "
            "timestamp，并排除已由 document 表达的 content，保留 is_text_memory。"
        ),
        "record_id_strategy": (
            "Vanna 为每次调用生成 UUID4；两轮共 50 个 ID，唯一数 "
            f"{len(set(first_ids + second_ids))}。"
        ),
        "metadata_fields": sorted({key for item in metadatas for key in item}),
        "collection_name": collection.name,
        "formal_chroma_client_open_attempt_count_by_script": 0,
        "formal_chroma_path": str(FORMAL_CHROMA),
        "repository_chroma_path": str(REPOSITORY_CHROMA),
        "isolated_chroma_path": str(isolated_chroma),
    }

    evidence = {
        "static_audit": static_audit(),
        "reproduction": result,
        "reproduction_validation": {"status": "PENDING", "failure_reason": None},
        "sensitive_data_policy": (
            "未保存密码、API Key、完整数据库数据、完整 DDL 或查询结果；"
            "仅保存表名、内容哈希、计数和非敏感结构证据。"
        ),
    }
    evidence_path = evidence_dir / "f6-1a-audit-evidence.json"
    try:
        validate_reproduction_result(result)
    except ValueError as exc:
        evidence["reproduction_validation"] = {
            "status": "FAIL",
            "failure_reason": str(exc),
        }
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise

    evidence["reproduction_validation"] = {"status": "PASS", "failure_reason": None}
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def self_test() -> int:
    assert normalize_ddl("  A  \r\nB\t\r\n") == "A\nB"
    identity_input = DdlMemoryIdentityInput(
        source_id="postgres_water",
        schema_name="public",
        object_type="table",
        object_name="demo",
    )
    first = build_ddl_memory_identity(identity_input, "DDL")
    repeated = build_ddl_memory_identity(identity_input, "DDL")
    assert first == repeated
    assert first.record_id == f"ddlmem-v1-{first.logical_id}"
    grouped = _group_records(
        ["DDL\r\n", "DDL\n"],
        [
            {"content": "DDL\r\n", "timestamp": "1", "is_text_memory": True},
            {"content": "DDL\n", "timestamp": "2", "is_text_memory": True},
        ],
    )
    assert grouped == (1, 2, 0, 0)
    protected_paths = (
        FORMAL_CHROMA,
        FORMAL_CHROMA / "child",
        REPOSITORY_CHROMA,
        REPOSITORY_CHROMA / "child",
        PROJECT_ROOT / "docs" / "isolated_chroma",
    )
    for protected in protected_paths:
        try:
            validate_isolated_path(protected)
        except ValueError:
            pass
        else:
            raise AssertionError(f"未拒绝受保护路径：{protected}")

    valid_result = {
        "before_count": 0,
        "first_run_created": 25,
        "after_first_count": 25,
        "second_run_created": 25,
        "after_second_count": 50,
        "duplicate_group_count": 25,
        "duplicate_record_count": 50,
        "duplicate_excess_record_count": 25,
        "unique_memory_id_count": 50,
        "collection_name": COLLECTION_NAME,
    }
    validate_reproduction_result(valid_result)
    invalid_result = dict(valid_result)
    invalid_result["second_run_created"] = 0
    try:
        validate_reproduction_result(invalid_result)
    except ValueError as exc:
        assert "second_run_created" in str(exc)
    else:
        raise AssertionError("未拒绝第二轮零增长的不合格复现结果")

    assert static_audit()["formal_chroma_client_open_attempt_count_by_script"] == 0
    print("SELF_TEST=PASS")
    print("PATH_GUARD_TEST=PASS")
    print("REPRODUCTION_GATE_TEST=PASS")
    print("FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--isolated-chroma", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        if args.isolated_chroma or args.evidence_dir:
            raise SystemExit("--self-test 不接受复现参数")
        return self_test()

    if args.isolated_chroma is None and args.evidence_dir is None:
        print(json.dumps(static_audit(), ensure_ascii=False, indent=2))
        return 0
    if args.isolated_chroma is None or args.evidence_dir is None:
        raise SystemExit("复现必须同时显式传入 --isolated-chroma 和 --evidence-dir")

    try:
        isolated = validate_isolated_path(args.isolated_chroma)
        evidence = validate_evidence_path(args.evidence_dir)
        result = asyncio.run(reproduce(isolated, evidence))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"AUDIT_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for key in (
        "before_count",
        "first_run_created",
        "after_first_count",
        "second_run_created",
        "after_second_count",
        "duplicate_group_count",
        "duplicate_record_count",
        "duplicate_excess_record_count",
        "unique_memory_id_count",
        "record_id_strategy",
        "formal_chroma_client_open_attempt_count_by_script",
    ):
        print(f"{key}={result[key]}")
    print("reproduction_validation=PASS")
    print(f"evidence_dir={evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
