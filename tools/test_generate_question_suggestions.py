"""离线推荐问题生成器契约测试（隔离临时资产，不碰正式资产与数据库）。

覆盖：
1. 生成器只读取指定 source_id 的目录记录与材料；
2. 生成器不修改数据库和正式训练资产（输入文件哈希不变，只写本源问题资产）；
3. 泛化后问题不含具体日期/ID；
4. 具体名称被泛化为稳定称呼；
5. 资产按 source_id 严格隔离；未知 source 直接报错且不写资产；
6. 输出可重复审计：同输入再运行，问题 ID 与文本稳定。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from tools.generate_question_suggestions import (
    generate_questions,
    generalize_question,
)


def _make_catalog(db_path: Path, root: Path) -> DataSourceCatalog:
    cipher = CredentialCipher(Fernet.generate_key().decode("ascii"))
    catalog = DataSourceCatalog(
        db_path,
        cipher=cipher,
        environ={"A_USER": "a", "A_PASSWORD": "a-secret"},
    )
    catalog.initialize(
        [
            {
                "source_id": "source-a",
                "display_name": "数据源 A",
                "description": "A 源",
                "database_type": "postgresql",
                "host": "127.0.0.1",
                "port": 5433,
                "database_name": "db_a",
                "schema_name": "public",
                "credential_reference": {
                    "username": "A_USER",
                    "password": "A_PASSWORD",
                },
                "metadata_path": root / "metadata.json",
                "memory_path": root / "memory",
                "routing_summary": "a",
                "capabilities": [],
                "connect_timeout": 10,
                "selected_tables_count": 1,
                "selected_columns_count": 1,
            }
        ]
    )
    return catalog


def _write_materials(materials_dir: Path) -> Path:
    samples = [
        {
            "sample_id": "SAMPLE_001",
            "question": "查询幸福河站2025年4月9日的pH小时变化，最多返回50条",
            "tool_name": "run_sql",
            "args": {
                "sql": (
                    "SELECT r.monitor_time, r.m2_value AS ph_value "
                    "FROM wm_waterquality_hour_records r "
                    "JOIN wm_station_info s ON s.id = r.station_id "
                    "WHERE s.station_name = '幸福河站' "
                    "AND r.monitor_time >= '2025-04-09 00:00:00' "
                    "AND r.monitor_time < '2025-04-10 00:00:00' "
                    "ORDER BY r.monitor_time LIMIT 50"
                )
            },
            "train_decision": "approved",
            "expected_behavior": "返回时间和pH，适合折线图",
            "expected_tables": ["wm_waterquality_hour_records", "wm_station_info"],
        },
        {
            "sample_id": "SAMPLE_002",
            "question": "比较污染源监测站ID 96、97、98在2023年12月24日的平均氨氮",
            "tool_name": "run_sql",
            "args": {
                "sql": (
                    "SELECT station_id, AVG(ammonia_nitrogen) AS avg_nh3 "
                    "FROM rs_pollutant_day_records "
                    "WHERE station_id IN (96, 97, 98) "
                    "AND record_time = '2023-12-24'"
                )
            },
            "train_decision": "approved",
            "expected_behavior": "返回ID和平均氨氮，适合柱状图",
            "expected_tables": ["rs_pollutant_day_records"],
        },
        {
            "sample_id": "SAMPLE_003",
            "question": "查询数据字典中的列表类型、列表描述、列表项代码和列表项名称，最多返回50条",
            "tool_name": "run_sql",
            "args": {
                "sql": (
                    "SELECT list_type, list_desc, item_code, item_name "
                    "FROM ad_dict LIMIT 50"
                )
            },
            "train_decision": "approved",
            "expected_behavior": "返回字典明细",
            "expected_tables": ["ad_dict"],
        },
    ]
    materials_file = materials_dir / "sql_examples.json"
    materials_file.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "training_level": "test",
                "samples": samples,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return materials_file


def _write_metadata(metadata_path: Path) -> None:
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "table": "ad_dict",
                    "table_comment": "数据字典",
                    "column": "item_name",
                    "comment": "列表项名称",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))

    with tempfile.TemporaryDirectory(prefix="qs-generator-") as directory:
        root = Path(directory)
        catalog_path = root / "catalog.sqlite3"
        materials_dir = root / "materials"
        materials_dir.mkdir()
        metadata_path = root / "metadata.json"
        asset_root = root / "assets"
        materials_file = _write_materials(materials_dir)
        _write_metadata(metadata_path)
        catalog = _make_catalog(catalog_path, root)

        before_materials = _sha256(materials_file)
        before_metadata = _sha256(metadata_path)
        before_catalog = _sha256(catalog_path)

        summary = generate_questions(
            source_id="source-a",
            root=asset_root,
            catalog_path=str(catalog_path),
            materials_dir=str(materials_dir),
            metadata_path=metadata_path,
            no_db_verify=True,
            max_questions=100,
            asset_version="v1",
        )

        check("生成器返回摘要", summary["source_id"] == "source-a")
        check(
            "生成 3 条问题",
            summary["generated_question_count"] == 3,
            f"count={summary['generated_question_count']}",
        )
        check("无数据库验证模式", summary["db_verification"] == "skipped")

        asset_file = asset_root / "source-a" / "questions_v1.json"
        check("资产已写入本源目录", asset_file.is_file())

        payload = json.loads(asset_file.read_text(encoding="utf-8"))
        check("资产 schema_version", payload["schema_version"] == 1)
        check("资产 source_id", payload["source_id"] == "source-a")
        texts = [item["text"] for item in payload["questions"]]

        check(
            "泛化问题不含具体日期",
            all(not re.search(r"\d{4}年|\d{4}[-/]", text) for text in texts),
            f"texts={texts}",
        )
        check(
            "泛化问题不含具体 ID",
            all("ID" not in text and "96" not in text for text in texts),
        )
        check(
            "具体站名被泛化",
            any("指定监测站" in text for text in texts),
            f"texts={texts}",
        )
        check(
            "无日期问题保持原样",
            any("数据字典" in text and "最多返回50条" in text for text in texts),
        )

        # 输入文件未被修改
        check(
            "不修改已批准 SQL 材料",
            _sha256(materials_file) == before_materials,
        )
        check(
            "不修改 Metadata",
            _sha256(metadata_path) == before_metadata,
        )
        check(
            "不修改 Catalog",
            _sha256(catalog_path) == before_catalog,
        )
        # 资产根之外不写任何文件
        written_outside = [
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file()
            and not str(path).startswith(str(asset_root))
            and not str(path).startswith(str(catalog_path))
            and not str(path).startswith(str(materials_dir))
            and str(path) != str(metadata_path)
            and path.name != "catalog.sqlite3-wal"
            and path.name != "catalog.sqlite3-shm"
        ]
        check(
            "只写本源问题资产，不写其他文件",
            written_outside == [],
            f"extra={written_outside}",
        )

        # 可重复审计：再运行，问题 ID/文本稳定
        summary2 = generate_questions(
            source_id="source-a",
            root=asset_root,
            catalog_path=str(catalog_path),
            materials_dir=str(materials_dir),
            metadata_path=metadata_path,
            no_db_verify=True,
            max_questions=100,
            asset_version="v1",
        )
        payload2 = json.loads(asset_file.read_text(encoding="utf-8"))
        check(
            "同输入重复运行问题稳定",
            [(q["id"], q["text"]) for q in payload["questions"]]
            == [(q["id"], q["text"]) for q in payload2["questions"]],
        )

        # 未知 source_id → 报错且不写资产
        try:
            generate_questions(
                source_id="source-unknown",
                root=asset_root,
                catalog_path=str(catalog_path),
                materials_dir=str(materials_dir),
                metadata_path=metadata_path,
                no_db_verify=True,
                max_questions=100,
                asset_version="v1",
            )
            unknown_failed = False
        except Exception:
            unknown_failed = True
        check(
            "未知数据源直接报错且不生成资产",
            unknown_failed and not (asset_root / "source-unknown" / "questions_v1.json").exists(),
        )

        # 只读取指定数据源：另一个源未创建对应资产
        check(
            "未为其他 source_id 创建资产",
            not (asset_root / "source-unknown" / "questions_v1.json").exists()
            and not (asset_root / "other-source" / "questions_v1.json").exists(),
        )

    # 泛化单测
    check(
        "泛化去日期与名称",
        generalize_question(
            "查询幸福河站2025年4月9日的pH小时变化",
            "SELECT * FROM t WHERE station_name='幸福河站' AND d>='2025-04-09'",
        )
        == "查询指定监测站某日的pH小时变化",
    )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
