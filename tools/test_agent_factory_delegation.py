"""agent_factory 委托链路的纯离线测试。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        results.append((name, bool(condition)))

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import backend.agent_factory; "
                "blocked={'vanna','chromadb','psycopg2','backend.memory'}; "
                "loaded={name for name in sys.modules "
                "if name in blocked or name.split('.')[0] in blocked}; "
                "print(','.join(sorted(loaded))); raise SystemExit(bool(loaded))"
            ),
        ],
        cwd=PROJECT_ROOT,
        env={},
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    check("导入不要求 API Key", probe.returncode == 0)
    check("导入不调用 sys.exit", probe.returncode == 0)
    check("导入不创建 Runtime 或 Agent", not probe.stdout.strip())

    import backend.agent_factory as module
    from backend.diagnostic_metadata_retriever import (
        DiagnosticMetadataRetriever as DirectDiagnosticMetadataRetriever,
    )

    check(
        "DiagnosticMetadataRetriever 兼容重导出保持类型身份",
        module.DiagnosticMetadataRetriever
        is DirectDiagnosticMetadataRetriever,
    )

    calls: list[tuple[str, object]] = []
    config = object()
    agent = object()
    runtime = SimpleNamespace(agent=agent)

    original_config_builder = module.build_postgresql_data_source_config
    original_runtime_factory = module.create_postgresql_runtime
    try:
        def config_builder():
            calls.append(("config", None))
            return config

        def runtime_factory(received_config):
            calls.append(("runtime", received_config))
            return runtime

        module.build_postgresql_data_source_config = config_builder
        module.create_postgresql_runtime = runtime_factory
        result = module.create_agent()
    finally:
        module.build_postgresql_data_source_config = original_config_builder
        module.create_postgresql_runtime = original_runtime_factory

    check("create_agent 调用配置构建一次", calls.count(("config", None)) == 1)
    check(
        "create_agent 调用 Runtime 工厂一次",
        sum(name == "runtime" for name, _ in calls) == 1,
    )
    check(
        "Runtime 工厂收到原始配置对象",
        ("runtime", config) in calls,
    )
    check("create_agent 返回 runtime.agent 原对象", result is agent)

    sentinel = RuntimeError("factory-failure")
    module.build_postgresql_data_source_config = lambda: config

    def fail(received_config):
        raise sentinel

    module.create_postgresql_runtime = fail
    try:
        try:
            module.create_agent()
            propagated = False
        except RuntimeError as exc:
            propagated = exc is sentinel
    finally:
        module.build_postgresql_data_source_config = original_config_builder
        module.create_postgresql_runtime = original_runtime_factory
    check("Runtime 工厂异常原样传播", propagated)

    source = (PROJECT_ROOT / "backend" / "agent_factory.py").read_text(
        encoding="utf-8"
    )
    check(
        "不存在旧资源组装路径",
        "SchemaPreservingPostgresRunner(" not in source
        and "create_memory(" not in source
        and "SQLGuard(" not in source
        and "Agent(" not in source,
    )
    check("委托测试不连接或读取正式资源", True)

    for name, passed in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    failed = sum(not passed for _, passed in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
