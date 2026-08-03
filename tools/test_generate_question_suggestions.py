"""离线推荐问题生成器契约测试（V1 阻断修复版，隔离临时资产）。

覆盖：
1. 启用问题不含未解析占位词（某日/某月/某年/指定监测站等）；
2. 最终问题可生成并执行对应的只读 SQL（验证器被调用且结果对应资产 SQL）；
3. 缺 train_decision / 非 approved / 非 run_sql / 非法 training_level 均被过滤；
4. SQLGuard 失败的问题不启用；
5. Metadata 不含关联表时不启用；
6. 数据库执行失败的问题不启用；
7. 资产携带 runtime_revision 与 metadata_sha256；
8. 生成器不修改数据库与正式训练资产（输入哈希不变，只写本源资产）；
9. 未知数据源不生成资产；
10. 同输入重复运行结果稳定。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from tools.generate_question_suggestions import generate_questions


PLACEHOLDER_WORDS = (
    "某日", "某月", "某年", "某天", "某一天",
    "指定对象", "指定监测站", "指定站", "指定企业", "指定区域",
    "指定断面", "指定水文站", "指定气象站", "指定污染源",
    "某个", "某站", "某企业", "某区域", "某断面", "某监测站",
)

METADATA_ITEMS = [
    {"table": "wm_data", "column": "monitor_time", "comment": "监测时间"},
    {"table": "wm_data", "column": "ph_value", "comment": "pH 值"},
    {"table": "wm_data", "column": "station_id", "comment": "站点"},
    {"table": "wm_data", "column": "del_flag", "comment": "有效标志"},
    {"table": "wm_station", "column": "id", "comment": "ID"},
    {"table": "wm_station", "column": "station_name", "comment": "站点名称"},
    {"table": "wm_station", "column": "del_flag", "comment": "有效标志"},
]


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
                "credential_reference": {"username": "A_USER", "password": "A_PASSWORD"},
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


def _sql(sample_id: str, question: str, sql: str, **overrides) -> dict:
    base = {
        "sample_id": sample_id,
        "question": question,
        "tool_name": "run_sql",
        "training_level": "level2_sql_examples",
        "train_decision": "approved",
        "expected_behavior": "",
        "args": {"sql": sql},
        "expected_tables": ["wm_data", "wm_station"],
    }
    base.update(overrides)
    return base


def _write_materials(materials_dir: Path) -> Path:
    samples = [
        _sql(
            "SAMPLE_OK_DATE",
            "查询幸福河站2025年4月9日的pH小时变化，最多返回50条",
            (
                "SELECT r.monitor_time, r.ph_value "
                "FROM wm_data AS r "
                "JOIN wm_station AS s ON s.id = r.station_id AND s.del_flag = '0' "
                "WHERE s.station_name = '幸福河站' "
                "AND r.monitor_time >= '2025-04-09 00:00:00' "
                "AND r.monitor_time < '2025-04-10 00:00:00' "
                "AND r.del_flag = '0' "
                "ORDER BY r.monitor_time LIMIT 50"
            ),
        ),
        _sql(
            "SAMPLE_OK_PLAIN",
            "查询站点名称，最多返回50条",
            "SELECT s.station_name FROM wm_station AS s WHERE s.del_flag = '0' LIMIT 50",
            expected_tables=["wm_station"],
        ),
        # 过滤类：缺 train_decision
        {
            **_sql("SAMPLE_NO_DECISION", "查询A", "SELECT monitor_time FROM wm_data LIMIT 5"),
            "train_decision": None,
        },
        # 过滤类：非 approved
        {
            **_sql("SAMPLE_REJECTED", "查询B", "SELECT monitor_time FROM wm_data LIMIT 5"),
            "train_decision": "rejected",
        },
        # 过滤类：非 run_sql
        {
            **_sql("SAMPLE_WRONG_TOOL", "查询C", "SELECT monitor_time FROM wm_data LIMIT 5"),
            "tool_name": "generate_sql",
        },
        # 过滤类：非法 training_level
        {
            **_sql("SAMPLE_BAD_LEVEL", "查询D", "SELECT monitor_time FROM wm_data LIMIT 5"),
            "training_level": "not_a_real_level",
        },
        # SQLGuard 失败：SQL 引用未知表（关联表字段仍在本源）
        _sql(
            "SAMPLE_GUARD_FAIL",
            "查询E",
            "SELECT x.unknown_col FROM wm_ghost AS x LIMIT 5",
            expected_tables=["wm_data"],
        ),
        # Metadata 不匹配：关联表不在发布范围
        _sql(
            "SAMPLE_META_MISMATCH",
            "查询F",
            "SELECT monitor_time FROM wm_data LIMIT 5",
            expected_tables=["wm_data", "wm_ghost"],
        ),
        # 占位词：改写后仍含占位（防御性）
        _sql(
            "SAMPLE_PLACEHOLDER",
            "查询某监测站最新有数据的一天的pH",
            "SELECT monitor_time FROM wm_data LIMIT 5",
            expected_tables=["wm_data"],
        ),
    ]
    materials_file = materials_dir / "sql_examples.json"
    materials_file.write_text(
        json.dumps({"schema_version": 3, "samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return materials_file


def _write_metadata(metadata_path: Path) -> None:
    metadata_path.write_text(
        json.dumps(METADATA_ITEMS, ensure_ascii=False),
        encoding="utf-8",
    )


class FakeVerifier:
    """注入的只读验证器：控制最新日期与执行结果。"""

    def __init__(self, latest_day: date, verify_ok: bool = True) -> None:
        self.latest_day = latest_day
        self.verify_ok = verify_ok
        self.probes: list[str] = []
        self.verified_sqls: list[str] = []

    def connect(self) -> None:
        pass

    def resolve_latest_day(self, probe_sql: str):
        self.probes.append(probe_sql)
        return self.latest_day

    def verify(self, sql: str) -> dict:
        self.verified_sqls.append(sql)
        if not self.verify_ok:
            return {"verified": False, "read_only": True, "error": "syntax error"}
        return {
            "verified": True,
            "read_only": True,
            "columns": ["monitor_time", "ph_value"],
            "row_count_sampled": 10,
        }

    def close(self) -> None:
        pass


def _run(source_id, root, catalog_path, materials_dir, metadata_path, **kwargs):
    return generate_questions(
        source_id=source_id,
        root=root,
        catalog_path=str(catalog_path),
        materials_dir=str(materials_dir),
        metadata_path=metadata_path,
        no_db_verify=kwargs.get("no_db_verify", False),
        max_questions=kwargs.get("max_questions", 100),
        asset_version=kwargs.get("asset_version", "v1"),
        verifier=kwargs.get("verifier"),
    )


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))

    with tempfile.TemporaryDirectory(prefix="qs-gen2-") as directory:
        root = Path(directory)
        catalog_path = root / "catalog.sqlite3"
        materials_dir = root / "materials"
        materials_dir.mkdir()
        metadata_path = root / "metadata.json"
        asset_root = root / "assets"
        materials_file = _write_materials(materials_dir)
        _write_metadata(metadata_path)
        catalog = _make_catalog(catalog_path, root)

        before_materials = hashlib.sha256(materials_file.read_bytes()).hexdigest()
        before_metadata = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
        before_catalog = hashlib.sha256(catalog_path.read_bytes()).hexdigest()

        verifier = FakeVerifier(date(2026, 1, 15), verify_ok=True)
        summary = _run(
            "source-a",
            asset_root,
            catalog_path,
            materials_dir,
            metadata_path,
            verifier=verifier,
        )

        asset_file = asset_root / "source-a" / "questions_v1.json"
        payload = json.loads(asset_file.read_text(encoding="utf-8"))

        # 过滤统计：缺 train_decision / 非 approved / 非 run_sql / 非法 level
        reasons = summary["filter_reasons"]
        check("过滤:缺 train_decision", reasons.get("非 approved") == 2)
        check("过滤:非 run_sql", reasons.get("tool_name 非 run_sql") == 1)
        check("过滤:非法 training_level", reasons.get("非法 training_level: 'not_a_real_level'") == 1)

        # 占位词：防御性占位样本被禁用
        check("占位词问题被禁用", summary["disabled_reasons"].get("placeholder") == 1)

        # SQLGuard 失败不启用
        check("SQLGuard 失败问题不启用", summary["disabled_reasons"].get("sqlguard_fail") == 1)

        # Metadata 不匹配不启用
        check("Metadata 不匹配问题不启用", summary["disabled_reasons"].get("metadata_mismatch") == 1)

        # 启用的问题：占位词检查 + 数量
        enabled = [q for q in payload["questions"] if q.get("enabled")]
        check(
            "启用 2 条（日期改写 + 无日期）",
            len(enabled) == 2,
            f"count={len(enabled)}",
        )
        check(
            "启用问题不含占位词",
            all(
                not any(word in q["text"] for word in PLACEHOLDER_WORDS)
                for q in enabled
            ),
            f"texts={[q['text'] for q in enabled]}",
        )
        texts = [q["text"] for q in enabled]
        check(
            "日期改写为确定语义",
            any("最新有数据的一天" in text for text in texts),
            f"texts={texts}",
        )
        check(
            "保留真实站点名（无未解析占位）",
            any("幸福河站" in text for text in texts),
        )

        # 验证对应最终问题的实际 SQL
        asset_sqls = [q.get("related_sql", "") for q in enabled]
        check(
            "验证器验证了与资产一致的改写 SQL",
            verifier.verified_sqls == asset_sqls,
            f"n={len(verifier.verified_sqls)}",
        )
        check(
            "改写 SQL 使用真实日期（无占位日期）",
            all(
                "2026-01-15" in sql and "2026-01-16" in sql
                for sql in verifier.verified_sqls
                if "monitor_time >=" in sql
            ),
        )
        check(
            "资产携带 runtime_revision 与 metadata_sha256",
            payload["runtime_revision"] == 1
            and len(payload["metadata_sha256"]) == 64,
        )

        # 输入未被修改
        check("不修改 SQL 材料", hashlib.sha256(materials_file.read_bytes()).hexdigest() == before_materials)
        check("不修改 Metadata", hashlib.sha256(metadata_path.read_bytes()).hexdigest() == before_metadata)
        check("不修改 Catalog", hashlib.sha256(catalog_path.read_bytes()).hexdigest() == before_catalog)

        # 可重复审计
        verifier2 = FakeVerifier(date(2026, 1, 15), verify_ok=True)
        summary2 = _run(
            "source-a", asset_root, catalog_path, materials_dir, metadata_path, verifier=verifier2
        )
        payload2 = json.loads(asset_file.read_text(encoding="utf-8"))
        check(
            "同输入重复运行稳定",
            [(q["id"], q["text"], q.get("related_sql")) for q in payload["questions"]]
            == [(q["id"], q["text"], q.get("related_sql")) for q in payload2["questions"]],
        )

        # 未知数据源 → 报错且不写资产
        try:
            _run("source-unknown", asset_root, catalog_path, materials_dir, metadata_path, verifier=FakeVerifier(date(2026, 1, 15)))
            unknown_raises = False
        except Exception:
            unknown_raises = True
        check(
            "未知数据源报错且不生成资产",
            unknown_raises
            and not (asset_root / "source-unknown" / "questions_v1.json").exists(),
        )

        # 执行失败 → 不启用
        fail_verifier = FakeVerifier(date(2026, 1, 15), verify_ok=False)
        summary_fail = _run(
            "source-a",
            root / "assets2",
            catalog_path,
            materials_dir,
            metadata_path,
            verifier=fail_verifier,
        )
        check(
            "数据库执行失败的问题不启用",
            summary_fail["disabled_reasons"].get("execution_fail") == 2,
            f"reasons={summary_fail['disabled_reasons']}",
        )

        # no-db-verify 模式：不写启用问题，不修改输入
        before2 = hashlib.sha256(materials_file.read_bytes()).hexdigest()
        summary_skip = _run("source-a", root / "assets3", catalog_path, materials_dir, metadata_path, no_db_verify=True)
        check(
            "no-db-verify 模式不启用问题",
            summary_skip["enabled_question_count"] == 0
            and summary_skip["disabled_reasons"].get("verification_unavailable", 0) >= 2,
        )
        check(
            "no-db-verify 不修改输入",
            hashlib.sha256(materials_file.read_bytes()).hexdigest() == before2,
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
