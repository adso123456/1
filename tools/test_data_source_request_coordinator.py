"""请求级数据源编排器的纯离线与并发契约测试。"""

from __future__ import annotations

import ast
import importlib
import os
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.conversation_data_source_binding import (
    ConversationDataSourceBinding,
    ConversationDataSourceBindings,
)
from backend.data_source_registry import DataSourceRegistry
from config.data_source_config import DataSourceConfig


TEST_PASSWORD = "coordinator-password-that-must-not-appear"
BASE_CONNECTION_SETTINGS = {
    "host": "offline.invalid",
    "port": 5433,
    "database": "offline_database",
    "user": "offline_user",
    "password": TEST_PASSWORD,
    "connect_timeout": 10,
}


def _make_config(root: Path, source_id: str) -> DataSourceConfig:
    return DataSourceConfig(
        source_id=source_id,
        database_type="postgresql",
        sql_dialect="postgresql",
        connection_settings=dict(BASE_CONNECTION_SETTINGS),
        metadata_path=root / f"{source_id}-metadata-does-not-exist.json",
        memory_path=root / f"{source_id}-memory-does-not-exist",
        read_only=True,
    )


def _expect_error(
    callback: Callable[[], Any],
    expected_texts: tuple[str, ...],
    error_types: tuple[type[BaseException], ...] = (ValueError,),
) -> tuple[bool, str]:
    try:
        callback()
    except error_types as exc:
        message = str(exc)
        return (
            all(expected_text in message for expected_text in expected_texts)
            and TEST_PASSWORD not in message,
            message,
        )
    return False, "未抛出预期异常"


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    environment_reads: list[str] = []
    original_getenv = os.getenv

    def tracked_getenv(key: str, default: str | None = None) -> str | None:
        environment_reads.append(key)
        return original_getenv(key, default)

    os.getenv = tracked_getenv
    try:
        coordinator_module = importlib.import_module(
            "backend.data_source_request_coordinator"
        )
    finally:
        os.getenv = original_getenv

    DataSourceRequestContext = coordinator_module.DataSourceRequestContext
    DataSourceRequestCoordinator = (
        coordinator_module.DataSourceRequestCoordinator
    )

    with tempfile.TemporaryDirectory(
        prefix="data-source-coordinator-test-"
    ) as temp_name:
        root = Path(temp_name).resolve()
        first_config = _make_config(root, "postgresql-main")
        second_config = _make_config(root, "postgresql-archive")
        single_registry = DataSourceRegistry([first_config])
        registry = DataSourceRegistry([first_config, second_config])

        single = DataSourceRequestCoordinator(single_registry)
        single_context = single.resolve(
            "conversation-single",
            {"source_id": "postgresql-main"},
        )
        results.append(
            (
                "单数据源首次请求成功",
                single_context.conversation_id == "conversation-single"
                and single_context.source_id == "postgresql-main",
                repr(single_context),
            )
        )

        coordinator = DataSourceRequestCoordinator(registry)
        first_context = coordinator.resolve(
            "conversation-main",
            {"source_id": "postgresql-main"},
        )
        second_context = coordinator.resolve(
            "conversation-archive",
            {"source_id": "postgresql-archive"},
        )
        results.append(
            (
                "双数据源分别建立独立会话",
                first_context.source_id == "postgresql-main"
                and second_context.source_id == "postgresql-archive"
                and len(coordinator.bindings) == 2,
                str(tuple(coordinator.bindings)),
            )
        )
        results.append(
            (
                "返回 Registry 中同一个配置对象",
                first_context.config is first_config
                and second_context.config is second_config,
                (
                    f"first={first_context.config is first_config}, "
                    f"second={second_context.config is second_config}"
                ),
            )
        )

        repeated_context = coordinator.resolve(
            "conversation-main",
            {"source_id": "postgresql-main"},
        )
        results.append(
            (
                "同会话同 source_id 重复解析成功",
                repeated_context.source_id == first_context.source_id
                and repeated_context.config is first_context.config
                and len(coordinator.bindings) == 2,
                repr(repeated_context),
            )
        )

        conflict = _expect_error(
            lambda: coordinator.resolve(
                "conversation-main",
                {"source_id": "postgresql-archive"},
            ),
            (
                "conversation-main",
                "postgresql-main",
                "postgresql-archive",
            ),
        )
        results.append(
            (
                "同会话切换 source_id 被拒绝",
                conflict[0],
                conflict[1],
            )
        )
        context_after_conflict = coordinator.require("conversation-main")
        results.append(
            (
                "冲突后原上下文保持不变",
                context_after_conflict.source_id == "postgresql-main"
                and context_after_conflict.config is first_config,
                repr(context_after_conflict),
            )
        )

        selection_failure_bindings = ConversationDataSourceBindings()
        selection_failure = DataSourceRequestCoordinator(
            registry,
            selection_failure_bindings,
        )
        missing_source = _expect_error(
            lambda: selection_failure.resolve(
                "conversation-missing-source",
                {"message": "不参与数据源猜测"},
            ),
            ("缺少 source_id",),
        )
        results.append(
            (
                "缺失 metadata.source_id 不产生绑定",
                missing_source[0] and not selection_failure.bindings,
                missing_source[1],
            )
        )

        unknown_source = _expect_error(
            lambda: selection_failure.resolve(
                "conversation-unknown-source",
                {"source_id": "postgresql-unknown"},
            ),
            ("未知 source_id",),
        )
        results.append(
            (
                "未知 source_id 不产生绑定",
                unknown_source[0] and not selection_failure.bindings,
                unknown_source[1],
            )
        )

        invalid_conversation = _expect_error(
            lambda: selection_failure.resolve(
                None,  # type: ignore[arg-type]
                {"source_id": "postgresql-main"},
            ),
            ("conversation_id 必须显式提供",),
        )
        results.append(
            (
                "非法 conversation_id 不产生绑定",
                invalid_conversation[0] and not selection_failure.bindings,
                invalid_conversation[1],
            )
        )

        required = coordinator.require("conversation-main")
        results.append(
            (
                "require 已绑定会话成功",
                required.config is first_config
                and required.source_id == "postgresql-main",
                repr(required),
            )
        )

        require_missing = _expect_error(
            lambda: coordinator.require("conversation-missing"),
            ("conversation-missing", "尚未绑定"),
        )
        results.append(
            (
                "require 未绑定会话被拒绝",
                require_missing[0],
                require_missing[1],
            )
        )

        release_coordinator = DataSourceRequestCoordinator(registry)
        before_release = release_coordinator.resolve(
            "conversation-release",
            {"source_id": "postgresql-main"},
        )
        released = release_coordinator.release("conversation-release")
        results.append(
            (
                "release 返回释放前上下文",
                released.conversation_id == before_release.conversation_id
                and released.source_id == before_release.source_id
                and released.config is before_release.config
                and not release_coordinator.bindings,
                repr(released),
            )
        )

        rebound = release_coordinator.resolve(
            "conversation-release",
            {"source_id": "postgresql-archive"},
        )
        results.append(
            (
                "release 后允许改绑另一数据源",
                rebound.source_id == "postgresql-archive"
                and rebound.config is second_config,
                repr(rebound),
            )
        )

        release_missing = _expect_error(
            lambda: release_coordinator.release(
                "conversation-release-missing"
            ),
            ("conversation-release-missing", "尚未绑定"),
        )
        results.append(
            (
                "release 未绑定会话被拒绝",
                release_missing[0],
                release_missing[1],
            )
        )

        private_first = DataSourceRequestCoordinator(registry)
        private_second = DataSourceRequestCoordinator(registry)
        private_first.resolve(
            "conversation-private",
            {"source_id": "postgresql-main"},
        )
        private_second.resolve(
            "conversation-private",
            {"source_id": "postgresql-archive"},
        )
        results.append(
            (
                "两个编排器的私有 bindings 互不影响",
                private_first.require(
                    "conversation-private"
                ).source_id
                == "postgresql-main"
                and private_second.require(
                    "conversation-private"
                ).source_id
                == "postgresql-archive",
                (
                    f"first={tuple(private_first.bindings)}, "
                    f"second={tuple(private_second.bindings)}"
                ),
            )
        )

        shared_bindings = ConversationDataSourceBindings()
        shared_first = DataSourceRequestCoordinator(
            registry,
            shared_bindings,
        )
        shared_second = DataSourceRequestCoordinator(
            registry,
            shared_bindings,
        )
        shared_context = shared_first.resolve(
            "conversation-shared",
            {"source_id": "postgresql-main"},
        )
        shared_required = shared_second.require("conversation-shared")
        results.append(
            (
                "显式共享 bindings 时状态一致",
                shared_required.source_id == shared_context.source_id
                and shared_required.config is shared_context.config,
                repr(shared_required),
            )
        )

        binding_snapshot = coordinator.bindings
        try:
            binding_snapshot["other"] = next(
                iter(binding_snapshot.values())
            )  # type: ignore[index]
        except TypeError:
            snapshot_immutable = True
        else:
            snapshot_immutable = False
        results.append(
            (
                "bindings 快照只读且不暴露配置",
                snapshot_immutable
                and all(
                    isinstance(value, ConversationDataSourceBinding)
                    and not hasattr(value, "config")
                    for value in binding_snapshot.values()
                ),
                type(binding_snapshot).__name__,
            )
        )

        try:
            first_context.source_id = "postgresql-archive"
        except FrozenInstanceError:
            context_immutable = True
        else:
            context_immutable = False
        results.append(
            (
                "结果对象不可修改",
                context_immutable,
                type(first_context).__name__,
            )
        )

        repr_text = (
            repr(first_context)
            + repr(coordinator.bindings)
            + conflict[1]
        )
        results.append(
            (
                "repr 和异常不泄露密码或连接参数",
                TEST_PASSWORD not in repr_text
                and "connection_settings" not in repr_text
                and "offline.invalid" not in repr_text,
                repr(first_context),
            )
        )

        same_coordinator = DataSourceRequestCoordinator(registry)
        same_barrier = Barrier(12)

        def resolve_same_source() -> Any:
            same_barrier.wait()
            return same_coordinator.resolve(
                "conversation-concurrent-same",
                {"source_id": "postgresql-main"},
            )

        with ThreadPoolExecutor(max_workers=12) as executor:
            same_results = list(
                executor.map(lambda _: resolve_same_source(), range(12))
            )
        results.append(
            (
                "并发同源 resolve 幂等",
                all(
                    context.source_id == "postgresql-main"
                    and context.config is first_config
                    for context in same_results
                )
                and len(same_coordinator.bindings) == 1,
                f"results={len(same_results)}",
            )
        )

        cross_coordinator = DataSourceRequestCoordinator(registry)
        cross_barrier = Barrier(12)

        def resolve_cross_source(source_id: str) -> tuple[str, Any]:
            cross_barrier.wait()
            try:
                return (
                    "success",
                    cross_coordinator.resolve(
                        "conversation-concurrent-cross",
                        {"source_id": source_id},
                    ),
                )
            except ValueError as exc:
                return ("conflict", str(exc))

        cross_inputs = ["postgresql-main", "postgresql-archive"] * 6
        with ThreadPoolExecutor(max_workers=12) as executor:
            cross_results = list(
                executor.map(resolve_cross_source, cross_inputs)
            )
        cross_successes = [
            value for status, value in cross_results if status == "success"
        ]
        cross_conflicts = [
            value for status, value in cross_results if status == "conflict"
        ]
        cross_final = cross_coordinator.require(
            "conversation-concurrent-cross"
        )
        losing_source_id = (
            "postgresql-archive"
            if cross_final.source_id == "postgresql-main"
            else "postgresql-main"
        )
        results.append(
            (
                "并发跨源 resolve 只能一个源获胜",
                bool(cross_successes)
                and bool(cross_conflicts)
                and all(
                    context.source_id == cross_final.source_id
                    and context.config is cross_final.config
                    for context in cross_successes
                )
                and all(
                    cross_final.conversation_id in message
                    and cross_final.source_id in message
                    and losing_source_id in message
                    for message in cross_conflicts
                )
                and len(cross_coordinator.bindings) == 1,
                (
                    f"winner={cross_final.source_id}, "
                    f"success={len(cross_successes)}, "
                    f"conflict={len(cross_conflicts)}"
                ),
            )
        )

        environment_reads_before_operations = len(environment_reads)
        os.getenv = tracked_getenv
        try:
            environment_coordinator = DataSourceRequestCoordinator(registry)
            environment_coordinator.resolve(
                "conversation-environment",
                {"source_id": "postgresql-main"},
            )
            environment_coordinator.require("conversation-environment")
            environment_coordinator.release("conversation-environment")
        finally:
            os.getenv = original_getenv
        assets_absent = all(
            not path.exists()
            for config in (first_config, second_config)
            for path in (config.metadata_path, config.memory_path)
        )
        results.append(
            (
                "导入和执行不读取环境变量或正式资产",
                environment_reads_before_operations == 0
                and not environment_reads
                and assets_absent,
                f"reads={environment_reads}",
            )
        )

        forbidden_roots = ("vanna", "psycopg2", "chromadb")
        forbidden_modules = sorted(
            module_name
            for module_name in sys.modules
            if module_name == "backend.memory"
            or any(
                module_name == root_name
                or module_name.startswith(root_name + ".")
                for root_name in forbidden_roots
            )
        )
        results.append(
            (
                "不加载 Vanna、数据库、Chroma 或 Memory",
                not forbidden_modules,
                f"loaded={forbidden_modules}",
            )
        )

        module_path = (
            PROJECT_ROOT / "backend" / "data_source_request_coordinator.py"
        )
        module_source = module_path.read_text(encoding="utf-8")
        module_tree = ast.parse(module_source)
        imported_modules = {
            node.module or ""
            for node in ast.walk(module_tree)
            if isinstance(node, ast.ImportFrom)
        }
        allowed_imports = {
            "__future__",
            "collections.abc",
            "dataclasses",
            "typing",
            "config.data_source_config",
            "backend.data_source_registry",
            "backend.data_source_selection",
            "backend.conversation_data_source_binding",
        }
        results.append(
            (
                "编排器依赖边界符合要求",
                imported_modules <= allowed_imports
                and "config.data_sources" not in module_source
                and "config.settings" not in module_source,
                str(sorted(imported_modules)),
            )
        )

        mismatch_context = _expect_error(
            lambda: DataSourceRequestContext(
                conversation_id="conversation-mismatch",
                source_id="postgresql-main",
                config=second_config,
            ),
            ("source_id 与配置不一致",),
        )
        results.append(
            (
                "请求上下文拒绝 source_id 与配置不一致",
                mismatch_context[0],
                mismatch_context[1],
            )
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
