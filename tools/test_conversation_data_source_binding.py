"""会话—数据源绑定契约的纯离线测试。"""

from __future__ import annotations

import ast
import importlib
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_registry import DataSourceRegistry
from backend.data_source_selection import ResolvedDataSource
from config.data_source_config import DataSourceConfig


TEST_PASSWORD = "binding-password-that-must-not-appear"
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
        binding_module = importlib.import_module(
            "backend.conversation_data_source_binding"
        )
    finally:
        os.getenv = original_getenv

    ConversationDataSourceBinding = (
        binding_module.ConversationDataSourceBinding
    )
    ConversationDataSourceBindings = (
        binding_module.ConversationDataSourceBindings
    )

    with tempfile.TemporaryDirectory(
        prefix="conversation-binding-test-"
    ) as temp_name:
        root = Path(temp_name).resolve()
        first_config = _make_config(root, "postgresql-main")
        second_config = _make_config(root, "postgresql-archive")
        registry = DataSourceRegistry([first_config, second_config])
        first_resolved = ResolvedDataSource(
            source_id="postgresql-main",
            config=registry.require("postgresql-main"),
        )
        second_resolved = ResolvedDataSource(
            source_id="postgresql-archive",
            config=registry.require("postgresql-archive"),
        )

        bindings = ConversationDataSourceBindings()
        first_binding = bindings.bind("conversation-main", first_resolved)
        results.append(
            (
                "首次绑定成功",
                first_binding.conversation_id == "conversation-main"
                and first_binding.source_id == "postgresql-main",
                repr(first_binding),
            )
        )

        repeated = bindings.bind("conversation-main", first_resolved)
        results.append(
            (
                "同 source_id 重复绑定幂等",
                repeated == first_binding,
                repr(repeated),
            )
        )
        results.append(
            (
                "重复绑定返回同一个对象",
                repeated is first_binding,
                str(repeated is first_binding),
            )
        )

        conflict = _expect_error(
            lambda: bindings.bind("conversation-main", second_resolved),
            (
                "conversation-main",
                "postgresql-main",
                "postgresql-archive",
            ),
        )
        results.append(
            (
                "不同 source_id 切换被拒绝",
                conflict[0],
                conflict[1],
            )
        )
        results.append(
            (
                "冲突后原绑定不变",
                bindings.require("conversation-main") is first_binding,
                repr(bindings.require("conversation-main")),
            )
        )

        second_binding = bindings.bind(
            "conversation-archive",
            second_resolved,
        )
        results.append(
            (
                "两个 conversation_id 可绑定不同 source_id",
                first_binding.source_id == "postgresql-main"
                and second_binding.source_id == "postgresql-archive",
                repr(bindings),
            )
        )

        invalid_conversation_cases = (
            (
                "conversation_id 为 None 被拒绝",
                lambda: bindings.bind(None, first_resolved),  # type: ignore[arg-type]
                ("conversation_id 必须显式提供",),
                (ValueError,),
            ),
            (
                "conversation_id 非字符串被拒绝",
                lambda: bindings.bind(1, first_resolved),  # type: ignore[arg-type]
                ("conversation_id 必须是字符串",),
                (TypeError,),
            ),
            (
                "空字符串被拒绝",
                lambda: bindings.bind("", first_resolved),
                ("conversation_id 必须是非空字符串",),
                (ValueError,),
            ),
            (
                "空白字符串被拒绝",
                lambda: bindings.bind("   ", first_resolved),
                ("conversation_id 必须是非空字符串",),
                (ValueError,),
            ),
        )
        for name, callback, expected_texts, error_types in (
            invalid_conversation_cases
        ):
            passed, detail = _expect_error(
                callback,
                expected_texts,
                error_types,
            )
            results.append((name, passed, detail))

        identity_bindings = ConversationDataSourceBindings()
        exact = identity_bindings.bind("conversation-exact", first_resolved)
        padded = identity_bindings.bind(
            " conversation-exact ",
            second_resolved,
        )
        results.append(
            (
                "前后空白不得自动归一化",
                exact.conversation_id == "conversation-exact"
                and padded.conversation_id == " conversation-exact "
                and len(identity_bindings.bindings) == 2,
                str(tuple(identity_bindings.bindings)),
            )
        )

        lower = identity_bindings.bind("conversation-case", first_resolved)
        upper = identity_bindings.bind("Conversation-case", second_resolved)
        results.append(
            (
                "大小写不同视为不同 conversation_id",
                lower.conversation_id != upper.conversation_id
                and len(identity_bindings.bindings) == 4,
                str(tuple(identity_bindings.bindings)),
            )
        )

        results.append(
            (
                "require 已绑定会话成功",
                bindings.require("conversation-main") is first_binding,
                repr(bindings.require("conversation-main")),
            )
        )

        require_missing = _expect_error(
            lambda: bindings.require("conversation-missing"),
            ("conversation-missing", "尚未绑定"),
        )
        results.append(
            (
                "require 未绑定会话被拒绝",
                require_missing[0],
                require_missing[1],
            )
        )

        release_bindings = ConversationDataSourceBindings()
        released_original = release_bindings.bind(
            "conversation-release",
            first_resolved,
        )
        released = release_bindings.release("conversation-release")
        results.append(
            (
                "release 已绑定会话成功",
                released is released_original
                and not release_bindings.bindings,
                repr(released),
            )
        )

        release_missing = _expect_error(
            lambda: release_bindings.release("conversation-release"),
            ("conversation-release", "尚未绑定"),
        )
        results.append(
            (
                "release 未绑定会话被拒绝",
                release_missing[0],
                release_missing[1],
            )
        )

        rebound = release_bindings.bind(
            "conversation-release",
            second_resolved,
        )
        results.append(
            (
                "release 后允许改绑其他 source_id",
                rebound.source_id == "postgresql-archive",
                repr(rebound),
            )
        )

        mapping_snapshot = bindings.bindings
        try:
            mapping_snapshot["other"] = first_binding  # type: ignore[index]
        except TypeError:
            mapping_immutable = True
        else:
            mapping_immutable = False
        results.append(
            (
                "内部映射不可修改",
                mapping_immutable,
                type(mapping_snapshot).__name__,
            )
        )

        external_container = {"resolved": first_resolved}
        external_bindings = ConversationDataSourceBindings()
        external_binding = external_bindings.bind(
            "conversation-external",
            external_container["resolved"],
        )
        external_snapshot = external_bindings.bindings
        external_container["resolved"] = second_resolved
        external_bindings.bind("conversation-later", second_resolved)
        results.append(
            (
                "修改外部容器和后续状态不影响既有结果或快照",
                external_binding.source_id == "postgresql-main"
                and "conversation-later" not in external_snapshot,
                repr(external_binding),
            )
        )

        ordered_bindings = ConversationDataSourceBindings()
        ordered_bindings.bind("conversation-z", first_resolved)
        ordered_bindings.bind("conversation-a", second_resolved)
        results.append(
            (
                "bindings 输出顺序确定且不暴露配置",
                tuple(ordered_bindings.bindings)
                == ("conversation-a", "conversation-z")
                and all(
                    isinstance(value, ConversationDataSourceBinding)
                    for value in ordered_bindings.bindings.values()
                ),
                str(tuple(ordered_bindings.bindings)),
            )
        )

        try:
            first_binding.source_id = "postgresql-archive"
        except FrozenInstanceError:
            binding_immutable = True
        else:
            binding_immutable = False
        results.append(
            (
                "绑定结果不可重新赋值",
                binding_immutable,
                type(first_binding).__name__,
            )
        )

        repr_text = (
            repr(first_binding)
            + repr(bindings)
            + repr(bindings.bindings)
        )
        results.append(
            (
                "repr 和异常不泄露密码或连接配置",
                TEST_PASSWORD not in repr_text
                and "connection_settings" not in repr_text
                and TEST_PASSWORD not in conflict[1],
                repr(first_binding),
            )
        )

        environment_reads_before_operations = len(environment_reads)
        os.getenv = tracked_getenv
        try:
            environment_bindings = ConversationDataSourceBindings()
            environment_bindings.bind(
                "conversation-environment",
                first_resolved,
            )
            environment_bindings.require("conversation-environment")
            environment_bindings.release("conversation-environment")
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
        assets_absent = all(
            not path.exists()
            for config in (first_config, second_config)
            for path in (config.metadata_path, config.memory_path)
        )
        results.append(
            (
                "不加载运行模块或访问正式资产",
                not forbidden_modules and assets_absent,
                f"loaded={forbidden_modules}",
            )
        )

        module_path = (
            PROJECT_ROOT
            / "backend"
            / "conversation_data_source_binding.py"
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
            "types",
            "typing",
            "backend.data_source_selection",
        }
        results.append(
            (
                "被测模块仅依赖标准库和数据源选择契约",
                imported_modules <= allowed_imports,
                str(sorted(imported_modules)),
            )
        )

        forbidden_builder_names = (
            "build_current_data_source_registry",
            "build_postgresql_data_source_config",
            "DataSourceRegistry",
            "config.data_sources",
        )
        results.append(
            (
                "不调用 Registry 或 PostgreSQL 配置构建入口",
                not any(
                    forbidden_name in module_source
                    for forbidden_name in forbidden_builder_names
                ),
                "static source check",
            )
        )

        invalid_resolved = _expect_error(
            lambda: bindings.bind(
                "conversation-invalid-resolved",
                object(),  # type: ignore[arg-type]
            ),
            ("resolved_data_source", "ResolvedDataSource"),
            (TypeError,),
        )
        results.append(
            (
                "bind 拒绝非 ResolvedDataSource",
                invalid_resolved[0],
                invalid_resolved[1],
            )
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
