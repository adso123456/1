"""MySQL 全库扩展脚本的工作区隔离约束。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.data_source_catalog import (
    DataSourceCatalog,
    selected_scope_fingerprint,
)
from tools import publish_mysql_general_agent_scope as publisher
from tools import test_mysql_general_agent_expansion as acceptance


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_apply_requires_explicit_catalog() -> None:
    with pytest.raises(SystemExit) as exc:
        publisher._args(["--apply"])
    assert exc.value.code == 2


def test_plan_resolves_catalog_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "plan" / "catalog.sqlite3"
    monkeypatch.setenv("DATA_SOURCE_CATALOG_PATH", str(catalog))
    assert publisher._args([]).catalog == catalog.resolve()


def test_apply_only_uses_explicit_isolated_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = tmp_path / "isolated" / "catalog.sqlite3"
    forbidden = tmp_path / "main-worktree" / "catalog.sqlite3"
    forbidden.parent.mkdir()
    forbidden.write_text("unchanged", encoding="utf-8")
    environ = {
        "MYSQL_USER": "test-only",
        "MYSQL_PASSWORD": "test-only",
        "DEEPSEEK_API_KEY": "test-only",
    }
    catalog = DataSourceCatalog(isolated, environ=environ)
    catalog.initialize(
        [
            {
                "source_id": acceptance.SOURCE_ID,
                "display_name": "隔离测试数据源",
                "description": "仅用于目录路径隔离测试",
                "database_type": "mysql",
                "host": "127.0.0.1",
                "port": 3307,
                "database_name": "isolated",
                "credential_reference": {
                    "username": "MYSQL_USER",
                    "password": "MYSQL_PASSWORD",
                },
                "metadata_path": tmp_path / "assets" / "metadata.json",
                "memory_path": tmp_path / "assets" / "memory",
                "capabilities": [],
            }
        ]
    )
    selected = {
        "schema": "",
        "table": "isolated_table",
        "column": "id",
        "type": "bigint",
        "comment": "隔离测试主键",
        "nullable": False,
        "primary_key": True,
    }
    catalog.save_discovery(acceptance.SOURCE_ID, [selected])
    isolated_before = hashlib.sha256(isolated.read_bytes()).hexdigest()

    monkeypatch.setattr(
        publisher,
        "build_selected_scope",
        lambda *args: [selected],
    )
    monkeypatch.setattr(publisher, "_runtime_manager", lambda *args: object())
    monkeypatch.setattr(
        publisher,
        "DataSourceAssetPreparer",
        lambda *args: SimpleNamespace(prepare=lambda source_id: {}),
    )
    monkeypatch.setattr(publisher, "_verify_published", lambda *args: {})
    for name in ("MYSQL_USER", "MYSQL_PASSWORD", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "test-only")

    assert publisher.main(["--apply", "--catalog", str(isolated)]) == 0
    isolated_after = hashlib.sha256(isolated.read_bytes()).hexdigest()
    assert isolated_after != isolated_before
    assert forbidden.read_text(encoding="utf-8") == "unchanged"


def test_live_requires_explicit_metadata() -> None:
    with pytest.raises(SystemExit) as exc:
        acceptance._args(["--live"])
    assert exc.value.code == 2


def test_non_live_builds_candidate_without_main_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = []
    original_load = acceptance._load

    def tracked_load(path):
        loaded.append(Path(path).resolve())
        return original_load(path)

    monkeypatch.setattr(acceptance, "_load", tracked_load)
    assert acceptance.main([]) == 0
    assert loaded
    assert all(str(path).lower().startswith(str(ROOT).lower()) for path in loaded)


def test_scope_metadata_mismatch_fails() -> None:
    inventory = _load(acceptance.INVENTORY)
    scope = _load(acceptance.SCOPE)
    metadata = acceptance.build_candidate_metadata(inventory, scope)
    metadata.pop()
    with pytest.raises(RuntimeError, match="Metadata 与 scope 不一致"):
        acceptance.validate_metadata_scope(metadata, scope)


def test_manifest_checks_source_scope_hash_and_revision(tmp_path: Path) -> None:
    inventory = _load(acceptance.INVENTORY)
    scope = _load(acceptance.SCOPE)
    metadata = acceptance.build_candidate_metadata(inventory, scope)
    metadata_path = tmp_path / "column_metadata_index.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "source_id": acceptance.SOURCE_ID,
        "scope_fingerprint": selected_scope_fingerprint(metadata),
        "metadata_hash": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        "runtime_revision": 9,
    }
    (tmp_path / "asset_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    acceptance.validate_manifest(
        metadata_path,
        9,
        manifest["scope_fingerprint"],
    )
    with pytest.raises(RuntimeError, match="--revision"):
        acceptance.validate_manifest(metadata_path, None, None)
    with pytest.raises(RuntimeError, match="revision"):
        acceptance.validate_manifest(
            metadata_path,
            8,
            manifest["scope_fingerprint"],
        )
    with pytest.raises(RuntimeError, match="scope fingerprint"):
        acceptance.validate_manifest(metadata_path, 9, "wrong")


def test_new_scripts_have_no_fixed_main_workspace_path() -> None:
    forbidden = "/".join(("e:", "3", "posgresql", "1"))
    scripts = (
        ROOT / "tools" / "publish_mysql_general_agent_scope.py",
        ROOT / "tools" / "test_mysql_general_agent_expansion.py",
        ROOT / "tools" / "test_mysql_expansion_workspace_isolation.py",
    )
    assert all(
        forbidden
        not in path.read_text(encoding="utf-8").lower().replace("\\", "/")
        for path in scripts
    )
