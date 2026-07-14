"""0B-2A 确定性指纹、验证复制和恢复副本合成测试。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import training.sop.storage_snapshot as snapshot_module
from training.sop.storage_snapshot import (
    SnapshotError,
    UnsafePathError,
    build_directory_manifest,
    create_restore_rehearsal,
    create_verified_copy,
    verify_directory_equivalence,
)


CLI = PROJECT_ROOT / "tools" / "snapshot_training_store.py"


def make_synthetic_store(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (root / ".hidden").write_bytes(b"hidden\x00content")
    (root / "empty").mkdir()
    (root / "nested" / "deep").mkdir(parents=True)
    (root / "nested" / "data.bin").write_bytes(bytes(range(32)))
    (root / "nested" / "deep" / "说明.txt").write_text(
        "纯合成测试内容", encoding="utf-8"
    )


def raises_snapshot_error(callback, expected=SnapshotError) -> tuple[bool, str]:
    try:
        callback()
    except expected as error:
        return True, f"{type(error).__name__}: {error}"
    except Exception as error:
        return False, f"unexpected {type(error).__name__}: {error}"
    return False, "未抛出异常"


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=True,
    ).stdout


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=environment,
    )


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    status_before = git_status()

    with tempfile.TemporaryDirectory(prefix="training-store-snapshot-") as temp_name:
        temp_root = Path(temp_name)
        project_root = temp_root / "synthetic-project"
        project_root.mkdir()
        source_a = temp_root / "sources" / "source-a"
        source_b = temp_root / "sources" / "source-b"
        make_synthetic_store(source_a)
        make_synthetic_store(source_b)

        manifest_a_first = build_directory_manifest(source_a)
        manifest_a_second = build_directory_manifest(source_a)
        results.append(
            (
                "相同内容重复摘要一致",
                manifest_a_first == manifest_a_second,
                manifest_a_first.content_sha256,
            )
        )

        manifest_b = build_directory_manifest(source_b)
        results.append(
            (
                "不同绝对路径的相同内容摘要一致",
                manifest_a_first.content_sha256 == manifest_b.content_sha256,
                f"a={manifest_a_first.content_sha256}; b={manifest_b.content_sha256}",
            )
        )

        manifest_json = json.dumps(
            manifest_a_first.to_dict(), ensure_ascii=False, sort_keys=True
        )
        results.append(
            (
                "清单不包含根绝对路径或运行时字段",
                str(source_a) not in manifest_json
                and "generated" not in manifest_json
                and "hostname" not in manifest_json
                and "username" not in manifest_json,
                manifest_json,
            )
        )

        original_b = manifest_b.content_sha256
        (source_b / "alpha.txt").write_text("changed\n", encoding="utf-8")
        changed_content = build_directory_manifest(source_b)
        results.append(
            (
                "文件内容变化导致摘要变化",
                changed_content.content_sha256 != original_b,
                changed_content.content_sha256,
            )
        )

        source_path_change = temp_root / "sources" / "path-change"
        make_synthetic_store(source_path_change)
        before_path_change = build_directory_manifest(source_path_change)
        (source_path_change / "alpha.txt").rename(source_path_change / "renamed.txt")
        after_path_change = build_directory_manifest(source_path_change)
        results.append(
            (
                "文件相对路径变化导致摘要变化",
                before_path_change.content_sha256
                != after_path_change.content_sha256,
                after_path_change.content_sha256,
            )
        )

        source_empty_change = temp_root / "sources" / "empty-change"
        make_synthetic_store(source_empty_change)
        before_empty_change = build_directory_manifest(source_empty_change)
        (source_empty_change / "another-empty").mkdir()
        after_empty_change = build_directory_manifest(source_empty_change)
        results.append(
            (
                "空目录变化导致摘要变化",
                before_empty_change.content_sha256
                != after_empty_change.content_sha256
                and "another-empty" in after_empty_change.directories,
                str(after_empty_change.directories),
            )
        )

        file_paths = [item.path for item in manifest_a_first.files]
        results.append(
            (
                "隐藏文件进入清单",
                ".hidden" in file_paths,
                str(file_paths),
            )
        )
        results.append(
            (
                "文件和目录排序稳定",
                list(manifest_a_first.directories)
                == sorted(manifest_a_first.directories)
                and file_paths == sorted(file_paths),
                f"directories={manifest_a_first.directories}; files={file_paths}",
            )
        )

        link_source = temp_root / "sources" / "link-source"
        make_synthetic_store(link_source)
        link_path = link_source / "synthetic-link"
        link_detail = ""
        try:
            os.symlink(link_source / "alpha.txt", link_path)
        except (OSError, NotImplementedError) as error:
            marker = link_source / "alpha.txt"
            marker_stat = marker.lstat()
            marker_identity = (marker_stat.st_dev, marker_stat.st_ino)
            original_reparse_check = snapshot_module._is_reparse_point

            def fake_reparse(stat_result):
                identity = (stat_result.st_dev, stat_result.st_ino)
                return identity == marker_identity or original_reparse_check(stat_result)

            with mock.patch.object(
                snapshot_module, "_is_reparse_point", side_effect=fake_reparse
            ):
                link_rejected, detail = raises_snapshot_error(
                    lambda: build_directory_manifest(link_source), UnsafePathError
                )
            link_detail = f"mocked reparse point after {type(error).__name__}: {detail}"
        else:
            link_rejected, detail = raises_snapshot_error(
                lambda: build_directory_manifest(link_source), UnsafePathError
            )
            link_detail = f"real symlink: {detail}"
            link_path.unlink()
        results.append(("符号链接或 reparse point 被拒绝", link_rejected, link_detail))

        repository_destination = project_root / "forbidden-backup"
        project_rejected, project_detail = raises_snapshot_error(
            lambda: create_verified_copy(
                source_a, repository_destination, project_root
            ),
            UnsafePathError,
        )
        results.append(
            ("目标位于项目仓库内时被拒绝", project_rejected, project_detail)
        )

        inside_source_destination = source_a / "forbidden-inside"
        inside_rejected, inside_detail = raises_snapshot_error(
            lambda: create_verified_copy(
                source_a, inside_source_destination, project_root
            ),
            UnsafePathError,
        )
        results.append(
            ("目标位于源目录内时被拒绝", inside_rejected, inside_detail)
        )

        existing_destination = temp_root / "copies" / "already-exists"
        existing_destination.mkdir(parents=True)
        existing_rejected, existing_detail = raises_snapshot_error(
            lambda: create_verified_copy(
                source_a, existing_destination, project_root
            ),
            UnsafePathError,
        )
        results.append(
            ("已存在目标目录被拒绝", existing_rejected, existing_detail)
        )

        missing_rejected, missing_detail = raises_snapshot_error(
            lambda: build_directory_manifest(temp_root / "missing-source"),
            UnsafePathError,
        )
        file_source = temp_root / "not-a-directory.txt"
        file_source.write_text("not directory", encoding="utf-8")
        file_rejected, file_detail = raises_snapshot_error(
            lambda: build_directory_manifest(file_source), UnsafePathError
        )
        results.append(
            (
                "不存在和非目录源路径被拒绝",
                missing_rejected and file_rejected,
                f"missing={missing_detail}; file={file_detail}",
            )
        )

        verified_source = temp_root / "sources" / "verified-source"
        make_synthetic_store(verified_source)
        backup_destination = temp_root / "copies" / "verified-backup"
        backup_result = create_verified_copy(
            verified_source, backup_destination, project_root
        )
        backup_equivalence = verify_directory_equivalence(
            verified_source, backup_destination
        )
        results.append(
            (
                "验证备份与源目录摘要一致",
                backup_equivalence.equivalent
                and backup_result.source_before == backup_result.source_after
                and backup_result.source_before.content_sha256
                == backup_result.destination.content_sha256,
                backup_result.destination.content_sha256,
            )
        )
        results.append(
            (
                "最终目标仅在验证后发布且无临时目录",
                backup_destination.is_dir()
                and not list(backup_destination.parent.glob(".snapshot-*")),
                str(backup_destination),
            )
        )

        restored_destination = temp_root / "copies" / "restored-copy"
        restore_result = create_restore_rehearsal(
            verified_source,
            backup_destination,
            restored_destination,
            project_root,
        )
        three_digests = {
            restore_result.source.content_sha256,
            restore_result.backup.content_sha256,
            restore_result.restored.content_sha256,
        }
        results.append(
            (
                "恢复副本与源、备份三方一致",
                len(three_digests) == 1 and restored_destination.is_dir(),
                str(three_digests),
            )
        )

        unstable_source = temp_root / "sources" / "unstable-source"
        make_synthetic_store(unstable_source)
        unstable_destination = temp_root / "copies" / "must-not-publish"
        hook_calls: list[str] = []

        def mutate_source(source: Path) -> None:
            hook_calls.append("called")
            (source / "alpha.txt").write_text(
                "deterministic mutation during copy\n", encoding="utf-8"
            )

        mutation_rejected, mutation_detail = raises_snapshot_error(
            lambda: create_verified_copy(
                unstable_source,
                unstable_destination,
                project_root,
                _after_copy_hook=mutate_source,
            )
        )
        results.append(
            (
                "复制期间源变化被确定性检测",
                mutation_rejected and hook_calls == ["called"],
                mutation_detail,
            )
        )
        results.append(
            (
                "源变化失败后无最终目标或临时副本",
                not unstable_destination.exists()
                and not list(unstable_destination.parent.glob(".snapshot-*")),
                str(unstable_destination),
            )
        )

        cli_source = temp_root / "cli" / "source"
        make_synthetic_store(cli_source)
        cli_backup = temp_root / "cli" / "backup"
        cli_restore = temp_root / "cli" / "restore"
        cli_manifest = run_cli("manifest", str(cli_source))
        cli_backup_result = run_cli("backup", str(cli_source), str(cli_backup))
        cli_verify = run_cli("verify", str(cli_source), str(cli_backup))
        cli_restore_result = run_cli(
            "restore-rehearsal",
            str(cli_source),
            str(cli_backup),
            str(cli_restore),
        )
        cli_ok = (
            cli_manifest.returncode == 0
            and cli_backup_result.returncode == 0
            and cli_verify.returncode == 0
            and cli_restore_result.returncode == 0
            and cli_backup.is_dir()
            and cli_restore.is_dir()
        )
        results.append(
            (
                "CLI 四个显式路径子命令通过",
                cli_ok,
                str(
                    [
                        cli_manifest.returncode,
                        cli_backup_result.returncode,
                        cli_verify.returncode,
                        cli_restore_result.returncode,
                    ]
                ),
            )
        )

    status_after = git_status()
    results.append(
        (
            "测试前后 Git 工作区状态不变",
            status_before == status_after,
            f"before={status_before!r}; after={status_after!r}",
        )
    )

    forbidden_modules = sorted(
        name
        for name in sys.modules
        if name == "chromadb"
        or name.startswith("chromadb.")
        or name == "psycopg2"
        or name.startswith("psycopg2.")
        or name == "sqlite3"
        or name.startswith("sqlite3.")
        or name.startswith("vanna.capabilities.agent_memory")
    )
    results.append(
        (
            "验证过程未导入 Chroma、数据库或 Memory 写入模块",
            not forbidden_modules,
            str(forbidden_modules),
        )
    )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
