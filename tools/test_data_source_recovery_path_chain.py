"""内置数据源发布恢复路径链与 Chroma metadata 清洗回归。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_connectors import (
    DataSourceAssetCleaner,
    DataSourceAssetPreparer,
)


def _record(is_builtin: bool, metadata: Path, memory: Path) -> object:
    return types.SimpleNamespace(
        is_builtin=is_builtin,
        metadata_path=metadata,
        memory_path=memory,
    )


def test_managed_root_contract(root: Path) -> None:
    import backend.data_source_connectors as connectors

    project = root / "project"
    agent_data = project / "agent_data"
    builtin = agent_data / "mysql-lzh-monitor"
    dynamic = agent_data / "data_sources" / "dynamic-source"
    outside = root / "outside"
    builtin.mkdir(parents=True)
    dynamic.mkdir(parents=True)
    outside.mkdir(parents=True)

    with patch.object(connectors, "PROJECT_ROOT", project):
        assert DataSourceAssetCleaner._managed_root(
            "mysql-lzh-monitor",
            _record(True, builtin / "metadata.json", builtin / "memory"),
        ) == builtin.resolve()
        assert DataSourceAssetCleaner._managed_root(
            "dynamic-source",
            _record(False, dynamic / "metadata.json", dynamic / "memory"),
        ) == dynamic.resolve()
        assert DataSourceAssetCleaner._managed_root(
            "mysql-lzh-monitor",
            _record(True, outside / "metadata.json", outside / "memory"),
        ) is None
        assert DataSourceAssetCleaner._managed_root(
            "dynamic-source",
            _record(False, dynamic / "metadata.json", outside / "memory"),
        ) is None


def _synthetic_link_chain(
    root: Path,
    *,
    escape_reenter: bool,
) -> None:
    """无 symlink 权限的平台仍直接验证 raw lexical 判定顺序。"""
    import backend.data_source_connectors as connectors

    project = root / "project"
    base = project / "agent_data"
    data_sources = base / "data_sources"
    managed = data_sources / "dynamic-source"
    outside = root / "outside"
    reentered = base / "reentered-dynamic-source"
    managed.mkdir(parents=True)
    outside.mkdir(parents=True)
    reentered.mkdir(parents=True)
    link_component = data_sources if escape_reenter else managed
    real_resolve = Path.resolve

    def fake_resolve(path: Path, *args, **kwargs) -> Path:
        if path == link_component:
            return outside
        if escape_reenter and path == managed:
            return reentered
        return real_resolve(path, *args, **kwargs)

    with (
        patch.object(connectors, "PROJECT_ROOT", project),
        patch.object(
            DataSourceAssetCleaner,
            "_is_link_or_reparse",
            side_effect=lambda path: path == link_component,
        ),
        patch.object(Path, "resolve", new=fake_resolve),
    ):
        assert not DataSourceAssetCleaner._managed_chain_safe(managed)


def test_internal_link_chain_stays_managed(root: Path) -> None:
    """允许内部链接时，逐组件目标与最终目标都必须留在边界内。"""
    import backend.data_source_connectors as connectors

    project = root / "project"
    base = project / "agent_data"
    managed = base / "data_sources" / "dynamic-source"
    inside = base / "internal-dynamic-source"
    managed.mkdir(parents=True)
    inside.mkdir(parents=True)
    real_resolve = Path.resolve

    def fake_resolve(path: Path, *args, **kwargs) -> Path:
        if path == managed:
            return inside
        return real_resolve(path, *args, **kwargs)

    with (
        patch.object(connectors, "PROJECT_ROOT", project),
        patch.object(
            DataSourceAssetCleaner,
            "_is_link_or_reparse",
            side_effect=lambda path: path == managed,
        ),
        patch.object(Path, "resolve", new=fake_resolve),
    ):
        assert DataSourceAssetCleaner._managed_chain_safe(managed)


def test_symlink_outside_and_reenter_rejected(root: Path) -> None:
    import backend.data_source_connectors as connectors

    if os.name == "nt":
        # 当前 Windows 测试账户可能没有 SeCreateSymbolicLinkPrivilege；
        # 用平台隔离映射验证 symlink 的逐组件 fail-closed 顺序。
        _synthetic_link_chain(root / "outside", escape_reenter=False)
        _synthetic_link_chain(root / "reenter", escape_reenter=True)
        probe = root / "symlink-probe"
        probe.mkdir(parents=True)
        with patch.object(Path, "is_symlink", return_value=True):
            assert DataSourceAssetCleaner._is_link_or_reparse(probe)
        return

    project = root / "project"
    base = project / "agent_data"
    outside = root / "outside-target"
    outside.mkdir(parents=True)
    direct = base / "data_sources" / "dynamic-source"
    direct.parent.mkdir(parents=True)
    direct.symlink_to(outside, target_is_directory=True)
    with patch.object(connectors, "PROJECT_ROOT", project):
        assert DataSourceAssetCleaner._managed_root(
            "dynamic-source",
            _record(False, outside / "metadata.json", outside / "memory"),
        ) is None

    reenter_root = root / "reenter"
    project = reenter_root / "project"
    base = project / "agent_data"
    bridge = reenter_root / "outside-bridge"
    inside = base / "inside-dynamic-source"
    bridge.mkdir(parents=True)
    inside.mkdir(parents=True)
    (base / "data_sources").parent.mkdir(parents=True)
    (base / "data_sources").symlink_to(bridge, target_is_directory=True)
    (bridge / "dynamic-source").symlink_to(inside, target_is_directory=True)
    with patch.object(connectors, "PROJECT_ROOT", project):
        assert DataSourceAssetCleaner._managed_root(
            "dynamic-source",
            _record(False, inside / "metadata.json", inside / "memory"),
        ) is None


def _make_junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"junction 创建失败：{result.stderr or result.stdout}")
    assert link.is_junction()


def test_windows_junction_outside_and_reenter_rejected(root: Path) -> None:
    import backend.data_source_connectors as connectors

    if os.name != "nt":
        probe = root / "junction-probe"
        probe.mkdir(parents=True)
        with patch.object(Path, "is_junction", return_value=True):
            assert DataSourceAssetCleaner._is_link_or_reparse(probe)
        return

    project = root / "direct" / "project"
    base = project / "agent_data"
    outside = root / "direct" / "outside-target"
    managed = base / "data_sources" / "dynamic-source"
    _make_junction(managed, outside)
    with patch.object(connectors, "PROJECT_ROOT", project):
        assert DataSourceAssetCleaner._managed_root(
            "dynamic-source",
            _record(False, outside / "metadata.json", outside / "memory"),
        ) is None

    project = root / "reenter" / "project"
    base = project / "agent_data"
    bridge = root / "reenter" / "outside-bridge"
    inside = base / "inside-dynamic-source"
    inside.mkdir(parents=True)
    _make_junction(base / "data_sources", bridge)
    _make_junction(bridge / "dynamic-source", inside)
    with patch.object(connectors, "PROJECT_ROOT", project):
        assert DataSourceAssetCleaner._managed_root(
            "dynamic-source",
            _record(False, inside / "metadata.json", inside / "memory"),
        ) is None


def test_builtin_rollback_failed_recovery(root: Path) -> None:
    import backend.data_source_connectors as connectors

    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        environ={},
    )
    catalog.initialize()
    project = root / "project"
    managed = project / "agent_data" / "mysql-lzh-monitor"
    source = catalog.create(
        display_name="builtin-recovery",
        description="builtin-recovery",
        database_type="postgresql",
        host="127.0.0.1",
        port=5433,
        database_name="test",
        schema_name="public",
        username="test",
        password="test",
        source_id="mysql-lzh-monitor",
        metadata_path=managed / "column_metadata_index.json",
        memory_path=managed / "memory.revision-1-base",
    )
    managed.mkdir(parents=True)
    formal = {
        "metadata": managed / "column_metadata_index.json",
        "memory": managed / "memory.revision-1-base",
        "ddl": managed / "ddl_memories.json",
        "documentation": managed / "business_documents.json",
        "manifest": managed / "asset_manifest.json",
    }
    formal["metadata"].write_text("[]", encoding="utf-8")
    formal["ddl"].write_text("[]", encoding="utf-8")
    formal["documentation"].write_text("[]", encoding="utf-8")
    formal["manifest"].write_text(
        json.dumps({"source_id": source.source_id}), encoding="utf-8"
    )
    formal["memory"].mkdir()
    (formal["memory"] / ".asset_identity.json").write_text(
        "{}", encoding="utf-8"
    )
    (formal["memory"] / "chroma.sqlite3").write_text(
        "base", encoding="utf-8"
    )

    batch_id = "2-1234567890-abcdef"
    candidate_root = managed / f"candidate-{batch_id}"
    candidate_root.mkdir()
    target_memory = managed / f"memory.revision-2-{batch_id}"
    target_memory.mkdir()
    (target_memory / "chroma.sqlite3").write_text("partial", encoding="utf-8")
    candidate = {
        "metadata": candidate_root / formal["metadata"].name,
        "memory": target_memory,
        "ddl": candidate_root / formal["ddl"].name,
        "documentation": candidate_root / formal["documentation"].name,
        "manifest": candidate_root / formal["manifest"].name,
    }
    for name, path in candidate.items():
        if name != "memory":
            path.write_text("candidate", encoding="utf-8")

    cleaner = DataSourceAssetCleaner(catalog)
    plan = []
    for name in ("metadata", "memory", "ddl", "documentation", "manifest"):
        destination = target_memory if name == "memory" else formal[name]
        plan.append(
            {
                "name": name,
                "candidate": str(candidate[name]),
                "formal": str(destination),
                "backup": str(
                    destination.with_name(
                        f".{destination.name}.backup-{batch_id}"
                    )
                ),
                "base_existed": name != "memory",
                "base_hash": (
                    cleaner._path_hash(formal[name])
                    if name != "memory"
                    else ""
                ),
                "target_hash": "",
            }
        )
    snapshot = {
        "base_runtime_revision": 1,
        "target_runtime_revision": 2,
        "base_status": "metadata_ready",
        "base_enabled_for_chat": False,
        "base_routing_summary": "",
        "base_memory_path": str(formal["memory"]),
        "target_memory_path": str(target_memory),
        "base_scope_fingerprint": "scope",
        "base_review_policy_fingerprint": "policy",
        "base_updated_at": int(time.time()),
        "base_last_error": "",
    }
    with catalog._lock, catalog._connection(write=True) as connection:
        connection.execute(
            """
            UPDATE data_sources SET is_builtin=1, status='error',
                enabled_for_chat=0, runtime_revision=1,
                metadata_path=?, memory_path=?,
                last_error='rollback_failed'
            WHERE source_id=?
            """,
            (str(formal["metadata"]), str(formal["memory"]), source.source_id),
        )
        connection.execute(
            """
            INSERT INTO active_asset_batches (
                source_id, batch_id, candidate_root, candidate_memory,
                published_memory_path, backup_paths_json, snapshot_json,
                asset_plan_json, backed_up_assets_json, installed_assets_json,
                phase, started_at, updated_at, owner_pid, last_error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source.source_id,
                batch_id,
                str(candidate_root),
                str(target_memory),
                str(target_memory),
                "[]",
                json.dumps(snapshot),
                json.dumps(plan),
                "[]",
                "[]",
                "rollback_failed",
                int(time.time()),
                int(time.time()),
                0,
                "rollback_failed",
            ),
        )

    with patch.object(connectors, "PROJECT_ROOT", project):
        DataSourceAssetCleaner(catalog).recover_incomplete_batches(
            source.source_id, grace_seconds=0
        )

    restored = catalog.require(source.source_id)
    remaining = catalog.active_asset_batches(source.source_id)
    assert not remaining, remaining
    assert restored.status == "metadata_ready"
    assert restored.runtime_revision == 1
    assert not restored.enabled_for_chat
    assert formal["memory"].is_dir()
    assert not candidate_root.exists()
    assert not target_memory.exists()


def test_chroma_metadata_sanitize() -> None:
    metadata = {
        "chroma:document": "hidden",
        "chroma:future": 1,
        "record_id": "sql-1",
        "tool_name": "run_sql",
        "source_id": "test-source",
    }
    cleaned = DataSourceAssetPreparer._sanitize_chroma_metadata(metadata)
    assert cleaned == {
        "record_id": "sql-1",
        "tool_name": "run_sql",
        "source_id": "test-source",
    }
    merged = DataSourceAssetPreparer._merge_extra_sql_tool_records(
        [],
        [("sql-1", "question", metadata)],
        source_id="test-source",
    )
    assert not any(key.startswith("chroma:") for key in merged[0][2])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="recovery-path-chain-") as name:
        root = Path(name)
        test_managed_root_contract(root / "managed-root")
        test_internal_link_chain_stays_managed(root / "internal-link")
        test_symlink_outside_and_reenter_rejected(root / "symlink")
        test_windows_junction_outside_and_reenter_rejected(root / "junction")
        test_builtin_rollback_failed_recovery(root / "builtin-recovery")
    test_chroma_metadata_sanitize()
    print("data source recovery path chain: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
