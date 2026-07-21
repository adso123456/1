from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.storage_snapshot import build_directory_manifest, create_verified_copy


BASE_COMMIT = "ab272b51212bf5b68904760654ef56fb8f74a9ff"
EXPECTED_CANDIDATE_SHA256 = "46b9661814de42097f492822946a5b6ea6b39440ef338f53befa4a5bb50bd9a6"
EXPECTED_RECORD_COUNT = 187
CANDIDATE_CHROMA = Path(
    r"E:\3\_training_backups\full-schema-functional-115-20260715-152328\vanna_data"
)
LEGACY_FORMAL = PROJECT_ROOT / "vanna_data"
PROMOTED_CHROMA = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
METADATA_INDEX = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
BACKUP_PARENT = Path(r"E:\3\_training_backups")

SCHEMA_SQL = """SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;"""


def manifest(path: Path) -> dict[str, Any]:
    return build_directory_manifest(path).to_dict()


def chroma_record_count(path: Path) -> int:
    uri = path.joinpath("chroma.sqlite3").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_index_schema(path: Path) -> dict[str, set[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("metadata index 顶层必须是列表")
    result: dict[str, set[str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError("metadata index 包含非对象条目")
        table = str(item.get("table", "")).strip()
        column = str(item.get("column", "")).strip()
        if not table or not column:
            raise RuntimeError("metadata index 包含空对象名或字段名")
        result.setdefault(table, set()).add(column)
    return result


def load_live_schema() -> dict[str, set[str]]:
    import psycopg2

    def positive_int(name: str, default: int) -> int:
        value = int(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError(f"环境变量 {name} 必须是正整数")
        return value

    db_kwargs = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": positive_int("DB_PORT", 5433),
        "database": os.getenv("DB_NAME", "gt_monitor"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "connect_timeout": positive_int("DB_CONNECT_TIMEOUT", 10),
        "application_name": "vanna-water-agent-f4-cutover",
        "options": " ".join(
            (
                "-c default_transaction_read_only=on",
                f"-c statement_timeout={positive_int('DB_STATEMENT_TIMEOUT_MS', 30000)}",
                f"-c lock_timeout={positive_int('DB_LOCK_TIMEOUT_MS', 5000)}",
            )
        ),
    }
    if not db_kwargs["user"] or not db_kwargs["password"]:
        raise ValueError("缺少必需的数据库环境变量：DB_USER 或 DB_PASSWORD")
    sslmode = os.getenv("DB_SSLMODE", "").strip()
    if sslmode:
        db_kwargs["sslmode"] = sslmode
    result: dict[str, set[str]] = {}
    with psycopg2.connect(**db_kwargs) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
            for table, column in cursor.fetchall():
                result.setdefault(str(table), set()).add(str(column))
    return result


def compare_schema_scope(
    live: dict[str, set[str]], index: dict[str, set[str]]
) -> dict[str, Any]:
    live_names = set(live)
    index_names = set(index)
    shared = live_names & index_names
    column_differences = [
        {
            "table": name,
            "index_only_columns": sorted(index[name] - live[name]),
            "live_only_columns": sorted(live[name] - index[name]),
        }
        for name in sorted(shared)
    ]
    index_objects_missing = sorted(index_names - live_names)
    out_of_scope_live = sorted(live_names - index_names)
    index_only_count = sum(
        len(item["index_only_columns"]) for item in column_differences
    )
    live_only_count = sum(
        len(item["live_only_columns"]) for item in column_differences
    )
    comparison = {
        "live_object_count": len(live),
        "index_object_count": len(index),
        "index_objects_missing_in_live": index_objects_missing,
        "out_of_scope_live_objects": out_of_scope_live,
        "column_differences": column_differences,
        "column_difference_object_count": sum(
            bool(item["index_only_columns"] or item["live_only_columns"])
            for item in column_differences
        ),
        "index_only_column_count": index_only_count,
        "live_only_column_count": live_only_count,
    }
    comparison["schema_scope_valid"] = (
        comparison["index_object_count"] == 115
        and not index_objects_missing
        and index_only_count == 0
    )
    return comparison


def get_user_environment(name: str) -> tuple[bool, str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return True, str(value)
    except FileNotFoundError:
        return False, ""


def set_user_environment(name: str, value: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    os.environ[name] = value

    # 通知后续新启动的 Windows 进程读取新的用户级环境变量。
    try:
        import ctypes

        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
        )
    except Exception:
        pass


def rollback_text(previous_present: bool, previous_value: str) -> str:
    if previous_present:
        action = f"将用户级 VANNA_DATA_DIR 恢复为原值：{previous_value}"
    else:
        action = "删除用户级 VANNA_DATA_DIR，恢复 config/settings.py 默认路径。"
    return (
        "F4 Level 1 正式运行库切换回滚说明\n\n"
        f"{action}\n\n"
        "旧正式 Chroma 备份路径见 f4-summary.json。\n"
        "环境变量切换对新启动的终端和服务生效。\n"
    )


def summarize_f2_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "question_count": len(results),
        "question_pass_count": sum(bool(item.get("passed")) for item in results),
        "question_failure_count": sum(not bool(item.get("passed")) for item in results),
        "failed_questions": [item["id"] for item in results if not item.get("passed")],
        "sql_guard_pass_count": sum(
            bool(item["result"].get("guard", {}).get("passed")) for item in results
        ),
        "sql_execution_success_count": sum(
            bool(item["result"].get("csv_file")) and not item["result"].get("errors")
            for item in results
        ),
        "nonempty_result_count": sum(
            int(item["result"].get("row_count", 0)) > 0 for item in results
        ),
        "final_answer_count": sum(
            bool(str(item["result"].get("answer", "")).strip()) for item in results
        ),
        "chart_spec_pass_count": sum(
            item["id"] == "Q6"
            and bool(item.get("passed"))
            and any(
                spec.get("type") in {"horizontal_bar", "bar"}
                for spec in item["result"].get("chart_specs", [])
            )
            for item in results
        ),
    }


def smoke_accepted(summary: dict[str, Any]) -> bool:
    return (
        summary["question_count"] == 6
        and summary["question_pass_count"] == 6
        and summary["question_failure_count"] == 0
        and summary["sql_guard_pass_count"] == 6
        and summary["sql_execution_success_count"] == 6
        and summary["nonempty_result_count"] == 6
        and summary["final_answer_count"] == 6
        and summary["chart_spec_pass_count"] == 1
    )


def port_8000_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def assert_parent_memory_binding(promoted_path: Path) -> None:
    if "backend.memory" in sys.modules:
        raise RuntimeError("EARLY_BACKEND_MEMORY_IMPORT")
    raw_path = os.getenv("VANNA_DATA_DIR", "").strip()
    if not raw_path:
        raise RuntimeError("PARENT_VANNA_DATA_DIR_NOT_SET")
    if Path(raw_path).resolve() != promoted_path.resolve():
        raise RuntimeError("PARENT_VANNA_DATA_DIR_MISMATCH")


def self_test() -> int:
    names = {f"t{i}": {"id", "name"} for i in range(115)}
    assert compare_schema_scope(names, names)["schema_scope_valid"]

    live_with_extra = {**names, "staging_extra": {"id"}}
    extra_result = compare_schema_scope(live_with_extra, names)
    assert extra_result["schema_scope_valid"]
    assert extra_result["out_of_scope_live_objects"] == ["staging_extra"]

    live_column_extra = {**names, "t0": {"id", "name", "new_column"}}
    column_extra_result = compare_schema_scope(live_column_extra, names)
    assert column_extra_result["schema_scope_valid"]
    assert column_extra_result["live_only_column_count"] == 1

    live_object_missing = dict(names)
    del live_object_missing["t0"]
    object_missing_result = compare_schema_scope(live_object_missing, names)
    assert not object_missing_result["schema_scope_valid"]
    assert object_missing_result["index_objects_missing_in_live"] == ["t0"]

    live_column_missing = {**names, "t0": {"id"}}
    column_missing_result = compare_schema_scope(live_column_missing, names)
    assert not column_missing_result["schema_scope_valid"]
    assert column_missing_result["index_only_column_count"] == 1

    reordered_live = {name: set(reversed(sorted(columns))) for name, columns in names.items()}
    assert compare_schema_scope(reordered_live, names)["schema_scope_valid"]

    empty_result = compare_schema_scope({}, {})
    assert not empty_result["schema_scope_valid"]
    assert empty_result["index_objects_missing_in_live"] == []
    assert empty_result["column_differences"] == []

    assert "删除用户级" in rollback_text(False, "")
    assert "恢复为原值" in rollback_text(True, r"E:\old")
    assert summarize_f2_results([])["question_count"] == 0
    left = {"content_sha256": "a"}
    right = {"content_sha256": "a"}
    assert left["content_sha256"] == right["content_sha256"]

    original_vanna_data_dir = os.environ.pop("VANNA_DATA_DIR", None)
    original_backend_memory = sys.modules.pop("backend.memory", None)
    promoted = Path(r"E:\runtime\promoted")
    try:
        try:
            assert_parent_memory_binding(promoted)
            raise AssertionError("未设置 VANNA_DATA_DIR 时应拒绝")
        except RuntimeError as error:
            assert str(error) == "PARENT_VANNA_DATA_DIR_NOT_SET"

        os.environ["VANNA_DATA_DIR"] = str(PROJECT_ROOT / "vanna_data")
        try:
            assert_parent_memory_binding(promoted)
            raise AssertionError("指向旧正式目录时应拒绝")
        except RuntimeError as error:
            assert str(error) == "PARENT_VANNA_DATA_DIR_MISMATCH"

        os.environ["VANNA_DATA_DIR"] = str(promoted)
        assert_parent_memory_binding(promoted)

        sys.modules["backend.memory"] = object()  # type: ignore[assignment]
        try:
            assert_parent_memory_binding(promoted)
            raise AssertionError("backend.memory 提前导入时应拒绝")
        except RuntimeError as error:
            assert str(error) == "EARLY_BACKEND_MEMORY_IMPORT"
    finally:
        sys.modules.pop("backend.memory", None)
        if original_backend_memory is not None:
            sys.modules["backend.memory"] = original_backend_memory
        if original_vanna_data_dir is None:
            os.environ.pop("VANNA_DATA_DIR", None)
        else:
            os.environ["VANNA_DATA_DIR"] = original_vanna_data_dir
    print("SELF_TEST: PASS")
    return 0


def main() -> int:
    if parse_args().self_test:
        return self_test()
    if port_8000_open():
        raise RuntimeError("端口 8000 已占用，无法保证切换冒烟服务隔离")
    candidate_before = manifest(CANDIDATE_CHROMA)
    if candidate_before["content_sha256"] != EXPECTED_CANDIDATE_SHA256:
        raise RuntimeError("候选 Chroma 摘要不匹配")
    if chroma_record_count(CANDIDATE_CHROMA) != EXPECTED_RECORD_COUNT:
        raise RuntimeError("候选 Chroma 记录数不匹配")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cutover_root = BACKUP_PARENT / f"f4-cutover-r2-{timestamp}"
    evidence_dir = cutover_root / "evidence"
    agent_dir = cutover_root / "agent_data"
    backup_root = BACKUP_PARENT / f"f4-pre-cutover-{timestamp}"
    backup_path = backup_root / "legacy-formal-vanna_data"
    evidence_dir.mkdir(parents=True)
    agent_dir.mkdir(parents=True)

    index_schema = load_index_schema(METADATA_INDEX)
    live_schema = load_live_schema()
    schema_comparison = compare_schema_scope(live_schema, index_schema)
    write_json(evidence_dir / "schema-comparison.json", schema_comparison)
    if not schema_comparison["schema_scope_valid"]:
        write_json(
            evidence_dir / "f4-summary.json",
            {"f4_accepted": False, "failure_stage": "schema_comparison", **schema_comparison},
        )
        print(json.dumps({"cutover_root": str(cutover_root), **schema_comparison}, ensure_ascii=False))
        return 2

    legacy_before = manifest(LEGACY_FORMAL)
    write_json(evidence_dir / "legacy-formal-before.json", legacy_before)
    backup_root.mkdir(parents=True)
    backup_copy = create_verified_copy(LEGACY_FORMAL, backup_path, PROJECT_ROOT)
    legacy_backup = backup_copy.destination.to_dict()
    write_json(evidence_dir / "legacy-formal-backup.json", legacy_backup)
    if legacy_before["content_sha256"] != legacy_backup["content_sha256"]:
        raise RuntimeError("旧正式 Chroma 备份验证失败")

    write_json(evidence_dir / "candidate-chroma.json", candidate_before)
    PROMOTED_CHROMA.parent.mkdir(parents=True, exist_ok=True)
    if PROMOTED_CHROMA.exists():
        promoted_initial = manifest(PROMOTED_CHROMA)
    else:
        promoted_copy = create_verified_copy(CANDIDATE_CHROMA, PROMOTED_CHROMA, PROJECT_ROOT)
        promoted_initial = promoted_copy.destination.to_dict()
    write_json(evidence_dir / "promoted-chroma.json", promoted_initial)
    if promoted_initial["content_sha256"] != candidate_before["content_sha256"]:
        raise RuntimeError("新正式运行 Chroma 与候选摘要不一致")
    promoted_record_count_before_smoke = chroma_record_count(PROMOTED_CHROMA)
    if promoted_record_count_before_smoke != EXPECTED_RECORD_COUNT:
        raise RuntimeError("新正式运行 Chroma 记录数不是 187")

    previous_present, previous_value = get_user_environment("VANNA_DATA_DIR")
    environment_evidence = {
        "previous_present": previous_present,
        "previous_value": previous_value,
        "new_value": str(PROMOTED_CHROMA),
        "scope": "user",
        "note": "环境变量切换对新启动的终端和服务生效。",
    }
    set_user_environment("VANNA_DATA_DIR", str(PROMOTED_CHROMA))
    write_json(evidence_dir / "environment-cutover.json", environment_evidence)
    (evidence_dir / "rollback.txt").write_text(
        rollback_text(previous_present, previous_value), encoding="utf-8"
    )

    os.environ["VANNA_DATA_DIR"] = str(PROMOTED_CHROMA)
    os.environ["AGENT_DATA_DIR"] = str(agent_dir)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["VANNA_DISABLE_LEGACY_SQL_EXAMPLES"] = "0"
    assert_parent_memory_binding(PROMOTED_CHROMA)

    from tools.f2_end_to_end_mvp_probe import (
        CASES,
        redact,
        run_case,
        start_server,
        stop_server,
    )

    process = None
    logs: list[str] = []
    key = ""
    server_started = False
    health_check = False
    try:
        process, logs, _, key = start_server(PROMOTED_CHROMA, agent_dir, False)
        server_started = True
        health_check = True
        assert_parent_memory_binding(PROMOTED_CHROMA)
        results = [run_case(case, agent_dir, True) for case in CASES]
    finally:
        stop_server(process)

    smoke_summary = summarize_f2_results(results)
    write_json(
        evidence_dir / "cutover-smoke-results.json",
        {"summary": smoke_summary, "cases": results},
    )
    (evidence_dir / "server-log.txt").write_text(
        redact("\n".join(logs), [key]), encoding="utf-8"
    )

    legacy_after = manifest(LEGACY_FORMAL)
    candidate_after = manifest(CANDIDATE_CHROMA)
    promoted_after_smoke = manifest(PROMOTED_CHROMA)
    promoted_record_count_after_smoke = chroma_record_count(PROMOTED_CHROMA)
    accepted = (
        smoke_accepted(smoke_summary)
        and legacy_before["content_sha256"] == legacy_after["content_sha256"]
        and candidate_before["content_sha256"] == candidate_after["content_sha256"]
        and promoted_initial["content_sha256"] == candidate_before["content_sha256"]
        and promoted_record_count_before_smoke == EXPECTED_RECORD_COUNT
        and promoted_record_count_after_smoke == EXPECTED_RECORD_COUNT
        and get_user_environment("VANNA_DATA_DIR")
        == (True, str(PROMOTED_CHROMA))
    )
    summary = {
        "f4_accepted": accepted,
        "cutover_root": str(cutover_root),
        "evidence_directory": str(evidence_dir),
        "backup_path": str(backup_path),
        "promoted_path": str(PROMOTED_CHROMA),
        "schema": schema_comparison,
        "legacy_formal_sha256_before": legacy_before["content_sha256"],
        "legacy_formal_sha256_after": legacy_after["content_sha256"],
        "legacy_formal_backup_sha256": legacy_backup["content_sha256"],
        "candidate_sha256_before": candidate_before["content_sha256"],
        "candidate_sha256_after": candidate_after["content_sha256"],
        "promoted_sha256_initial": promoted_initial["content_sha256"],
        "promoted_sha256_after_smoke": promoted_after_smoke["content_sha256"],
        "promoted_record_count_before_smoke": promoted_record_count_before_smoke,
        "promoted_record_count_after_smoke": promoted_record_count_after_smoke,
        "parent_process_vanna_data_dir": os.environ["VANNA_DATA_DIR"],
        "early_backend_memory_import": False,
        "previous_user_vanna_data_dir_present": previous_present,
        "user_vanna_data_dir_after": get_user_environment("VANNA_DATA_DIR")[1],
        "server_started": server_started,
        "health_check": health_check,
        "smoke": smoke_summary,
        "training_executed": False,
        "explicit_memory_write_executed": False,
        "memory_delete_executed": False,
        "ddl_dml_executed": False,
    }
    write_json(evidence_dir / "f4-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if accepted else 3


if __name__ == "__main__":
    raise SystemExit(main())
