"""DataSourceRuntimeManager 的纯离线与并发契约测试。"""

from __future__ import annotations

import ast
import importlib
import os
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_registry import DataSourceRegistry
from config.data_source_config import DataSourceConfig


TEST_PASSWORD = "runtime-manager-password-that-must-not-appear"
RESOURCE_REPR = "RUNTIME_MANAGER_RESOURCE_REPR_MUST_NOT_APPEAR"


class FakeResource:
    def __init__(self, source_id: str, kind: str) -> None:
        self.source_id = source_id
        self.kind = kind

    def __repr__(self) -> str:
        return f"{RESOURCE_REPR}:{self.source_id}:{self.kind}"


def _make_config(
    root: Path,
    source_id: str,
    database_type: str,
) -> DataSourceConfig:
    return DataSourceConfig(
        source_id=source_id,
        database_type=database_type,
        sql_dialect=f"{database_type}-dialect",
        connection_settings={
            "password": TEST_PASSWORD,
            "source": source_id,
        },
        metadata_path=root / f"{source_id}-metadata-does-not-exist.json",
        memory_path=root / f"{source_id}-memory-does-not-exist",
        read_only=True,
    )


def _make_runtime(runtime_class: type, config: DataSourceConfig) -> Any:
    return runtime_class(
        config=config,
        runner=FakeResource(config.source_id, "runner"),
        memory=FakeResource(config.source_id, "memory"),
        metadata_retriever=FakeResource(config.source_id, "metadata"),
        sql_guard=FakeResource(config.source_id, "sql_guard"),
        agent=FakeResource(config.source_id, "agent"),
    )


class RecordingFactory:
    def __init__(self, runtime_class: type) -> None:
        self.runtime_class = runtime_class
        self.call_count = 0
        self.configs: list[DataSourceConfig] = []
        self._lock = Lock()

    def __call__(self, config: DataSourceConfig) -> Any:
        with self._lock:
            self.call_count += 1
            self.configs.append(config)
        return _make_runtime(self.runtime_class, config)


def _expect_error(
    callback: Callable[[], Any],
    expected_text: str,
    error_types: tuple[type[BaseException], ...] = (ValueError,),
) -> tuple[bool, str]:
    try:
        callback()
    except error_types as exc:
        message = str(exc)
        return (
            expected_text in message
            and TEST_PASSWORD not in message
            and RESOURCE_REPR not in message,
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
        runtime_module = importlib.import_module("backend.data_source_runtime")
        manager_module = importlib.import_module(
            "backend.data_source_runtime_manager"
        )
    finally:
        os.getenv = original_getenv

    DataSourceRuntime = runtime_module.DataSourceRuntime
    DataSourceRuntimeManager = manager_module.DataSourceRuntimeManager

    with tempfile.TemporaryDirectory(
        prefix="data-source-runtime-manager-test-"
    ) as temp_name:
        root = Path(temp_name).resolve()
        first = _make_config(
            root,
            "runtime-main",
            "test-postgresql",
        )
        second = _make_config(
            root,
            "runtime-archive",
            "test-postgresql",
        )
        secondary = _make_config(
            root,
            "runtime-secondary",
            "test-secondary",
        )

        first_factory = RecordingFactory(DataSourceRuntime)
        single = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            {"test-postgresql": first_factory},
        )
        first_runtime = single.require("runtime-main")
        results.append(
            (
                "单数据源首次创建成功",
                first_runtime.source_id == "runtime-main"
                and len(single.runtimes) == 1,
                repr(first_runtime),
            )
        )

        repeated_runtime = single.require("runtime-main")
        results.append(
            (
                "同 source_id 重复 require 返回同一 runtime",
                repeated_runtime is first_runtime
                and first_factory.call_count == 1,
                f"calls={first_factory.call_count}",
            )
        )
        results.append(
            (
                "工厂收到 Registry 原始配置对象",
                first_factory.configs == [first]
                and first_factory.configs[0] is first,
                str(first_factory.configs[0] is first),
            )
        )

        same_type_factory = RecordingFactory(DataSourceRuntime)
        same_type_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, second]),
            {"test-postgresql": same_type_factory},
        )
        same_first = same_type_manager.require("runtime-main")
        same_second = same_type_manager.require("runtime-archive")
        results.append(
            (
                "两个 source_id 创建两个不同 runtime",
                same_first is not same_second
                and len(same_type_manager.runtimes) == 2,
                str(tuple(same_type_manager.runtimes)),
            )
        )
        results.append(
            (
                "同 database_type 的数据源仍拥有不同资源",
                all(
                    getattr(same_first, field_name)
                    is not getattr(same_second, field_name)
                    for field_name in (
                        "runner",
                        "memory",
                        "metadata_retriever",
                        "sql_guard",
                        "agent",
                    )
                ),
                f"calls={same_type_factory.call_count}",
            )
        )

        primary_factory = RecordingFactory(DataSourceRuntime)
        secondary_factory = RecordingFactory(DataSourceRuntime)
        mixed_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, secondary]),
            {
                "test-postgresql": primary_factory,
                "test-secondary": secondary_factory,
            },
        )
        mixed_first = mixed_manager.require("runtime-main")
        mixed_secondary = mixed_manager.require("runtime-secondary")
        results.append(
            (
                "不同 database_type 精确调用对应工厂",
                primary_factory.configs == [first]
                and secondary_factory.configs == [secondary]
                and mixed_first.database_type == "test-postgresql"
                and mixed_secondary.database_type == "test-secondary",
                (
                    f"primary={primary_factory.call_count}, "
                    f"secondary={secondary_factory.call_count}"
                ),
            )
        )

        unknown = _expect_error(
            lambda: single.require("runtime-unknown"),
            "未知 source_id",
        )
        results.append(("未知 source_id 被拒绝", *unknown))

        invalid_source_cases = (
            (
                "None source_id 被拒绝",
                lambda: single.require(None),  # type: ignore[arg-type]
            ),
            (
                "非字符串 source_id 被拒绝",
                lambda: single.require(1),  # type: ignore[arg-type]
            ),
            ("空 source_id 被拒绝", lambda: single.require("")),
            ("空白 source_id 被拒绝", lambda: single.require("   ")),
        )
        for name, callback in invalid_source_cases:
            invalid_source = _expect_error(
                callback,
                "source_id",
                (TypeError, ValueError),
            )
            results.append((name, *invalid_source))

        no_default = _expect_error(
            lambda: single.require(),  # type: ignore[call-arg]
            "required positional argument",
            (TypeError,),
        )
        results.append(("单数据源不允许缺省选择", *no_default))

        mutable_factory = RecordingFactory(DataSourceRuntime)
        factory_mapping = {"test-postgresql": mutable_factory}
        snapshot_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            factory_mapping,
        )
        factory_mapping.clear()
        snapshot_runtime = snapshot_manager.require("runtime-main")
        results.append(
            (
                "工厂映射不受外部修改影响",
                snapshot_runtime.config is first
                and mutable_factory.call_count == 1,
                repr(snapshot_manager),
            )
        )

        empty_factories = _expect_error(
            lambda: DataSourceRuntimeManager(
                DataSourceRegistry([first]),
                {},
            ),
            "至少需要一个",
        )
        results.append(("空 factories 被拒绝", *empty_factories))

        missing_factory = _expect_error(
            lambda: DataSourceRuntimeManager(
                DataSourceRegistry([first, secondary]),
                {"test-postgresql": RecordingFactory(DataSourceRuntime)},
            ),
            "test-secondary",
        )
        results.append(
            ("缺少 Registry 所需 database_type 工厂被拒绝", *missing_factory)
        )

        non_mapping = _expect_error(
            lambda: DataSourceRuntimeManager(  # type: ignore[arg-type]
                DataSourceRegistry([first]),
                [],
            ),
            "factories 必须是 Mapping",
            (TypeError,),
        )
        results.append(("非 Mapping factories 被拒绝", *non_mapping))

        non_string_key = _expect_error(
            lambda: DataSourceRuntimeManager(  # type: ignore[dict-item]
                DataSourceRegistry([first]),
                {1: RecordingFactory(DataSourceRuntime)},
            ),
            "factory key 必须是字符串",
            (TypeError,),
        )
        results.append(("非字符串 factory key 被拒绝", *non_string_key))

        blank_key = _expect_error(
            lambda: DataSourceRuntimeManager(
                DataSourceRegistry([first]),
                {"   ": RecordingFactory(DataSourceRuntime)},
            ),
            "factory key 必须是非空字符串",
        )
        results.append(("空白 factory key 被拒绝", *blank_key))

        non_callable = _expect_error(
            lambda: DataSourceRuntimeManager(  # type: ignore[dict-item]
                DataSourceRegistry([first]),
                {"test-postgresql": object()},
            ),
            "factory 必须可调用",
            (TypeError,),
        )
        results.append(("不可调用 factory 被拒绝", *non_callable))

        non_runtime_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            {"test-postgresql": lambda config: object()},
        )
        non_runtime = _expect_error(
            lambda: non_runtime_manager.require("runtime-main"),
            "必须返回 DataSourceRuntime",
            (TypeError,),
        )
        results.append(("factory 返回非 DataSourceRuntime 被拒绝", *non_runtime))

        other_source_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            {
                "test-postgresql": lambda config: _make_runtime(
                    DataSourceRuntime,
                    second,
                )
            },
        )
        other_source = _expect_error(
            lambda: other_source_manager.require("runtime-main"),
            "Registry 原始配置对象",
        )
        results.append(
            ("factory 返回其他 source_id runtime 被拒绝", *other_source)
        )

        copied_first = DataSourceConfig(
            source_id=first.source_id,
            database_type=first.database_type,
            sql_dialect=first.sql_dialect,
            connection_settings=first.connection_settings,
            metadata_path=first.metadata_path,
            memory_path=first.memory_path,
            read_only=first.read_only,
        )
        copied_config_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            {
                "test-postgresql": lambda config: _make_runtime(
                    DataSourceRuntime,
                    copied_first,
                )
            },
        )
        copied_config = _expect_error(
            lambda: copied_config_manager.require("runtime-main"),
            "Registry 原始配置对象",
        )
        results.append(
            ("factory 使用复制配置被拒绝", *copied_config)
        )

        failed_managers = (
            non_runtime_manager,
            other_source_manager,
            copied_config_manager,
        )
        results.append(
            (
                "所有校验失败结果均不进入缓存",
                all(not manager.runtimes for manager in failed_managers),
                str([len(manager.runtimes) for manager in failed_managers]),
            )
        )

        retry_state = {"calls": 0}

        def retry_factory(config: DataSourceConfig) -> Any:
            retry_state["calls"] += 1
            if retry_state["calls"] == 1:
                raise RuntimeError("offline factory first failure")
            return _make_runtime(DataSourceRuntime, config)

        retry_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            {"test-postgresql": retry_factory},
        )
        first_failure = _expect_error(
            lambda: retry_manager.require("runtime-main"),
            "offline factory first failure",
            (RuntimeError,),
        )
        results.append(
            (
                "工厂首次异常时缓存保持为空",
                first_failure[0] and not retry_manager.runtimes,
                first_failure[1],
            )
        )
        retry_runtime = retry_manager.require("runtime-main")
        results.append(
            (
                "工厂失败后允许重试成功",
                retry_state["calls"] == 2
                and retry_runtime.config is first
                and retry_manager.runtimes["runtime-main"] is retry_runtime,
                f"calls={retry_state['calls']}",
            )
        )

        def always_fail(config: DataSourceConfig) -> Any:
            raise RuntimeError("offline isolated failure")

        isolated_success_factory = RecordingFactory(DataSourceRuntime)
        isolated_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, secondary]),
            {
                "test-postgresql": always_fail,
                "test-secondary": isolated_success_factory,
            },
        )
        isolated_failure = _expect_error(
            lambda: isolated_manager.require("runtime-main"),
            "offline isolated failure",
            (RuntimeError,),
        )
        isolated_success = isolated_manager.require("runtime-secondary")
        results.append(
            (
                "失败源不影响其他数据源",
                isolated_failure[0]
                and isolated_success.config is secondary
                and "runtime-main" not in isolated_manager.runtimes
                and "runtime-secondary" in isolated_manager.runtimes,
                repr(isolated_manager.runtimes),
            )
        )

        safe_errors = " ".join(
            detail
            for passed, detail in (
                unknown,
                missing_factory,
                non_runtime,
                other_source,
                copied_config,
                first_failure,
                isolated_failure,
            )
        )
        results.append(
            (
                "错误信息不包含密码或资源 repr",
                TEST_PASSWORD not in safe_errors
                and RESOURCE_REPR not in safe_errors,
                "redacted",
            )
        )

        isolated_factory = RecordingFactory(DataSourceRuntime)
        resource_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, second]),
            {"test-postgresql": isolated_factory},
        )
        runtime_a = resource_manager.require("runtime-main")
        runtime_b = resource_manager.require("runtime-archive")
        resource_fields = (
            "runner",
            "memory",
            "metadata_retriever",
            "sql_guard",
            "agent",
        )
        results.append(
            (
                "五类资源对象均不共享",
                all(
                    getattr(runtime_a, field_name)
                    is not getattr(runtime_b, field_name)
                    for field_name in resource_fields
                ),
                str(resource_fields),
            )
        )
        resources_a = {
            getattr(runtime_a, field_name) for field_name in resource_fields
        }
        resources_b = {
            getattr(runtime_b, field_name) for field_name in resource_fields
        }
        results.append(
            (
                "Runtime A 不包含 Runtime B 资源",
                resources_a.isdisjoint(resources_b),
                f"A={len(resources_a)}, B={len(resources_b)}",
            )
        )
        results.append(
            (
                "Runtime B 不包含 Runtime A 资源",
                resources_b.isdisjoint(resources_a),
                f"B={len(resources_b)}, A={len(resources_a)}",
            )
        )
        results.append(
            (
                "两个配置 Metadata 路径独立",
                runtime_a.config.metadata_path
                != runtime_b.config.metadata_path,
                (
                    f"{runtime_a.config.metadata_path.name}, "
                    f"{runtime_b.config.metadata_path.name}"
                ),
            )
        )
        results.append(
            (
                "两个配置 Memory 路径独立",
                runtime_a.config.memory_path != runtime_b.config.memory_path,
                (
                    f"{runtime_a.config.memory_path.name}, "
                    f"{runtime_b.config.memory_path.name}"
                ),
            )
        )
        results.append(
            (
                "管理器没有修改原始 DataSourceConfig",
                runtime_a.config is first
                and runtime_b.config is second
                and first.source_id == "runtime-main"
                and second.source_id == "runtime-archive",
                "identity preserved",
            )
        )

        concurrent_factory = RecordingFactory(DataSourceRuntime)
        concurrent_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first]),
            {"test-postgresql": concurrent_factory},
        )
        caller_barrier = Barrier(20)

        def concurrent_same_source() -> Any:
            caller_barrier.wait()
            return concurrent_manager.require("runtime-main")

        with ThreadPoolExecutor(max_workers=20) as executor:
            concurrent_results = list(
                executor.map(lambda _: concurrent_same_source(), range(20))
            )
        results.append(
            (
                "20 线程同源只创建一次",
                concurrent_factory.call_count == 1
                and all(
                    runtime is concurrent_results[0]
                    for runtime in concurrent_results
                )
                and len(concurrent_manager.runtimes) == 1,
                (
                    f"calls={concurrent_factory.call_count}, "
                    f"results={len(concurrent_results)}"
                ),
            )
        )

        parallel_barrier = Barrier(2)
        parallel_calls: dict[str, int] = {}
        parallel_lock = Lock()

        def parallel_factory(config: DataSourceConfig) -> Any:
            with parallel_lock:
                parallel_calls[config.source_id] = (
                    parallel_calls.get(config.source_id, 0) + 1
                )
            parallel_barrier.wait(timeout=5)
            return _make_runtime(DataSourceRuntime, config)

        parallel_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, second]),
            {"test-postgresql": parallel_factory},
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            parallel_results = list(
                executor.map(
                    parallel_manager.require,
                    ("runtime-main", "runtime-archive"),
                )
            )
        results.append(
            (
                "不同 source_id 可同时进入工厂构建",
                parallel_calls == {
                    "runtime-main": 1,
                    "runtime-archive": 1,
                }
                and {runtime.source_id for runtime in parallel_results}
                == {"runtime-main", "runtime-archive"}
                and len(parallel_manager.runtimes) == 2,
                str(parallel_calls),
            )
        )

        failure_barrier = Barrier(2)

        def concurrent_selective_factory(
            config: DataSourceConfig,
        ) -> Any:
            failure_barrier.wait(timeout=5)
            if config.source_id == "runtime-main":
                raise RuntimeError("offline concurrent failure")
            return _make_runtime(DataSourceRuntime, config)

        concurrent_failure_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, second]),
            {"test-postgresql": concurrent_selective_factory},
        )

        def require_with_status(source_id: str) -> tuple[str, Any]:
            try:
                return (
                    "success",
                    concurrent_failure_manager.require(source_id),
                )
            except RuntimeError as exc:
                return ("failure", str(exc))

        with ThreadPoolExecutor(max_workers=2) as executor:
            failure_results = list(
                executor.map(
                    require_with_status,
                    ("runtime-main", "runtime-archive"),
                )
            )
        success_values = [
            value for status, value in failure_results if status == "success"
        ]
        failure_values = [
            value for status, value in failure_results if status == "failure"
        ]
        results.append(
            (
                "一个源并发失败不影响另一源且无半成品",
                len(success_values) == 1
                and success_values[0].source_id == "runtime-archive"
                and failure_values == ["offline concurrent failure"]
                and tuple(concurrent_failure_manager.runtimes)
                == ("runtime-archive",),
                str(
                    [
                        (status, getattr(value, "source_id", value))
                        for status, value in failure_results
                    ]
                ),
            )
        )

        snapshot_test_manager = DataSourceRuntimeManager(
            DataSourceRegistry([first, second]),
            {"test-postgresql": RecordingFactory(DataSourceRuntime)},
        )
        old_snapshot = snapshot_test_manager.runtimes
        snapshot_test_manager.require("runtime-main")
        new_snapshot = snapshot_test_manager.runtimes
        try:
            new_snapshot["runtime-archive"] = runtime_b  # type: ignore[index]
        except TypeError:
            snapshot_read_only = True
        else:
            snapshot_read_only = False
        results.append(
            (
                "runtimes 快照不可修改",
                snapshot_read_only,
                type(new_snapshot).__name__,
            )
        )
        results.append(
            (
                "旧快照不随后续缓存变化",
                not old_snapshot
                and tuple(new_snapshot) == ("runtime-main",),
                f"old={tuple(old_snapshot)}, new={tuple(new_snapshot)}",
            )
        )
        results.append(
            (
                "source_ids 顺序确定",
                snapshot_test_manager.source_ids
                == ("runtime-archive", "runtime-main"),
                str(snapshot_test_manager.source_ids),
            )
        )
        results.append(
            (
                "database_types 顺序确定",
                mixed_manager.database_types
                == ("test-postgresql", "test-secondary"),
                str(mixed_manager.database_types),
            )
        )

        manager_repr = repr(mixed_manager)
        results.append(
            (
                "manager repr 仅显示安全身份",
                "runtime-main" in manager_repr
                and "runtime-secondary" in manager_repr
                and "test-postgresql" in manager_repr
                and "test-secondary" in manager_repr
                and TEST_PASSWORD not in manager_repr
                and RESOURCE_REPR not in manager_repr
                and "factory" not in manager_repr.lower()
                and str(first.metadata_path) not in manager_repr
                and str(first.memory_path) not in manager_repr,
                manager_repr,
            )
        )

        environment_reads_before_operations = len(environment_reads)
        os.getenv = tracked_getenv
        try:
            environment_manager = DataSourceRuntimeManager(
                DataSourceRegistry([first]),
                {
                    "test-postgresql": RecordingFactory(
                        DataSourceRuntime
                    )
                },
            )
            environment_manager.require("runtime-main")
        finally:
            os.getenv = original_getenv
        results.append(
            (
                "导入和执行不读取环境变量",
                environment_reads_before_operations == 0
                and not environment_reads,
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
                "不加载真实运行资源模块",
                not forbidden_modules,
                f"loaded={forbidden_modules}",
            )
        )

        assets_absent = all(
            not path.exists()
            for config in (first, second, secondary)
            for path in (config.metadata_path, config.memory_path)
        )
        results.append(
            (
                "测试未读取或创建正式运行资产",
                assets_absent,
                "temporary paths remain absent",
            )
        )

        module_path = (
            PROJECT_ROOT / "backend" / "data_source_runtime_manager.py"
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
            "threading",
            "types",
            "typing",
            "backend.data_source_registry",
            "backend.data_source_runtime",
        }
        results.append(
            (
                "RuntimeManager 依赖边界符合要求",
                imported_modules <= allowed_imports
                and "config.data_sources" not in module_source
                and "config.settings" not in module_source,
                str(sorted(imported_modules)),
            )
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
