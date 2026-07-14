"""0B-3C 受控 Chroma Tool Memory 适配层合成测试。"""

from __future__ import annotations

import asyncio
import copy
import gc
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.batch_validator import validate_training_batch
from training.sop.chroma_tool_memory_adapter import (
    ChromaToolMemoryAdapter,
    ChromaToolMemoryAdapterError,
    ExpectedCreatedRecord,
)
from training.sop.memory_write_plan import build_memory_write_plan
from vanna.core.tool import ToolContext
from vanna.core.user import User
from vanna.integrations.chromadb import ChromaAgentMemory


FIXTURE = PROJECT_ROOT / "tools" / "fixtures" / "training_sop" / "valid_batch.json"


class DeterministicEmbeddingFunction:
    """不访问网络的固定维度字符散列 embedding。"""

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        vectors: list[list[float]] = []
        for text in input:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([byte / 255.0 for byte in digest])
        return vectors

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)

    @staticmethod
    def name() -> str:
        return "training-sop-deterministic-embedding"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "DeterministicEmbeddingFunction":
        del config
        return DeterministicEmbeddingFunction()


class FakeGuard:
    def validate(self, sql: str, query: str) -> SimpleNamespace:
        del query
        tables = [
            match.split(".")[-1].strip('"').lower()
            for match in re.findall(
                r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", sql, re.I
            )
        ]
        return SimpleNamespace(
            passed=True,
            severity="ok",
            reason="synthetic guard passed",
            used_tables=sorted(set(tables)),
        )


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=True,
    ).stdout


def make_plan(batch: dict[str, Any] | None = None):
    data = batch or json.loads(FIXTURE.read_text(encoding="utf-8"))
    validation = validate_training_batch(data, sql_guard=FakeGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise AssertionError(validation.to_dict())
    return build_memory_write_plan(
        data,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=FakeGuard(),
    )


def add_raw(collection: Any, storage_id: str, item: Any, *, document: str | None = None, metadata_changes: dict[str, Any] | None = None) -> None:
    metadata = {
        "question": item.canonical_content["question"],
        "tool_name": item.canonical_content["tool_name"],
        "args_json": json.dumps(item.canonical_content["args"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "success": True,
        "metadata_json": json.dumps(item.compatibility_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }
    metadata.update(item.governance_metadata)
    if metadata_changes:
        metadata.update(metadata_changes)
    collection.add(
        ids=[storage_id],
        documents=[document if document is not None else item.canonical_content["question"]],
        metadatas=[metadata],
    )


def record_result(results: list[tuple[str, bool, str]], name: str, passed: bool, detail: Any = "") -> None:
    results.append((name, bool(passed), str(detail)))


def expect_adapter_error(callback: Any, code: str) -> tuple[bool, str]:
    try:
        callback()
    except ChromaToolMemoryAdapterError as error:
        return error.code == code, str(error)
    except Exception as error:  # noqa: BLE001
        return False, f"unexpected {type(error).__name__}: {error}"
    return False, "未抛出异常"


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    status_before = git_status()
    temp_parent = Path(tempfile.mkdtemp(prefix="vanna-0b3c-"))
    isolated_root = temp_parent / "isolated"
    isolated_root.mkdir()
    persist = isolated_root / "synthetic-chroma"
    memory: ChromaAgentMemory | None = None
    adapter: ChromaToolMemoryAdapter | None = None

    try:
        plan = make_plan()
        first, second = plan.items
        memory = ChromaAgentMemory(
            persist_directory=str(persist),
            collection_name="tool_memories",
            embedding_function=DeterministicEmbeddingFunction(),
        )
        adapter = ChromaToolMemoryAdapter(
            memory,
            isolated_root=isolated_root,
        )
        record_result(results, "隔离路径检查通过", True, persist)

        fake_formal = SimpleNamespace(
            collection_name="tool_memories",
            persist_directory=str(PROJECT_ROOT / "vanna_data"),
        )
        formal_rejected, formal_detail = expect_adapter_error(
            lambda: ChromaToolMemoryAdapter(
                fake_formal,
                isolated_root=temp_parent,
            ),
            "FORMAL_STORE_REJECTED",
        )
        record_result(results, "正式项目路径被失败关闭", formal_rejected, formal_detail)

        wrong_memory = SimpleNamespace(
            collection_name="wrong_collection",
            persist_directory=str(isolated_root / "wrong"),
        )
        wrong_rejected, wrong_detail = expect_adapter_error(
            lambda: ChromaToolMemoryAdapter(
                wrong_memory,
                isolated_root=isolated_root,
            ),
            "INVALID_COLLECTION_NAME",
        )
        record_result(results, "错误 collection 名称被拒绝", wrong_rejected, wrong_detail)

        fake_agent_data = SimpleNamespace(
            collection_name="tool_memories",
            persist_directory=str(PROJECT_ROOT / "agent_data"),
        )
        agent_data_rejected, agent_data_detail = expect_adapter_error(
            lambda: ChromaToolMemoryAdapter(fake_agent_data, isolated_root=temp_parent),
            "FORMAL_STORE_REJECTED",
        )
        record_result(results, "正式 agent_data 路径被失败关闭", agent_data_rejected, agent_data_detail)

        reparse_path = isolated_root / "synthetic-reparse"
        reparse_path.mkdir()
        fake_reparse = SimpleNamespace(
            collection_name="tool_memories",
            persist_directory=str(reparse_path),
        )
        with patch(
            "training.sop.chroma_tool_memory_adapter._is_reparse_point",
            side_effect=lambda path: path == reparse_path,
        ):
            reparse_rejected, reparse_detail = expect_adapter_error(
                lambda: ChromaToolMemoryAdapter(fake_reparse, isolated_root=isolated_root),
                "REPARSE_PATH_REJECTED",
            )
        record_result(results, "链接 junction 或 reparse point 被拒绝", reparse_rejected, reparse_detail)

        empty_one = adapter.inventory_tool_records()
        empty_two = adapter.inventory_tool_records()
        record_result(results, "空 collection 清点计数稳定", empty_one.store_count_before == empty_one.store_count_after == 0)
        record_result(results, "空 collection 清点摘要确定一致", empty_one.inventory_sha256 == empty_two.inventory_sha256)

        collection = adapter._collection  # 仅测试合成底层异常和旧格式，不属于业务代码
        collection.add(
            ids=["synthetic-text-memory"],
            documents=["仅用于分类的合成文本"],
            metadatas=[{"is_text_memory": True, "content": "仅用于分类的合成文本"}],
        )
        with_text = adapter.inventory_tool_records()
        record_result(results, "Text Memory 被分类并排除", with_text.classifications["text_memory"] == 1 and not with_text.derived_record_ids)

        created = adapter.add_planned_record(plan, first)
        record_result(results, "受控 add 写入成功", created.status == "created", created)
        record_result(results, "写入使用计划确定性 ID", created.record_id == first.record_id)
        exact = adapter.get_exact_records([first.record_id])[0]
        record_result(results, "写后精确读取成功", exact.status == "found", exact.status)
        record_result(results, "写后 canonical content 一致", exact.record is not None and exact.record.canonical_content == first.canonical_content)
        record_result(results, "顶层治理 metadata 完整", exact.record is not None and all(key in (exact.record.metadata or {}) for key in first.governance_metadata))
        record_result(results, "compatibility metadata 完整", exact.record is not None and exact.record.compatibility_metadata == first.compatibility_metadata)

        user = User(id="synthetic-user", username="synthetic", group_memberships=[])
        context = ToolContext(
            user=user,
            conversation_id="synthetic-conversation",
            request_id="synthetic-request",
            agent_memory=memory,
            metadata={},
        )
        search = asyncio.run(
            memory.search_similar_usage(
                first.canonical_content["question"],
                context,
                limit=10,
                similarity_threshold=0.0,
                tool_name_filter="run_sql",
            )
        )
        recalled = next((item for item in search if item.memory.memory_id == first.record_id), None)
        record_result(results, "现有 search_similar_usage 可召回", recalled is not None)
        record_result(results, "检索反序列化 question/tool/sql 正确", recalled is not None and recalled.memory.question == first.canonical_content["question"] and recalled.memory.tool_name == "run_sql" and recalled.memory.args["sql"] == first.canonical_content["args"]["sql"])
        record_result(results, "检索保留关键 compatibility 字段", recalled is not None and all(key in recalled.memory.metadata for key in ("sample_id", "training_level", "train_decision", "expected_tables")))

        repeated = adapter.add_planned_record(plan, first)
        record_result(results, "重复 add 不覆盖已有记录", repeated.status == "existing_same", repeated.status)

        conflict_batch = json.loads(FIXTURE.read_text(encoding="utf-8"))
        conflict_batch["samples"][0]["source"] = "synthetic changed source"
        conflict_plan = make_plan(conflict_batch)
        conflict_item = copy.deepcopy(conflict_plan.items[0])
        object.__setattr__(conflict_item, "record_id", first.record_id)
        conflict_result = adapter.add_planned_record(conflict_plan, conflict_item)
        record_result(results, "相同 ID 不同计划内容被拒绝", conflict_result.status in {"blocked", "existing_conflict"}, conflict_result.status)

        legacy_id = "00000000-0000-4000-8000-000000000001"
        legacy_metadata = {
            "question": second.canonical_content["question"],
            "tool_name": second.canonical_content["tool_name"],
            "args_json": json.dumps(second.canonical_content["args"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "success": True,
            "metadata_json": json.dumps(second.compatibility_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        }
        collection.add(
            ids=[legacy_id],
            documents=[second.canonical_content["question"]],
            metadatas=[legacy_metadata],
        )
        legacy_inventory = adapter.inventory_tool_records()
        record_result(results, "旧 UUID 被识别为 legacy_id_mismatch", legacy_id in legacy_inventory.legacy_id_mismatches)
        legacy_preflight = adapter.inspect_plan_records(plan)
        record_result(results, "旧 UUID 同内容阻断计划创建", not legacy_preflight.executable and second.record_id in legacy_preflight.legacy_conflicts)

        legacy_id_two = "00000000-0000-4000-8000-000000000002"
        collection.add(
            ids=[legacy_id_two],
            documents=[second.canonical_content["question"]],
            metadatas=[legacy_metadata],
        )
        duplicate_inventory = adapter.inventory_tool_records()
        record_result(results, "两个旧 UUID 同内容被识别为重复", second.record_id in duplicate_inventory.duplicate_existing_content and len(duplicate_inventory.duplicate_existing_content[second.record_id]) == 2)
        collection.delete(ids=[legacy_id, legacy_id_two])

        bad_id = "toolmem-v1-" + "0" * 64
        add_raw(collection, bad_id, second)
        address_inventory = adapter.inventory_tool_records()
        record_result(results, "确定性 ID 内容冲突被识别", bad_id in address_inventory.content_address_conflicts)
        collection.delete(ids=[bad_id])

        malformed_json_id = "toolmem-v1-" + "1" * 64
        add_raw(collection, malformed_json_id, second, metadata_changes={"args_json": "{"})
        malformed_inventory = adapter.inventory_tool_records()
        record_result(results, "JSON 损坏记录被识别", any(record.storage_id == malformed_json_id and record.classification == "malformed_record" for record in malformed_inventory.records))
        record_result(results, "JSON 损坏记录阻断预查", not adapter.inspect_plan_records(plan).executable)
        collection.delete(ids=[malformed_json_id])

        mismatch_id = "toolmem-v1-" + "2" * 64
        add_raw(collection, mismatch_id, second, document="不同文档")
        mismatch_inventory = adapter.inventory_tool_records()
        record_result(results, "document 与 question 不一致被识别", any(problem.code == "DOCUMENT_QUESTION_MISMATCH" for problem in mismatch_inventory.issues))
        collection.delete(ids=[mismatch_id])

        created_second = adapter.add_planned_record(plan, second)
        record_result(results, "第二条受控记录写入成功", created_second.status == "created")
        reverse_exact = adapter.get_exact_records([second.record_id, first.record_id])
        record_result(
            results,
            "精确读取保持调用者输入顺序",
            [result.record_id for result in reverse_exact] == [second.record_id, first.record_id]
            and all(result.status == "found" for result in reverse_exact),
        )
        batch_records = adapter.list_records_by_batch_digest(plan.batch_content_sha256)
        batch_ids = [record.storage_id for record in batch_records.records]
        record_result(results, "按顶层批次摘要返回精确排序集合", batch_ids == sorted([first.record_id, second.record_id]) and not batch_records.issues, batch_ids)

        extra_batch = copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))
        extra_batch["training_batch_id"] = "level1-extra-20260714-01"
        extra_batch["samples"] = [extra_batch["samples"][0]]
        extra_batch["expected_new_memory_count"] = 1
        extra_batch["samples"][0]["question"] += "（额外）"
        extra_plan = make_plan(extra_batch)
        extra_item = extra_plan.items[0]
        extra_governance = dict(extra_item.governance_metadata)
        extra_governance.update(
            created_by_training_batch_id=plan.training_batch_id,
            created_by_batch_content_sha256=plan.batch_content_sha256,
        )
        extra_compatibility = dict(extra_item.compatibility_metadata)
        extra_compatibility.update(
            training_batch_id=plan.training_batch_id,
            batch_content_sha256=plan.batch_content_sha256,
        )
        controlled_extra = replace(
            extra_item,
            governance_metadata=extra_governance,
            compatibility_metadata=extra_compatibility,
        )
        add_raw(collection, controlled_extra.record_id, controlled_extra)
        extra_set = adapter.list_records_by_batch_digest(plan.batch_content_sha256)
        extra_ids = {record.storage_id for record in extra_set.records} - {
            first.record_id,
            second.record_id,
        }
        record_result(
            results,
            "批次查询可暴露额外非计划记录",
            extra_ids == {controlled_extra.record_id} and not extra_set.issues,
            sorted(extra_ids),
        )

        expected_first = ExpectedCreatedRecord(first.record_id, first.memory_content_sha256)
        mixed_delete = adapter.delete_exact_created_records(
            [
                expected_first,
                ExpectedCreatedRecord(second.record_id, "e" * 64),
            ],
            plan.batch_content_sha256,
        )
        mixed_after = adapter.get_exact_records([first.record_id, second.record_id])
        record_result(
            results,
            "任一删除预期不符时整批不删除",
            not mixed_delete.deleted
            and [item.status for item in mixed_delete.items]
            == ["not_deleted", "content_mismatch"]
            and all(result.status == "found" for result in mixed_after),
        )
        wrong_owner = adapter.delete_exact_created_records([expected_first], "f" * 64)
        record_result(results, "creator digest 不匹配拒绝删除", wrong_owner.items[0].status == "ownership_mismatch" and not wrong_owner.deleted)
        wrong_content = adapter.delete_exact_created_records(
            [ExpectedCreatedRecord(first.record_id, "e" * 64)], plan.batch_content_sha256
        )
        record_result(results, "内容摘要不匹配拒绝删除", wrong_content.items[0].status == "content_mismatch" and not wrong_content.deleted)
        delete_first = adapter.delete_exact_created_records([expected_first], plan.batch_content_sha256)
        record_result(results, "正确归属下精确删除成功", delete_first.deleted and delete_first.items[0].status == "deleted")
        record_result(results, "删除后精确确认不存在", adapter.get_exact_records([first.record_id])[0].status == "missing")
        delete_missing = adapter.delete_exact_created_records([expected_first], plan.batch_content_sha256)
        record_result(results, "删除不存在明确返回 not_found", delete_missing.items[0].status == "not_found")

        original_collection = adapter._collection

        class FailingGetCollection:
            def get(self, *args: Any, **kwargs: Any) -> Any:
                del args, kwargs
                raise RuntimeError("synthetic storage failure")

        adapter._collection = FailingGetCollection()
        storage_failure = adapter.get_exact_records([second.record_id])[0]
        adapter._collection = original_collection
        record_result(results, "底层读取异常明确返回 storage_error", storage_failure.status == "storage_error")

        stable_one = adapter.inventory_tool_records()
        stable_two = adapter.inventory_tool_records()
        record_result(results, "清点 count 前后一致", stable_one.store_count_before == stable_one.store_count_after)
        record_result(results, "清点顺序和摘要确定一致", [record.storage_id for record in stable_one.records] == sorted(record.storage_id for record in stable_one.records) and stable_one.inventory_sha256 == stable_two.inventory_sha256)
        plan_preflight = adapter.inspect_plan_records(plan)
        record_result(results, "计划预查返回确定性摘要", len(plan_preflight.store_preflight_sha256) == 64 and plan_preflight.executable)

        original_collection = adapter._collection

        class ChangingCountCollection:
            def __init__(self) -> None:
                self.calls = 0

            def count(self) -> int:
                self.calls += 1
                return self.calls - 1

            def get(self) -> dict[str, list[Any]]:
                return {"ids": [], "documents": [], "metadatas": []}

        adapter._collection = ChangingCountCollection()
        count_rejected, count_detail = expect_adapter_error(
            adapter.inventory_tool_records,
            "STORE_CHANGED_DURING_INVENTORY",
        )
        adapter._collection = original_collection
        record_result(results, "清点前后 count 变化形成稳定错误", count_rejected, count_detail)
        record_result(results, "测试未导入 agent_config", "agent_config" not in sys.modules)
        record_result(results, "测试不含数据库、SQL 执行或 DeepSeek 调用", True)
    finally:
        adapter = None
        if memory is not None:
            memory._executor.shutdown(wait=True)
            if memory._client is not None:
                memory._client._system.stop()
            memory._collection = None
            memory._client = None
        memory = None
        gc.collect()
        try:
            from chromadb.api.client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(temp_parent, ignore_errors=False)

    record_result(results, "系统临时 Chroma 目录已清理", not temp_parent.exists())
    status_after = git_status()
    record_result(results, "测试前后 Git 工作区状态不变", status_before == status_after)

    passed = sum(ok for _, ok, _ in results)
    failed = len(results) - passed
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"SUMMARY: {passed} pass / {failed} fail")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
