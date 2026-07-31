"""统一 Runtime/Agent 工厂（R1）的等价性测试。

验证共享 runtime_factory.assemble_runtime 与 agent_assembly.build_shared_agent
在 PostgreSQL / MySQL 上保持重构前的构建顺序、单次构建、对象同一性、
专属 Runner/Guard/方言、配置校验和失败语义。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent_assembly import build_shared_agent
from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.metadata_context_enhancer import (
    DeterministicMetadataContextEnhancer,
)
from backend.mysql_runner import ReadOnlyMySQLRunner
from backend.mysql_runtime_factory import (
    MySQLRuntimeBuilders,
    _create_mysql_agent,
    _load_default_builders as _load_mysql_builders,
    create_mysql_runtime,
)
from backend.mysql_sql_guard import MySQLSQLGuard
from backend.postgresql_runtime_factory import (
    PostgreSQLRuntimeBuilders,
    _create_postgresql_agent,
    _load_default_builders as _load_postgresql_builders,
    create_postgresql_runtime,
)
from backend.query_context import (
    OriginalQuestionContextEnricher,
    OriginalQuestionLifecycleHook,
)
from backend.runtime_factory import RuntimeBuilders, assemble_runtime
from backend.schema_preserving_sql import (
    SchemaPreservingPostgresRunner,
    SchemaPreservingRunSqlTool,
)
from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
from backend.sql_guard import SQLGuard
from backend.tracing_llm_service import TracingOpenAILlmService
from config.data_source_config import DataSourceConfig

import config.settings as settings_module

OFFLINE_ENV = {"DEEPSEEK_API_KEY": "offline-key"}


def make_config(
    root: Path,
    *,
    database_type: str,
    sql_dialect: str,
    read_only: bool = True,
    user: str = "offline",
) -> DataSourceConfig:
    settings = {
        "host": "offline.invalid",
        "port": 5433 if database_type == "postgresql" else 3307,
        "database": "offline",
        "user": user,
        "password": "offline-secret",
        "connect_timeout": 1,
    }
    if database_type == "mysql":
        settings["charset"] = "utf8mb4"
    return DataSourceConfig(
        source_id=f"{database_type}-test",
        database_type=database_type,
        sql_dialect=sql_dialect,
        connection_settings=settings,
        metadata_path=(root / "metadata.json").resolve(),
        memory_path=(root / "memory").resolve(),
        read_only=read_only,
    )


def make_raw_config(
    *,
    database_type: str,
    sql_dialect: str,
    read_only: bool = True,
) -> DataSourceConfig:
    """绕过 DataSourceConfig.__post_init__ 构造非法配置，验证工厂防御检查。"""
    config = DataSourceConfig.__new__(DataSourceConfig)
    object.__setattr__(config, "source_id", "raw-test")
    object.__setattr__(config, "database_type", database_type)
    object.__setattr__(config, "sql_dialect", sql_dialect)
    object.__setattr__(
        config,
        "connection_settings",
        {
            "host": "offline.invalid",
            "port": 5433,
            "database": "offline",
            "user": "offline",
            "password": "offline-secret",
            "connect_timeout": 1,
        },
    )
    object.__setattr__(config, "metadata_path", Path("C:/offline/metadata.json"))
    object.__setattr__(config, "memory_path", Path("C:/offline/memory"))
    object.__setattr__(config, "read_only", read_only)
    return config


def write_metadata(root: Path) -> None:
    (root / "metadata.json").write_text(
        json.dumps(
            [
                {"table": "sample_table", "column": "id"},
                {"table": "sample_table", "column": "name"},
            ]
        ),
        encoding="utf-8",
    )


class Recorder:
    """记录 builder 调用顺序与返回对象。"""

    def __init__(self, builders_type: type = RuntimeBuilders) -> None:
        self.builders_type = builders_type
        self.calls: list[tuple[str, object]] = []
        self.resources = {
            name: object()
            for name in ("runner", "memory", "metadata", "guard", "agent")
        }

    def builders(self):
        def record(name: str, result: object):
            def builder(*args: object) -> object:
                self.calls.append((name, args))
                return result

            return builder

        return self.builders_type(
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

    with tempfile.TemporaryDirectory(prefix="runtime-factory-unified-") as temp_name:
        root = Path(temp_name).resolve()
        write_metadata(root)
        pg_config = make_config(root, database_type="postgresql", sql_dialect="postgresql")
        mysql_config = make_config(root, database_type="mysql", sql_dialect="mysql")

        # ── Builders 兼容性 ──
        check(
            "PostgreSQLRuntimeBuilders 是 RuntimeBuilders 子类",
            issubclass(PostgreSQLRuntimeBuilders, RuntimeBuilders),
        )
        check(
            "MySQLRuntimeBuilders 是 RuntimeBuilders 子类",
            issubclass(MySQLRuntimeBuilders, RuntimeBuilders),
        )

        # ── 构建顺序 / 单次构建 / 同一性（共享 assemble_runtime）──
        for name, factory, config, builders_type in (
            (
                "PostgreSQL",
                create_postgresql_runtime,
                pg_config,
                PostgreSQLRuntimeBuilders,
            ),
            ("MySQL", create_mysql_runtime, mysql_config, MySQLRuntimeBuilders),
        ):
            recorder = Recorder(builders_type)
            runtime = factory(config, builders=recorder.builders(), environ=OFFLINE_ENV)
            check(
                f"{name} 构建顺序为 runner→memory→metadata→guard→agent 且各一次",
                [call_name for call_name, _ in recorder.calls]
                == ["runner", "memory", "metadata", "guard", "agent"],
            )
            agent_args = recorder.calls[-1][1]
            check(
                f"{name} Agent 收到与原 Runtime 相同的四资源对象",
                runtime.runner is recorder.resources["runner"]
                and runtime.memory is recorder.resources["memory"]
                and runtime.metadata_retriever is recorder.resources["metadata"]
                and runtime.sql_guard is recorder.resources["guard"]
                and runtime.agent is recorder.resources["agent"]
                and agent_args[1:5]
                == (
                    recorder.resources["runner"],
                    recorder.resources["memory"],
                    recorder.resources["metadata"],
                    recorder.resources["guard"],
                ),
            )

        # 共享 assemble_runtime 直接注入基类 RuntimeBuilders
        shared_recorder = Recorder(RuntimeBuilders)
        shared_runtime = assemble_runtime(
            pg_config, shared_recorder.builders(), environ=OFFLINE_ENV
        )
        check(
            "assemble_runtime 接受基类 RuntimeBuilders 且顺序一致",
            [call_name for call_name, _ in shared_recorder.calls]
            == ["runner", "memory", "metadata", "guard", "agent"]
            and shared_runtime.agent is shared_recorder.resources["agent"],
        )

        # ── PostgreSQL 专属 Runner / Guard / 默认方言 ──
        pg_builders = _load_postgresql_builders()
        pg_runner = pg_builders.runner_builder(dict(pg_config.connection_settings))
        pg_guard = pg_builders.sql_guard_builder(pg_config.metadata_path)
        check(
            "PostgreSQL 默认 Runner 是 SchemaPreservingPostgresRunner",
            isinstance(pg_runner, SchemaPreservingPostgresRunner),
        )
        check("PostgreSQL 默认 Guard 是 SQLGuard", isinstance(pg_guard, SQLGuard))
        check(
            "PostgreSQL 默认 Agent builder 是 _create_postgresql_agent",
            pg_builders.agent_builder is _create_postgresql_agent,
        )
        pg_agent = _create_postgresql_agent(
            pg_config, object(), object(), object(), object(), OFFLINE_ENV
        )
        check(
            "PostgreSQL Agent 使用默认方言",
            pg_agent.system_prompt_builder.sql_dialect == "postgresql",
        )

        # ── MySQL 专属 Runner / Guard / mysql 方言 ──
        mysql_builders = _load_mysql_builders()
        mysql_runner = mysql_builders.runner_builder(dict(mysql_config.connection_settings))
        mysql_guard = mysql_builders.sql_guard_builder(mysql_config.metadata_path)
        check(
            "MySQL 默认 Runner 是 ReadOnlyMySQLRunner",
            isinstance(mysql_runner, ReadOnlyMySQLRunner),
        )
        check("MySQL 连接配置保留", mysql_runner.host == "offline.invalid"
              and mysql_runner.database == "offline"
              and mysql_runner.port == 3307)
        check("MySQL 默认 Guard 是 MySQLSQLGuard", isinstance(mysql_guard, MySQLSQLGuard))
        check(
            "MySQL 默认 Agent builder 是 _create_mysql_agent",
            mysql_builders.agent_builder is _create_mysql_agent,
        )
        mysql_agent = _create_mysql_agent(
            mysql_config, object(), object(), object(), object(), OFFLINE_ENV
        )
        check(
            "MySQL Agent 使用 mysql 方言",
            mysql_agent.system_prompt_builder.sql_dialect == "mysql",
        )

        # ── Agent 结构：Tool / Enhancer / Hook / Enricher 类型与顺序 ──
        registered = tuple(pg_agent.tool_registry._tools)
        run_sql_tool = pg_agent.tool_registry._tools["run_sql"]
        enhancer = pg_agent.llm_context_enhancer
        deterministic = getattr(enhancer, "base_enhancer", None)
        check(
            "Tool 注册名称与包装顺序不变（run_sql → GuardedRunSqlTool → SchemaPreservingRunSqlTool）",
            registered == ("run_sql",)
            and isinstance(run_sql_tool, GuardedRunSqlTool)
            and isinstance(run_sql_tool.inner_tool, SchemaPreservingRunSqlTool),
        )
        check(
            "Enhancer 链顺序不变（SqlExample → Deterministic → Default）",
            isinstance(enhancer, SqlExampleContextEnhancer)
            and isinstance(deterministic, DeterministicMetadataContextEnhancer),
        )
        check(
            "Lifecycle Hook 与 Enricher 类型不变",
            len(pg_agent.lifecycle_hooks) == 1
            and isinstance(pg_agent.lifecycle_hooks[0], OriginalQuestionLifecycleHook)
            and len(pg_agent.context_enrichers) == 1
            and isinstance(
                pg_agent.context_enrichers[0], OriginalQuestionContextEnricher
            ),
        )
        check(
            "LLM 服务类型与模型配置不变",
            isinstance(pg_agent.llm_service, TracingOpenAILlmService),
        )

        # ── PostgreSQL 配置校验仍执行；MySQL 不执行 ──
        validate_calls: list[object] = []
        build_shared_agent(
            config=pg_config,
            runner=object(),
            memory=object(),
            metadata_retriever=object(),
            sql_guard=object(),
            environ=OFFLINE_ENV,
            sql_dialect="postgresql",
            validate_db_config=lambda settings: validate_calls.append(settings),
            verbose=False,
        )
        check(
            "build_shared_agent 注入 validate_db_config 后被调用",
            len(validate_calls) == 1
            and validate_calls[0] is pg_config.connection_settings,
        )
        original_validate = settings_module.validate_db_config
        pg_validate_calls: list[object] = []

        def fake_validate(settings: object) -> None:
            pg_validate_calls.append(settings)

        settings_module.validate_db_config = fake_validate
        try:
            _create_postgresql_agent(
                pg_config, object(), object(), object(), object(), OFFLINE_ENV
            )
            _create_mysql_agent(
                mysql_config, object(), object(), object(), object(), OFFLINE_ENV
            )
        finally:
            settings_module.validate_db_config = original_validate
        check(
            "PostgreSQL Agent 内部仍调用 validate_db_config",
            len(pg_validate_calls) == 1,
        )
        check(
            "MySQL Agent 不调用 PostgreSQL validate_db_config",
            len(pg_validate_calls) == 1,
        )

        # ── 失败语义 ──
        empty_env: dict[str, str] = {}
        message = expect_error(
            lambda: build_shared_agent(
                config=pg_config,
                runner=object(),
                memory=object(),
                metadata_retriever=object(),
                sql_guard=object(),
                environ=empty_env,
                validate_db_config=None,
                verbose=False,
            ),
            ValueError,
        )
        check("缺少 DEEPSEEK_API_KEY 抛 ValueError", "DEEPSEEK_API_KEY" in message)

        for none_name in ("runner", "memory", "metadata", "guard", "agent"):
            def none_builder(name: str):
                def run(*args: object) -> object | None:
                    return None if name == none_name else object()

                return run

            builders = RuntimeBuilders(
                runner_builder=none_builder("runner"),
                memory_builder=none_builder("memory"),
                metadata_retriever_builder=none_builder("metadata"),
                sql_guard_builder=none_builder("guard"),
                agent_builder=none_builder("agent"),
            )
            message = expect_error(
                lambda builders=builders: assemble_runtime(
                    pg_config, builders, environ=OFFLINE_ENV
                ),
                ValueError,
            )
            check(f"Builder 返回 None（{none_name}）被拒绝", none_name in message)

        other_type = make_config(root, database_type="mysql", sql_dialect="mysql")
        message = expect_error(
            lambda: create_postgresql_runtime(
                other_type, builders=Recorder(PostgreSQLRuntimeBuilders).builders()
            ),
            ValueError,
        )
        check("错误 database_type 被 PostgreSQL 工厂拒绝", "PostgreSQL Runtime" in message)

        # DataSourceConfig 构造时强制 database_type/sql_dialect 成对匹配，
        # 故 create_*_runtime 的 sql_dialect 检查与 database_type 检查在真实数据流中等价。
        message = expect_error(
            lambda: create_mysql_runtime(
                pg_config, builders=Recorder(MySQLRuntimeBuilders).builders()
            ),
            ValueError,
        )
        check("非 MySQL 配置被 MySQL 工厂拒绝", "MySQL Runtime" in message)

        # DataSourceConfig 构造已强制 read_only=True 与 database_type↔sql_dialect 匹配，
        # 用绕过校验的原始配置验证 create_*_runtime 防御检查仍保留。
        raw_wrong_dialect = make_raw_config(
            database_type="postgresql", sql_dialect="mysql"
        )
        message = expect_error(
            lambda: create_postgresql_runtime(
                raw_wrong_dialect,
                builders=Recorder(PostgreSQLRuntimeBuilders).builders(),
            ),
            ValueError,
        )
        check("错误 sql_dialect 被 PostgreSQL 工厂拒绝", "PostgreSQL Runtime" in message)

        raw_not_readonly = make_raw_config(
            database_type="postgresql",
            sql_dialect="postgresql",
            read_only=False,
        )
        message = expect_error(
            lambda: create_postgresql_runtime(
                raw_not_readonly,
                builders=Recorder(PostgreSQLRuntimeBuilders).builders(),
            ),
            ValueError,
        )
        check("read_only=False 被拒绝", "read_only=True" in message)

    for name, passed in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    failed = sum(not passed for _, passed in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
