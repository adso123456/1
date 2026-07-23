"""请求级数据源选择契约的纯离线测试。"""

from __future__ import annotations

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
from config.data_source_config import DataSourceConfig


TEST_PASSWORD = "selection-password-that-must-not-appear"
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
    expected_text: str,
    error_types: tuple[type[BaseException], ...] = (ValueError,),
) -> tuple[bool, str]:
    try:
        callback()
    except error_types as exc:
        message = str(exc)
        return expected_text in message and TEST_PASSWORD not in message, message
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
        selection_module = importlib.import_module("backend.data_source_selection")
    finally:
        os.getenv = original_getenv

    ResolvedDataSource = selection_module.ResolvedDataSource
    resolve_data_source = selection_module.resolve_data_source

    with tempfile.TemporaryDirectory(
        prefix="data-source-selection-test-"
    ) as temp_name:
        root = Path(temp_name).resolve()
        first = _make_config(root, "postgresql-main")
        second = _make_config(root, "postgresql-archive")
        single = DataSourceRegistry([first])
        multiple = DataSourceRegistry([first, second])

        selected = resolve_data_source(
            {"source_id": "postgresql-main"},
            single,
        )
        results.append(
            (
                "合法 metadata 显式选择成功",
                selected.source_id == "postgresql-main",
                repr(selected),
            )
        )
        results.append(
            (
                "返回 Registry 中同一个配置对象",
                selected.config is first,
                str(selected.config is first),
            )
        )

        selected_first = resolve_data_source(
            {"source_id": "postgresql-main"},
            multiple,
        )
        selected_second = resolve_data_source(
            {"source_id": "postgresql-archive"},
            multiple,
        )
        results.append(
            (
                "两个 source_id 分别解析",
                selected_first.config is first
                and selected_second.config is second,
                f"{selected_first.source_id}, {selected_second.source_id}",
            )
        )

        invalid_cases = (
            (
                "metadata 为 None 被拒绝",
                lambda: resolve_data_source(None, single),  # type: ignore[arg-type]
                "metadata 必须显式提供",
                (ValueError,),
            ),
            (
                "metadata 非 Mapping 被拒绝",
                lambda: resolve_data_source([], single),  # type: ignore[arg-type]
                "metadata 必须是 Mapping",
                (TypeError,),
            ),
            (
                "缺失 source_id 被拒绝",
                lambda: resolve_data_source({}, single),
                "缺少 source_id",
                (ValueError,),
            ),
            (
                "source_id 为 None 被拒绝",
                lambda: resolve_data_source({"source_id": None}, single),
                "source_id 必须显式提供",
                (ValueError,),
            ),
            (
                "source_id 非字符串被拒绝",
                lambda: resolve_data_source({"source_id": 1}, single),
                "source_id 必须是字符串",
                (TypeError,),
            ),
            (
                "空字符串被拒绝",
                lambda: resolve_data_source({"source_id": ""}, single),
                "source_id 必须是非空字符串",
                (ValueError,),
            ),
            (
                "空白字符串被拒绝",
                lambda: resolve_data_source({"source_id": "   "}, single),
                "source_id 必须是非空字符串",
                (ValueError,),
            ),
            (
                "未知 source_id 被拒绝",
                lambda: resolve_data_source(
                    {"source_id": "postgresql-unknown"},
                    single,
                ),
                "未知 source_id",
                (ValueError,),
            ),
            (
                "单数据源不允许缺省选择",
                lambda: resolve_data_source({"message": "任意问题"}, single),
                "缺少 source_id",
                (ValueError,),
            ),
            (
                "大小写不同不得自动归一化",
                lambda: resolve_data_source(
                    {"source_id": "PostgreSQL-main"},
                    single,
                ),
                "未知 source_id",
                (ValueError,),
            ),
            (
                "前后空白不得自动 strip 接受",
                lambda: resolve_data_source(
                    {"source_id": " postgresql-main "},
                    single,
                ),
                "未知 source_id",
                (ValueError,),
            ),
            (
                "其他字段不得参与猜测",
                lambda: resolve_data_source(
                    {
                        "message": "postgresql-main",
                        "conversation_id": "postgresql-main",
                    },
                    single,
                ),
                "缺少 source_id",
                (ValueError,),
            ),
        )
        for name, callback, expected_text, error_types in invalid_cases:
            passed, detail = _expect_error(
                callback,
                expected_text,
                error_types,
            )
            results.append((name, passed, detail))

        mutable_metadata = {"source_id": "postgresql-main"}
        stable_selection = resolve_data_source(mutable_metadata, single)
        mutable_metadata["source_id"] = "postgresql-archive"
        results.append(
            (
                "修改原始 metadata 不影响既有结果",
                stable_selection.source_id == "postgresql-main"
                and stable_selection.config is first,
                repr(stable_selection),
            )
        )

        try:
            stable_selection.source_id = "postgresql-archive"
        except FrozenInstanceError:
            result_immutable = True
        else:
            result_immutable = False
        results.append(
            (
                "结果对象不可重新赋值",
                result_immutable,
                type(stable_selection).__name__,
            )
        )

        repr_safe = (
            TEST_PASSWORD not in repr(stable_selection)
            and "connection_settings" not in repr(stable_selection)
        )
        unknown_error = _expect_error(
            lambda: resolve_data_source(
                {"source_id": "postgresql-unknown"},
                single,
            ),
            "未知 source_id",
        )
        results.append(
            (
                "repr 和异常不泄露密码",
                repr_safe and unknown_error[0],
                repr(stable_selection),
            )
        )

        environment_reads_before_resolve = len(environment_reads)
        os.getenv = tracked_getenv
        try:
            resolve_data_source({"source_id": "postgresql-main"}, single)
        finally:
            os.getenv = original_getenv
        no_environment_access = (
            environment_reads_before_resolve == 0
            and len(environment_reads) == 0
        )
        results.append(
            (
                "导入和解析不读取环境变量",
                no_environment_access,
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
            for config in (first, second)
            for path in (config.metadata_path, config.memory_path)
        )
        results.append(
            (
                "导入和解析不加载运行模块或访问资产",
                not forbidden_modules and assets_absent,
                f"loaded={forbidden_modules}",
            )
        )

        source_text = (
            PROJECT_ROOT / "backend" / "data_source_selection.py"
        ).read_text(encoding="utf-8")
        forbidden_import_text = (
            "build_current_data_source_registry",
            "build_postgresql_data_source_config",
            "config.data_sources",
            "backend.memory",
            "backend.metadata_retriever",
            "vanna",
            "chromadb",
            "psycopg2",
        )
        results.append(
            (
                "选择模块依赖边界符合要求",
                not any(
                    forbidden_text in source_text
                    for forbidden_text in forbidden_import_text
                ),
                "static import check",
            )
        )

        mismatch = _expect_error(
            lambda: ResolvedDataSource(
                source_id="postgresql-main",
                config=second,
            ),
            "source_id 与配置不一致",
        )
        results.append(
            (
                "选择结果拒绝 source_id 与配置不一致",
                mismatch[0],
                mismatch[1],
            )
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
