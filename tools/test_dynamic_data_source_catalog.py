"""B5 动态目录、凭据、范围与永久绑定的离线回归。"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    DataSourceCatalogError,
    DataSourceConflict,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def bootstrap(root: Path) -> list[dict]:
    return [
        {
            "source_id": "postgresql-main",
            "display_name": "排污口治理数据",
            "description": "排污口数据",
            "database_type": "postgresql",
            "host": "127.0.0.1",
            "port": 5433,
            "database_name": "gt_monitor",
            "schema_name": "public",
            "connect_timeout": 10,
            "credential_reference": {
                "username": "DB_USER",
                "password": "DB_PASSWORD",
            },
            "metadata_path": root / "pg" / "metadata.json",
            "memory_path": root / "pg" / "memory",
            "capabilities": [],
        },
        {
            "source_id": "mysql-lzh-monitor",
            "display_name": "梁子湖监测数据",
            "description": "水质数据",
            "database_type": "mysql",
            "host": "127.0.0.1",
            "port": 3307,
            "database_name": "lzh_monitor",
            "connect_timeout": 10,
            "credential_reference": {
                "username": "MYSQL_USER",
                "password": "MYSQL_PASSWORD",
            },
            "metadata_path": root / "mysql" / "metadata.json",
            "memory_path": root / "mysql" / "memory",
            "capabilities": ["water_quality_daily_report"],
        },
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="b5-catalog-") as directory:
        root = Path(directory)
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"))
        catalog = DataSourceCatalog(
            root / "catalog.sqlite3",
            cipher=cipher,
            environ={
                "DB_USER": "postgres",
                "DB_PASSWORD": "postgres-secret",
                "MYSQL_USER": "mysql",
                "MYSQL_PASSWORD": "mysql-secret",
            },
        )
        catalog.initialize(bootstrap(root))
        catalog.initialize(bootstrap(root))
        records = catalog.list()
        check(len(records) == 2, "内置双数据源迁移幂等")
        check(
            all(item.status == "ready" and item.enabled_for_chat for item in records),
            "内置源首次迁移为 ready 且可问数",
        )

        original = catalog.require("mysql-lzh-monitor")
        renamed = catalog.update(
            original.source_id,
            display_name="梁子湖流域水质监测库",
        )
        check(renamed.source_id == original.source_id, "重命名不改变 source_id")
        check(
            renamed.metadata_path == original.metadata_path
            and renamed.memory_path == original.memory_path,
            "重命名不移动 Metadata 或 Memory",
        )
        catalog.initialize(bootstrap(root))
        check(
            catalog.require(original.source_id).display_name
            == "梁子湖流域水质监测库",
            "重复启动不覆盖用户显示名称",
        )

        created = catalog.create(
            display_name="临时 PostgreSQL",
            description="离线目录测试",
            database_type="postgresql",
            host="127.0.0.1",
            port=5433,
            database_name="gt_monitor",
            schema_name="public",
            username="catalog-user",
            password="catalog-password",
        )
        check(created.source_id.startswith("ds_"), "新数据源自动生成永久 ID")
        public = created.public_dict(detail=True)
        check("password" not in public and public["has_password"], "API DTO 不回显密码")
        raw = sqlite3.connect(catalog.db_path).execute(
            """
            SELECT encrypted_username, encrypted_password
            FROM data_sources WHERE source_id = ?
            """,
            (created.source_id,),
        ).fetchone()
        check(
            "catalog-user" not in raw[0]
            and "catalog-password" not in raw[1],
            "用户名和密码均以密文写入 SQLite",
        )
        check(
            catalog.credentials(created.source_id)
            == ("catalog-user", "catalog-password"),
            "加密凭据可以受控解密",
        )

        metadata = [
            {
                "schema": "public",
                "table": "safe_table",
                "object_type": "table",
                "table_comment": "安全测试表",
                "column": "id",
                "type": "bigint",
                "comment": "主键",
                "nullable": False,
                "primary_key": True,
                "ordinal_position": 1,
            },
            {
                "schema": "public",
                "table": "safe_table",
                "object_type": "table",
                "table_comment": "安全测试表",
                "column": "name",
                "type": "text",
                "comment": "名称",
                "nullable": True,
                "primary_key": False,
                "ordinal_position": 2,
            },
        ]
        catalog.mark_connection_test(created.source_id, success=True)
        catalog.save_discovery(created.source_id, metadata)
        scoped = catalog.save_scope(created.source_id, metadata[:1])
        check(
            scoped.selected_tables_count == 1
            and scoped.selected_columns_count == 1,
            "表字段范围按发现结果保存",
        )
        check(
            "name" not in json.dumps(scoped.selected_scope, ensure_ascii=False),
            "未选择字段不进入问数范围",
        )
        ready = catalog.publish(created.source_id, routing_summary="安全测试表 主键")
        ready.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        ready.metadata_path.write_text("[]\n", encoding="utf-8")
        ready.memory_path.mkdir(parents=True, exist_ok=True)
        check(
            ready.runtime_revision == 1 and ready.status == "ready",
            "原子发布递增 runtime_revision",
        )
        disabled = catalog.set_enabled(created.source_id, False)
        check(
            disabled.status == "disabled" and not disabled.enabled_for_chat,
            "ready 仅可转换为 disabled",
        )
        enabled = catalog.set_enabled(created.source_id, True)
        check(
            enabled.status == "ready" and enabled.enabled_for_chat,
            "disabled 仅可转换为 ready",
        )

        for illegal_status in (
            "draft",
            "connected",
            "metadata_ready",
            "training_required",
            "error",
        ):
            with catalog._lock, catalog._connection(write=True) as connection:
                connection.execute(
                    """
                    UPDATE data_sources SET status = ?, enabled_for_chat = 0
                    WHERE source_id = ?
                    """,
                    (illegal_status, created.source_id),
                )
            for flag in (False, True):
                try:
                    catalog.set_enabled(created.source_id, flag)
                except DataSourceCatalogError:
                    pass
                else:
                    raise AssertionError(
                        f"{illegal_status} 不应允许启停操作"
                    )
                check(
                    catalog.require(created.source_id).status == illegal_status,
                    f"{illegal_status} 非法启停不改变目录状态",
                )

        with catalog._lock, catalog._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_sources SET status = 'ready', enabled_for_chat = 1
                WHERE source_id = ?
                """,
                (created.source_id,),
            )
        changed_scope = catalog.save_scope(created.source_id, metadata[:1])
        check(
            changed_scope.status == "training_required"
            and not changed_scope.enabled_for_chat,
            "ready 修改范围后进入 training_required",
        )
        for flag in (False, True):
            try:
                catalog.set_enabled(created.source_id, flag)
            except DataSourceCatalogError:
                pass
            else:
                raise AssertionError(
                    "training_required 不应通过启停绕过 prepare"
                )
        check(
            catalog.require(created.source_id).status == "training_required",
            "training_required 无法通过 disable/enable 绕过",
        )
        ready = catalog.publish(
            created.source_id,
            routing_summary="安全测试表 主键",
        )
        def concurrent_disable(_: int) -> str:
            try:
                catalog.set_enabled(created.source_id, False)
            except DataSourceCatalogError:
                return "rejected"
            return "disabled"

        with ThreadPoolExecutor(max_workers=2) as executor:
            transitions = list(executor.map(concurrent_disable, range(2)))
        check(
            sorted(transitions) == ["disabled", "rejected"]
            and catalog.require(created.source_id).status == "disabled",
            "并发停用仅有一个条件更新成功且不形成非法状态",
        )
        catalog.set_enabled(created.source_id, True)

        first = catalog.bind_conversation("conversation-b5", created.source_id)
        second = catalog.bind_conversation("conversation-b5", created.source_id)
        check(first == second, "同源会话绑定幂等")
        try:
            catalog.bind_conversation("conversation-b5", "postgresql-main")
        except DataSourceConflict:
            check(True, "会话改绑被永久目录拒绝")
        else:
            raise AssertionError("会话改绑未被拒绝")
        restarted = DataSourceCatalog(
            catalog.db_path,
            cipher=cipher,
            environ={},
        )
        check(
            restarted.require_binding("conversation-b5")[1] == created.source_id,
            "进程重启后会话绑定仍可恢复",
        )

        def rename(index: int) -> str:
            return catalog.update(
                created.source_id,
                description=f"并发更新 {index}",
            ).description

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(rename, range(16)))
        check(len(results) == 16, "SQLite 并发更新全部完成")

        dependency = catalog.dependency_summary(created.source_id)
        check(
            dependency["conversations"] == 1
            and not dependency["physical_delete_allowed"],
            "历史会话依赖阻止物理删除",
        )

    print("dynamic data source catalog: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
