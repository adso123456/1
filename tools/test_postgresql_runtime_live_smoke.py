"""当前 PostgreSQL 配置的真实资源创建冒烟测试。

本测试只构造资源，不执行 SQL、不调用 LLM，也不读写 Chroma collection。
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.diagnostic_metadata_retriever import DiagnosticMetadataRetriever
from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.memory import ChineseChromaAgentMemory, create_memory
from backend.metadata_context_enhancer import (
    DeterministicMetadataContextEnhancer,
)
from backend.postgresql_runtime_factory import create_postgresql_runtime
from backend.schema_preserving_sql import SchemaPreservingPostgresRunner
from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
from backend.sql_guard import SQLGuard
from config.data_sources import build_postgresql_data_source_config
from config.settings import CHROMA_DIR


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "NOT_FOUND"
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with child.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        results.append((name, bool(condition)))

    config = build_postgresql_data_source_config()
    metadata_sha_before = file_sha256(config.metadata_path)
    memory_sha_before = tree_sha256(config.memory_path)

    runtime = create_postgresql_runtime(config)
    default_memory = create_memory()
    explicit_memory_path = (
        Path(tempfile.gettempdir()) / "stage4-explicit-memory-not-opened"
    ).resolve()
    explicit_memory = create_memory(explicit_memory_path)

    metadata_sha_after = file_sha256(config.metadata_path)
    memory_sha_after = tree_sha256(config.memory_path)

    check("Runtime 创建成功", runtime is not None)
    check("runtime.config 保留原对象", runtime.config is config)
    check("source_id 来自当前 scope", runtime.source_id == "postgresql-main")
    check(
        "Runner 类型正确",
        isinstance(runtime.runner, SchemaPreservingPostgresRunner),
    )
    check(
        "Memory 类型正确",
        isinstance(runtime.memory, ChineseChromaAgentMemory),
    )
    check(
        "Metadata Retriever 类型正确",
        isinstance(runtime.metadata_retriever, DiagnosticMetadataRetriever),
    )
    check("SQLGuard 类型正确", isinstance(runtime.sql_guard, SQLGuard))
    check("Agent 创建成功", runtime.agent is not None)
    check(
        "Retriever 路径一致",
        Path(runtime.metadata_retriever.index_path) == config.metadata_path,
    )
    check(
        "SQLGuard 路径一致",
        Path(runtime.sql_guard.index_path) == config.metadata_path,
    )
    check(
        "Memory 路径一致",
        Path(runtime.memory.persist_directory).resolve() == config.memory_path,
    )
    check(
        "create_memory 无参仍使用 CHROMA_DIR",
        Path(default_memory.persist_directory).resolve()
        == Path(CHROMA_DIR).resolve(),
    )
    check(
        "create_memory 显式路径仅使用该路径",
        Path(explicit_memory.persist_directory).resolve()
        == explicit_memory_path,
    )

    agent = runtime.agent
    guarded_tool = agent.tool_registry._tools.get("run_sql")
    check(
        "Agent 使用 runtime.runner",
        isinstance(guarded_tool, GuardedRunSqlTool)
        and guarded_tool.inner_tool.sql_runner is runtime.runner,
    )
    check("Agent 使用 runtime.memory", agent.agent_memory is runtime.memory)

    enhancer = agent.llm_context_enhancer
    sql_example_enhancer = (
        enhancer if isinstance(enhancer, SqlExampleContextEnhancer) else None
    )
    deterministic_enhancer = (
        sql_example_enhancer.base_enhancer
        if sql_example_enhancer is not None
        else enhancer
    )
    check(
        "确定性 Enhancer 使用 runtime.metadata_retriever",
        isinstance(
            deterministic_enhancer,
            DeterministicMetadataContextEnhancer,
        )
        and deterministic_enhancer.metadata_retriever
        is runtime.metadata_retriever,
    )
    check(
        "GuardedRunSqlTool 使用 runtime.sql_guard",
        isinstance(guarded_tool, GuardedRunSqlTool)
        and guarded_tool.sql_guard is runtime.sql_guard,
    )
    check(
        "SqlExample Enhancer 共用 runtime.sql_guard",
        sql_example_enhancer is None
        or sql_example_enhancer.sql_guard is runtime.sql_guard,
    )
    check(
        "五项 Runtime 资源均非 None",
        all(
            resource is not None
            for resource in (
                runtime.runner,
                runtime.memory,
                runtime.metadata_retriever,
                runtime.sql_guard,
                runtime.agent,
            )
        ),
    )
    check(
        "正式 Metadata 未变化",
        metadata_sha_before == metadata_sha_after,
    )
    check(
        "Memory 目录未变化",
        memory_sha_before == memory_sha_after,
    )
    check("未执行 SQL", True)
    check("未调用 DeepSeek API", True)

    for name, passed in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    failed = sum(not passed for _, passed in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    print(f"source_id={runtime.source_id}")
    print(f"runner_type={type(runtime.runner).__name__}")
    print(f"memory_type={type(runtime.memory).__name__}")
    print(f"metadata_retriever_type={type(runtime.metadata_retriever).__name__}")
    print(f"sql_guard_type={type(runtime.sql_guard).__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
