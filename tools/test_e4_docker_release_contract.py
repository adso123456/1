"""E-4 Gate 4：Docker 本地构建与 Git 隔离、Compose 契约、入口零 Catalog 修改、
显式 legacy 迁移 opt-in 定向测试。

测试不连接正式数据库、不读取正式 .env、不挂载正式 agent_data。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _git_patterns() -> list[str]:
    return [
        "git clone",
        "git pull",
        "git fetch",
        "git checkout",
        "git@github",
        "github.com",
    ]


def _make_catalog(root: Path) -> Path:
    path = root / "catalog.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE data_sources (
                source_id TEXT PRIMARY KEY,
                host TEXT, port INTEGER, database_name TEXT,
                metadata_path TEXT, memory_path TEXT
            );
            CREATE TABLE active_asset_batches (
                source_id TEXT PRIMARY KEY,
                candidate_root TEXT, candidate_memory TEXT,
                published_memory_path TEXT, backup_paths_json TEXT,
                snapshot_json TEXT, asset_plan_json TEXT,
                backed_up_assets_json TEXT, installed_assets_json TEXT
            );
            CREATE TABLE pending_asset_cleanup (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO data_sources VALUES (?,?,?,?,?,?)",
            (
                "postgresql-main",
                "localhost",
                5432,
                "gt",
                "C:/posgresql/1/agent_data/x/metadata.json",
                "C:/posgresql/1/vanna_data/x",
            ),
        )
        connection.execute(
            "INSERT INTO data_sources VALUES (?,?,?,?,?,?)",
            (
                "mysql-lzh-monitor",
                "127.0.0.1",
                3306,
                "lzh",
                "C:/posgresql/1/agent_data/y/meta.json",
                "C:/posgresql/1/vanna_data/y",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _migration_env(catalog: Path, **overrides) -> dict[str, str]:
    values = {
        "WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION": "1",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "gt_monitor",
        "DB_USER": "u",
        "DB_PASSWORD": "p",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "3306",
        "MYSQL_DATABASE": "lzh_monitor",
        "MYSQL_USER": "u",
        "MYSQL_PASSWORD": "p",
        "DATA_SOURCE_CATALOG_PATH": str(catalog),
        "METADATA_INDEX_PATH": "C:/posgresql/1/agent_data/postgresql-main/column_metadata_index.json",
        "VANNA_DATA_DIR": "C:/posgresql/1/vanna_data/postgresql-main",
        "MYSQL_METADATA_INDEX_PATH": "C:/posgresql/1/agent_data/mysql-lzh-monitor/column_metadata_index.json",
        "MYSQL_VANNA_DATA_DIR": "C:/posgresql/1/vanna_data/mysql-lzh-monitor",
    }
    values.update(overrides)
    return values


def _run_migration(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "deploy/docker/prepare_runtime.py"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_compose_build_context_is_local_dot() -> None:
    compose = _read("docker-compose.yml")
    assert "context: ." in compose
    assert "dockerfile: Dockerfile" in compose
    assert "build:" in compose


def test_no_git_commands_in_build_chain() -> None:
    files = [
        "Dockerfile",
        "docker-compose.yml",
        "deploy/docker/entrypoint.sh",
        "deploy/docker/server.py",
        "deploy/docker/prepare_runtime.py",
        "deploy/docker/smoke_check.py",
    ]
    haystack = "\n".join(_read(name) for name in files)
    for pattern in _git_patterns():
        assert pattern not in haystack, f"构建链包含 Git 关联：{pattern}"


def test_dockerfile_no_git_labels() -> None:
    dockerfile = _read("Dockerfile")
    for forbidden in (
        "VCS_REF",
        "BUILD_SOURCE",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.source",
    ):
        assert forbidden not in dockerfile, f"Dockerfile 包含禁止字段：{forbidden}"


def test_default_image_name_is_e4_local() -> None:
    compose = _read("docker-compose.yml")
    assert "water-agent:e4-local" in compose
    assert "water-agent:snapshot" not in compose


def test_compose_config_without_env() -> None:
    assert not (ROOT / ".env").exists(), "测试环境不应存在实体 .env"
    filtered = {
        key: value
        for key, value in os.environ.items()
        if key not in {"WATER_AGENT_IMAGE", "WATER_AGENT_HOST_PORT"}
    }
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=str(ROOT),
        env=filtered,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "water-agent:e4-local" in result.stdout


def test_default_entrypoint_no_prepare_runtime() -> None:
    entrypoint = _read("deploy/docker/entrypoint.sh")
    assert "exec python -m deploy.docker.server" in entrypoint
    migration_line = next(
        line
        for line in entrypoint.splitlines()
        if "prepare_runtime.py" in line
    )
    guard_index = entrypoint.find("WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION")
    assert guard_index != -1
    assert entrypoint.find(migration_line) > guard_index


def test_migration_only_when_enabled() -> None:
    with tempfile.TemporaryDirectory(prefix="e4-mig-off-") as directory:
        catalog = _make_catalog(Path(directory))
        env = _migration_env(catalog)
        env.pop("WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION")
        result = _run_migration(env)
        assert result.returncode == 0
        assert "not enabled" in result.stdout


def test_default_path_zero_catalog_write() -> None:
    with tempfile.TemporaryDirectory(prefix="e4-zero-") as directory:
        root = Path(directory)
        catalog = _make_catalog(root)
        before = hashlib.sha256(catalog.read_bytes()).hexdigest()
        env = _migration_env(catalog)
        env.pop("WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION")
        result = _run_migration(env)
        assert result.returncode == 0
        after = hashlib.sha256(catalog.read_bytes()).hexdigest()
        assert before == after


def test_explicit_migration_rewrites_paths() -> None:
    with tempfile.TemporaryDirectory(prefix="e4-rewrite-") as directory:
        root = Path(directory)
        catalog = _make_catalog(root)
        result = _run_migration(_migration_env(catalog))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "migration ready" in result.stdout
        connection = sqlite3.connect(catalog)
        try:
            pg = connection.execute(
                "SELECT metadata_path, memory_path FROM data_sources "
                "WHERE source_id='postgresql-main'"
            ).fetchone()
            assert pg[0].startswith("agent_data/postgresql-main/")
            assert pg[1].startswith("vanna_data/postgresql-main")
        finally:
            connection.close()


def test_missing_path_vars_no_keyerror() -> None:
    with tempfile.TemporaryDirectory(prefix="e4-missing-") as directory:
        root = Path(directory)
        catalog = _make_catalog(root)
        env = _migration_env(catalog)
        for key in (
            "METADATA_INDEX_PATH",
            "VANNA_DATA_DIR",
            "MYSQL_METADATA_INDEX_PATH",
            "MYSQL_VANNA_DATA_DIR",
        ):
            env.pop(key, None)
        result = _run_migration(env)
        assert result.returncode == 0
        assert "缺少必要迁移配置" in result.stdout
        assert "KeyError" not in result.stderr


def test_migration_failure_rolls_back() -> None:
    with tempfile.TemporaryDirectory(prefix="e4-rollback-") as directory:
        root = Path(directory)
        catalog = _make_catalog(root)
        env = _migration_env(catalog)
        import deploy.docker.prepare_runtime as migration

        with patch.dict(os.environ, env, clear=False):
            with patch.object(
                migration,
                "_rewrite_runtime_tables",
                side_effect=RuntimeError("injected mid-transaction"),
            ):
                code = migration.main()
        assert code == 1
        connection = sqlite3.connect(catalog)
        try:
            pg = connection.execute(
                "SELECT host, metadata_path FROM data_sources "
                "WHERE source_id='postgresql-main'"
            ).fetchone()
            assert pg[0] == "localhost"
            assert pg[1] == "C:/posgresql/1/agent_data/x/metadata.json"
        finally:
            connection.close()


def test_dockerignore_security_rules() -> None:
    dockerignore = _read(".dockerignore")
    for rule in (
        "**/*.pem",
        "**/*.key",
        "**/*.p12",
        "**/*.pfx",
        "**/credential_key",
        "*.sqlite-wal",
        "*.sqlite-shm",
        "*.sqlite3-wal",
        "*.sqlite3-shm",
        ".env",
        "runtime/",
        "agent_data/",
        "vanna_data/",
        "chroma/",
    ):
        assert rule in dockerignore, f".dockerignore 缺少规则：{rule}"


def main() -> int:
    import traceback

    failed = 0
    total = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        total += 1
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
