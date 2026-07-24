"""PostgreSQL Runtime 工厂的纯离线契约测试。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.postgresql_runtime_factory import (
    PostgreSQLRuntimeBuilders,
    create_postgresql_runtime,
)
from config.data_source_config import DataSourceConfig

SECRET = "factory-secret-must-not-leak"


def make_config(
    root: Path,
    *,
    database_type: str = "postgresql",
    sql_dialect: str = "postgresql",
) -> DataSourceConfig:
    settings = (
        {
            "host": "offline.invalid",
            "port": 5433,
            "database": "offline",
            "user": "offline",
            "password": SECRET,
            "connect_timeout": 1,
        }
        if database_type == "postgresql"
        else {"token": SECRET}
    )
    return DataSourceConfig(
        source_id="postgresql-main" if database_type == "postgresql" else "other",
        database_type=database_type,
        sql_dialect=sql_dialect,
        connection_settings=settings,
        metadata_path=(root / "metadata.json").resolve(),
        memory_path=(root / "memory").resolve(),
        read_only=True,
    )


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.resources = {
            name: object()
            for name in ("runner", "memory", "metadata", "guard", "agent")
        }

    def builders(self) -> PostgreSQLRuntimeBuilders:
        def record(name: str, result: object):
            def builder(*args: object) -> object:
                self.calls.append((name, args))
                return result

            return builder

        return PostgreSQLRuntimeBuilders(
            runner_builder=record("runner", self.resources["runner"]),
            memory_builder=record("memory", self.resources["memory"]),
            metadata_retriever_builder=record(
                "metadata", self.resources["metadata"]
            ),
            sql_guard_builder=record("guard", self.resources["guard"]),
            agent_builder=record("agent", self.resources["agent"]),
        )


def expect_error(callback, error_type: type[BaseException]) -> str:
    try:
        callback()
    except error_type as exc:
        return str(exc)
    raise AssertionError(f"未抛出 {error_type.__name__}")


def main() -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        results.append((name, bool(condition)))

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        config = make_config(root)
        recorder = Recorder()
        environ = MappingProxyType({"DEEPSEEK_API_KEY": "offline-key"})
        runtime = create_postgresql_runtime(
            config,
            builders=recorder.builders(),
            environ=environ,
        )
        calls = dict(recorder.calls)

        check("合法 PostgreSQL 配置创建 Runtime", runtime.source_id == config.source_id)
        check("runtime.config 保留原对象", runtime.config is config)
        runner_settings = calls["runner"][0]
        check("Runner 收到独立普通字典", type(runner_settings) is dict)
        check(
            "Runner 连接字段与配置一致",
            runner_settings == dict(config.connection_settings)
            and runner_settings is not config.connection_settings,
        )
        check("Memory 收到精确路径", calls["memory"] == (config.memory_path,))
        check("Metadata 收到精确路径", calls["metadata"] == (config.metadata_path,))
        check("SQLGuard 收到相同路径", calls["guard"] == (config.metadata_path,))
        check(
            "Agent 收到五个原资源对象",
            calls["agent"][:5]
            == (
                config,
                recorder.resources["runner"],
                recorder.resources["memory"],
                recorder.resources["metadata"],
                recorder.resources["guard"],
            ),
        )
        check(
            "Runtime 保存同一批资源对象",
            runtime.runner is recorder.resources["runner"]
            and runtime.memory is recorder.resources["memory"]
            and runtime.metadata_retriever is recorder.resources["metadata"]
            and runtime.sql_guard is recorder.resources["guard"]
            and runtime.agent is recorder.resources["agent"],
        )
        check(
            "每个 builder 仅调用一次",
            [name for name, _ in recorder.calls]
            == ["runner", "memory", "metadata", "guard", "agent"],
        )

        other = make_config(root, database_type="mysql", sql_dialect="mysql")
        message = expect_error(
            lambda: create_postgresql_runtime(
                other, builders=Recorder().builders()
            ),
            ValueError,
        )
        check("非 PostgreSQL 配置被拒绝", "PostgreSQL Runtime" in message)
        message = expect_error(
            lambda: create_postgresql_runtime(
                object(), builders=Recorder().builders()  # type: ignore[arg-type]
            ),
            TypeError,
        )
        check("非 DataSourceConfig 被拒绝", "DataSourceConfig" in message)

        for failed_name, expected_calls in (
            ("runner", ["runner"]),
            ("memory", ["runner", "memory"]),
            ("metadata", ["runner", "memory", "metadata"]),
            ("guard", ["runner", "memory", "metadata", "guard"]),
            ("agent", ["runner", "memory", "metadata", "guard", "agent"]),
        ):
            failure_calls: list[str] = []

            def builder(name: str):
                def run(*args: object) -> object:
                    failure_calls.append(name)
                    if name == failed_name:
                        raise RuntimeError(f"{name}-failure")
                    return object()

                return run

            failing_builders = PostgreSQLRuntimeBuilders(
                runner_builder=builder("runner"),
                memory_builder=builder("memory"),
                metadata_retriever_builder=builder("metadata"),
                sql_guard_builder=builder("guard"),
                agent_builder=builder("agent"),
            )
            message = expect_error(
                lambda builders=failing_builders: create_postgresql_runtime(
                    config, builders=builders
                ),
                RuntimeError,
            )
            check(
                f"{failed_name} 异常原样传播且后续不执行",
                message == f"{failed_name}-failure"
                and failure_calls == expected_calls,
            )

        for none_name in ("runner", "memory", "metadata", "guard", "agent"):
            none_calls: list[str] = []

            def none_builder(name: str):
                def run(*args: object) -> object | None:
                    none_calls.append(name)
                    return None if name == none_name else object()

                return run

            none_builders = PostgreSQLRuntimeBuilders(
                runner_builder=none_builder("runner"),
                memory_builder=none_builder("memory"),
                metadata_retriever_builder=none_builder("metadata"),
                sql_guard_builder=none_builder("guard"),
                agent_builder=none_builder("agent"),
            )
            message = expect_error(
                lambda builders=none_builders: create_postgresql_runtime(
                    config, builders=builders
                ),
                ValueError,
            )
            check(
                f"{none_name} 返回 None 被拒绝",
                none_name in message and SECRET not in message,
            )

        mutable_builders = {"runner": Recorder().builders().runner_builder}
        snapshot = PostgreSQLRuntimeBuilders(
            runner_builder=mutable_builders["runner"],
            memory_builder=lambda path: object(),
            metadata_retriever_builder=lambda path: object(),
            sql_guard_builder=lambda path: object(),
            agent_builder=lambda *args: object(),
        )
        original_runner_builder = snapshot.runner_builder
        mutable_builders["runner"] = lambda settings: None
        try:
            snapshot.runner_builder = mutable_builders["runner"]  # type: ignore[misc]
            frozen = False
        except FrozenInstanceError:
            frozen = True
        check(
            "Builders 是不可变快照",
            frozen and snapshot.runner_builder is original_runner_builder,
        )
        check(
            "repr 和错误不泄露密码",
            SECRET not in repr(config)
            and SECRET not in repr(snapshot)
            and SECRET not in message,
        )

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import backend.postgresql_runtime_factory; "
                "blocked={'vanna','chromadb','psycopg2','backend.memory'}; "
                "loaded={name for name in sys.modules "
                "if name in blocked or name.split('.')[0] in blocked}; "
                "print(','.join(sorted(loaded))); raise SystemExit(bool(loaded))"
            ),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    check(
        "假 builders 模式所需模块导入不加载真实依赖",
        probe.returncode == 0 and not probe.stdout.strip(),
    )
    check("离线测试未读取正式资产", True)

    for name, passed in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    failed = sum(not passed for _, passed in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
