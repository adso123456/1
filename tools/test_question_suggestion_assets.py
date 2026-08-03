"""数据源专属推荐问题资产与在线读取 API 契约测试。

覆盖：
1. A 数据源会话只能返回 A 的问题（目录严格隔离，不跨源补齐）；
2. 篡改/伪造前端 source_id 不能读取 B 源问题（服务端以会话绑定为准）；
3. 未绑定会话返回明确安全响应（404）；
4. 目录缺失/损坏/source_id 不匹配 → 空列表，不跨源兜底；
5. 同一会话重复请求结果稳定；
6. 不同新会话可以产生不同组合；
7. 不足 limit 时正确降级；
8. 资产按 source_id 独立生成/替换/失败，不影响其他源。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.question_suggestion_api import create_question_suggestion_router
from backend.question_suggestion_assets import (
    build_question_directory,
    load_question_directory,
    select_suggested_questions,
    write_question_directory,
)


def _make_asset(
    root: Path,
    source_id: str,
    texts: list[str],
    *,
    asset_version: str = "v1",
    enabled: bool = True,
    runtime_revision: int = 1,
    metadata_sha256: str = "test-metadata-sha",
) -> Path:
    questions = [
        {"id": f"q_{index:02d}", "text": text, "enabled": enabled}
        for index, text in enumerate(texts)
    ]
    directory = build_question_directory(
        source_id,
        questions,
        asset_version=asset_version,
        runtime_revision=runtime_revision,
        metadata_sha256=metadata_sha256,
        generated_at="2026-01-01T00:00:00+00:00",
        generator="test",
        basis={"note": "test asset"},
    )
    return write_question_directory(directory, root=root)


def _bootstrap(root: Path) -> list[dict]:
    common = {
        "host": "127.0.0.1",
        "connect_timeout": 10,
        "selected_tables_count": 1,
        "selected_columns_count": 1,
    }
    return [
        {
            **common,
            "source_id": "source-a",
            "display_name": "数据源 A",
            "description": "A 源",
            "database_type": "postgresql",
            "port": 5433,
            "database_name": "db_a",
            "schema_name": "public",
            "credential_reference": {"username": "A_USER", "password": "A_PASSWORD"},
            "metadata_path": root / "a.json",
            "memory_path": root / "a-memory",
            "routing_summary": "a",
            "capabilities": [],
        },
        {
            **common,
            "source_id": "source-b",
            "display_name": "数据源 B",
            "description": "B 源",
            "database_type": "mysql",
            "port": 3307,
            "database_name": "db_b",
            "credential_reference": {"username": "B_USER", "password": "B_PASSWORD"},
            "metadata_path": root / "b.json",
            "memory_path": root / "b-memory",
            "routing_summary": "b",
            "capabilities": [],
        },
        {
            **common,
            "source_id": "source-c",
            "display_name": "数据源 C",
            "description": "C 源（无问题资产）",
            "database_type": "postgresql",
            "port": 5433,
            "database_name": "db_c",
            "schema_name": "public",
            "credential_reference": {"username": "C_USER", "password": "C_PASSWORD"},
            "metadata_path": root / "c.json",
            "memory_path": root / "c-memory",
            "routing_summary": "c",
            "capabilities": [],
        },
    ]


def _make_api(root: Path):
    cipher = CredentialCipher(Fernet.generate_key().decode("ascii"))
    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=cipher,
        environ={
            "A_USER": "a",
            "A_PASSWORD": "a-secret",
            "B_USER": "b",
            "B_PASSWORD": "b-secret",
            "C_USER": "c",
            "C_PASSWORD": "c-secret",
        },
    )
    catalog.initialize(_bootstrap(root))
    registry = DataSourceRegistry.from_catalog(catalog)
    coordinator = DataSourceRequestCoordinator(registry)
    app = FastAPI()
    app.include_router(
        create_question_suggestion_router(
            catalog=catalog,
            coordinator=coordinator,
            asset_root=root / "question_suggestions",
        )
    )
    return catalog, coordinator, app


def _set_runtime_revision(catalog: DataSourceCatalog, source_id: str, revision: int) -> None:
    import sqlite3

    connection = sqlite3.connect(catalog.db_path)
    try:
        connection.execute(
            "UPDATE data_sources SET runtime_revision = ? WHERE source_id = ?",
            (revision, source_id),
        )
        connection.commit()
    finally:
        connection.close()


def _client(app: FastAPI):
    return TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    )


def _test_asset_loading() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))

    with tempfile.TemporaryDirectory(prefix="qs-assets-") as directory:
        root = Path(directory) / "question_suggestions"

        _make_asset(root, "source-a", ["A 问题一", "A 问题二", "A 问题三"])
        _make_asset(root, "source-b", ["B 问题一"])

        loaded_a = load_question_directory("source-a", root=root)
        loaded_b = load_question_directory("source-b", root=root)
        check("A 资产可加载", loaded_a is not None and loaded_a["source_id"] == "source-a")
        check("B 资产可加载", loaded_b is not None and loaded_b["source_id"] == "source-b")
        check(
            "A 目录只含 A 的问题",
            loaded_a is not None
            and all("B" not in item["text"] for item in loaded_a["questions"]),
        )

        # 目录缺失 → None
        check(
            "目录缺失返回 None",
            load_question_directory("missing-source", root=root) is None,
        )

        # 目录损坏（非法 JSON）→ None
        broken = root / "source-a" / "questions_v1.json"
        broken.write_text("{ not json", encoding="utf-8")
        check(
            "目录损坏返回 None",
            load_question_directory("source-a", root=root) is None,
        )
        # 恢复 A 资产，避免污染后续
        _make_asset(root, "source-a", ["A 问题一", "A 问题二", "A 问题三"])

        # source_id 不匹配 → None
        mismatch = root / "source-a" / "questions_v1.json"
        payload = mismatch.read_text(encoding="utf-8").replace("source-a", "source-c")
        mismatch.write_text(payload, encoding="utf-8")
        check(
            "source_id 不匹配返回 None",
            load_question_directory("source-a", root=root) is None,
        )
        _make_asset(root, "source-a", ["A 问题一", "A 问题二", "A 问题三"])

        # 确定性抽取
        directory = load_question_directory("source-a", root=root)
        assert directory is not None
        first = select_suggested_questions(directory, "conv-1")
        second = select_suggested_questions(directory, "conv-1")
        check(
            "同一会话结果稳定",
            [item["id"] for item in first] == [item["id"] for item in second]
            and [item["text"] for item in first] == [item["text"] for item in second],
        )
        check(
            "资产携带 runtime_revision 与 metadata_sha256",
            loaded_a["runtime_revision"] == 1
            and loaded_a["metadata_sha256"] == "test-metadata-sha",
        )
        check(
            "不足 3 条时只返回实际数量",
            len(select_suggested_questions(loaded_b, "conv-b", limit=4)) == 1,
        )
        check(
            "不足 limit 返回全部",
            len(select_suggested_questions(directory, "conv-1", limit=2)) == 2,
        )

        # 不同会话可产生不同组合（池 > limit 时按确定性种子抽样）
        many = _make_asset(
            root,
            "source-many",
            [f"多问题 {index:02d}" for index in range(12)],
        )
        many_directory = load_question_directory("source-many", root=root)
        assert many_directory is not None
        samples: set[tuple[str, ...]] = set()
        for index in range(40):
            picked = tuple(
                item["id"]
                for item in select_suggested_questions(many_directory, f"conv-{index}")
            )
            samples.add(picked)
        check(
            "不同会话可以产生不同组合",
            len(samples) > 1,
            f"unique combinations={len(samples)}",
        )

        # 不跨源补齐：A 资产只有 3 条，即使 B 有 1 条也不会补充进 A
        check(
            "A 会话绝不返回 B 的问题",
            all("B" not in item["text"] for item in select_suggested_questions(directory, "conv-1")),
        )

        # 空资产（0 问题）→ 空结果
        empty = root / "source-empty"
        directory_empty = build_question_directory(
            "source-empty", [], generated_at="x", generator="test"
        )
        write_question_directory(directory_empty, root=root)
        loaded_empty = load_question_directory("source-empty", root=root)
        check(
            "空资产返回空列表",
            loaded_empty is not None
            and select_suggested_questions(loaded_empty, "conv-e") == [],
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return results


def _test_api() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))

    with tempfile.TemporaryDirectory(prefix="qs-api-") as directory:
        root = Path(directory)
        catalog, coordinator, app = _make_api(root)
        asset_root = root / "question_suggestions"
        _make_asset(
            asset_root,
            "source-a",
            [f"A 问题 {index:02d}" for index in range(12)],
        )
        _make_asset(asset_root, "source-b", ["B 问题一"])

        # 未绑定会话 → 404 明确安全响应
        with _client(app) as client:
            unbound = client.get("/api/conversations/conv-unbound/suggested-questions")
            check(
                "未绑定会话返回 404",
                unbound.status_code == 404,
                f"status={unbound.status_code}",
            )

            # 绑定会话到 A
            bound = catalog.bind_conversation("conv-a", "source-a")
            check("会话绑定 A 成功", bound[1] == "source-a")

            # 正常请求：A 会话只返回 A 的问题
            response = client.get("/api/conversations/conv-a/suggested-questions")
            check(
                "A 会话只返回 A 的问题",
                response.status_code == 200,
                f"status={response.status_code}",
            )
            payload = response.json()
            check(
                "响应绑定 source-a",
                payload["source_id"] == "source-a" and payload["asset_version"] == "v1",
            )
            check(
                "返回 3~4 条",
                3 <= len(payload["questions"]) <= 4,
                f"count={len(payload['questions'])}",
            )
            check(
                "问题均来自 A",
                all("B" not in item["text"] for item in payload["questions"]),
            )

            # 篡改：伪造 source_id 查询参数也不能读取 B
            tampered = client.get(
                "/api/conversations/conv-a/suggested-questions?source_id=source-b"
            )
            check(
                "伪造 source_id 不能读取 B 的问题",
                tampered.json()["source_id"] == "source-a"
                and all("B" not in item["text"] for item in tampered.json()["questions"]),
            )

            # 同一会话重复请求结果稳定
            again = client.get("/api/conversations/conv-a/suggested-questions").json()
            check(
                "同一会话重复请求稳定",
                [item["id"] for item in payload["questions"]]
                == [item["id"] for item in again["questions"]],
            )

            # 不同会话产生不同组合（统计层面）
            combos: set[str] = set()
            for index in range(30):
                bound_id = catalog.bind_conversation(f"conv-{index}", "source-a")[0]
                picked = client.get(
                    f"/api/conversations/{bound_id}/suggested-questions"
                ).json()
                combos.add(tuple(item["id"] for item in picked["questions"]))
            check(
                "不同新会话可产生不同组合",
                len(combos) > 1,
                f"unique={len(combos)}",
            )

            # 目录缺失（source-c 没有 asset）→ 空列表而非跨源补齐
            missing_asset = client.get(
                f"/api/conversations/{catalog.bind_conversation('conv-missing', 'source-c')[0]}/suggested-questions"
            )
            check(
                "资产缺失返回空列表",
                missing_asset.status_code == 200
                and missing_asset.json()["questions"] == []
                and missing_asset.json()["asset_version"] is None,
                f"source={missing_asset.json().get('source_id')}",
            )

            # revision 一致（1 = 目录当前 1）时正常返回
            matching_rev = client.get(
                f"/api/conversations/{catalog.bind_conversation('conv-rev-ok', 'source-a')[0]}/suggested-questions"
            )
            check(
                "runtime_revision 一致时正常返回",
                matching_rev.status_code == 200
                and len(matching_rev.json()["questions"]) > 0,
            )

            # revision 不一致 → 空列表（资产仍存在但禁止展示）
            _set_runtime_revision(catalog, "source-a", 2)
            mismatched_rev = client.get(
                f"/api/conversations/{catalog.bind_conversation('conv-rev-bad', 'source-a')[0]}/suggested-questions"
            )
            check(
                "runtime_revision 不一致返回空列表",
                mismatched_rev.status_code == 200
                and mismatched_rev.json()["questions"] == []
                and mismatched_rev.json()["asset_version"] == "v1",
            )
            _set_runtime_revision(catalog, "source-a", 1)

            # 源损坏 → 空列表
            catalog.bind_conversation("conv-corrupt", "source-a")
            (asset_root / "source-a" / "questions_v1.json").write_text(
                "{bad json", encoding="utf-8"
            )
            corrupt = client.get("/api/conversations/conv-corrupt/suggested-questions")
            check(
                "目录损坏返回空列表",
                corrupt.status_code == 200 and corrupt.json()["questions"] == [],
            )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return results


def main() -> int:
    failures = 0
    for fn in (_test_asset_loading, _test_api):
        results = fn()
        failures += sum(1 for _, passed, _ in results if not passed)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
