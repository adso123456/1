"""训练数据目录的确定性指纹、验证复制与恢复副本演练。"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any


MANIFEST_VERSION = "1.0"
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class SnapshotError(RuntimeError):
    """目录快照基础错误。"""


class UnsafePathError(SnapshotError):
    """路径、链接或文件类型不符合安全要求。"""


class SourceChangedError(SnapshotError):
    """源目录在清单或复制期间发生变化。"""


class VerificationError(SnapshotError):
    """两个目录的规范化清单不一致。"""


class PublishConflictError(SnapshotError):
    """最终目标在原子发布时已经存在。"""


class UnsupportedPlatformError(SnapshotError):
    """当前平台缺少可靠的原子 no-replace 目录发布原语。"""


@dataclass(frozen=True)
class FileManifestEntry:
    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DirectoryManifest:
    manifest_version: str
    directory_count: int
    file_count: int
    total_bytes: int
    directories: tuple[str, ...]
    files: tuple[FileManifestEntry, ...]
    content_sha256: str

    def content_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "directory_count": self.directory_count,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "directories": list(self.directories),
            "files": [item.to_dict() for item in self.files],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.content_dict(), "content_sha256": self.content_sha256}


@dataclass(frozen=True)
class EquivalenceResult:
    equivalent: bool
    left: DirectoryManifest
    right: DirectoryManifest
    differences: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedCopyResult:
    source_before: DirectoryManifest
    source_after: DirectoryManifest
    destination: DirectoryManifest


@dataclass(frozen=True)
class RestoreRehearsalResult:
    source: DirectoryManifest
    backup: DirectoryManifest
    restored: DirectoryManifest


@dataclass(frozen=True)
class _PreparedCopy:
    source_path: Path
    destination_path: Path
    temporary_path: Path
    source_before: DirectoryManifest
    source_after: DirectoryManifest
    temporary_manifest: DirectoryManifest


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _validate_node(path: Path, stat_result: os.stat_result) -> None:
    if stat.S_ISLNK(stat_result.st_mode) or _is_reparse_point(stat_result):
        raise UnsafePathError(f"拒绝符号链接或 reparse point: {path}")


def _validate_directory_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        root_stat = candidate.lstat()
    except FileNotFoundError as error:
        raise UnsafePathError(f"源目录不存在: {candidate}") from error
    _validate_node(candidate, root_stat)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise UnsafePathError(f"源路径不是目录: {candidate}")
    return candidate.resolve(strict=True)


def _node_identity(stat_result: os.stat_result) -> tuple[int, int] | None:
    inode = getattr(stat_result, "st_ino", 0)
    if not inode:
        return None
    return (getattr(stat_result, "st_dev", 0), inode)


def _same_opened_file(
    expected: os.stat_result, opened: os.stat_result
) -> bool:
    expected_identity = _node_identity(expected)
    opened_identity = _node_identity(opened)
    if expected_identity is not None and opened_identity is not None:
        return expected_identity == opened_identity
    return stat.S_IFMT(expected.st_mode) == stat.S_IFMT(opened.st_mode)


def _hash_regular_file(path: Path, expected: os.stat_result) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise UnsafePathError(f"无法安全打开普通文件: {path}") from error

    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        _validate_node(path, opened)
        if not stat.S_ISREG(opened.st_mode) or not _same_opened_file(expected, opened):
            raise UnsafePathError(f"文件类型或标识在打开时发生变化: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    stability_fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(opened, name, None) != getattr(after, name, None) for name in stability_fields):
        raise SourceChangedError(f"文件在计算指纹期间发生变化: {path}")
    return opened.st_size, digest.hexdigest()


def build_directory_manifest(root: str | Path) -> DirectoryManifest:
    """生成不含根绝对路径和运行时信息的确定性目录清单。"""

    root_path = _validate_directory_root(root)
    directories = ["."]
    files: list[FileManifestEntry] = []
    seen_directories: set[tuple[int, int]] = set()

    def visit(current: Path) -> None:
        current_stat = current.lstat()
        _validate_node(current, current_stat)
        if not stat.S_ISDIR(current_stat.st_mode):
            raise UnsafePathError(f"遍历节点不是目录: {current}")
        identity = _node_identity(current_stat)
        if identity is not None:
            if identity in seen_directories:
                raise UnsafePathError(f"检测到循环目录: {current}")
            seen_directories.add(identity)

        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError as error:
            raise UnsafePathError(f"无法读取目录: {current}") from error

        for entry in entries:
            path = Path(entry.path)
            entry_stat = entry.stat(follow_symlinks=False)
            _validate_node(path, entry_stat)
            try:
                relative = path.relative_to(root_path).as_posix()
            except ValueError as error:
                raise UnsafePathError(f"目录项越过源目录边界: {path}") from error
            if stat.S_ISDIR(entry_stat.st_mode):
                directories.append(relative)
                visit(path)
            elif stat.S_ISREG(entry_stat.st_mode):
                size, sha256 = _hash_regular_file(path, entry_stat)
                files.append(FileManifestEntry(relative, size, sha256))
            else:
                raise UnsafePathError(f"拒绝非普通文件: {path}")

    visit(root_path)
    sorted_directories = tuple(sorted(directories))
    sorted_files = tuple(sorted(files, key=lambda item: item.path))
    content = {
        "manifest_version": MANIFEST_VERSION,
        "directory_count": len(sorted_directories),
        "file_count": len(sorted_files),
        "total_bytes": sum(item.size for item in sorted_files),
        "directories": list(sorted_directories),
        "files": [item.to_dict() for item in sorted_files],
    }
    content_sha256 = hashlib.sha256(_canonical_json(content)).hexdigest()
    return DirectoryManifest(
        manifest_version=MANIFEST_VERSION,
        directory_count=content["directory_count"],
        file_count=content["file_count"],
        total_bytes=content["total_bytes"],
        directories=sorted_directories,
        files=sorted_files,
        content_sha256=content_sha256,
    )


def _manifest_differences(
    left: DirectoryManifest, right: DirectoryManifest
) -> tuple[str, ...]:
    differences: list[str] = []
    for field_name in (
        "manifest_version",
        "directory_count",
        "file_count",
        "total_bytes",
        "directories",
        "files",
        "content_sha256",
    ):
        if getattr(left, field_name) != getattr(right, field_name):
            differences.append(field_name)
    return tuple(differences)


def verify_directory_equivalence(
    left: str | Path, right: str | Path
) -> EquivalenceResult:
    left_manifest = build_directory_manifest(left)
    right_manifest = build_directory_manifest(right)
    differences = _manifest_differences(left_manifest, right_manifest)
    return EquivalenceResult(
        equivalent=not differences,
        left=left_manifest,
        right=right_manifest,
        differences=differences,
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_destination(
    source: Path, destination: str | Path, project_root: str | Path
) -> Path:
    project = _validate_directory_root(project_root)
    raw_destination = Path(destination).expanduser()
    if os.path.lexists(raw_destination):
        raise UnsafePathError(f"目标目录已经存在: {raw_destination}")

    raw_parent = raw_destination.parent
    try:
        parent_stat = raw_parent.lstat()
    except FileNotFoundError as error:
        raise UnsafePathError(f"目标父目录不存在: {raw_parent}") from error
    _validate_node(raw_parent, parent_stat)
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise UnsafePathError(f"目标父路径不是目录: {raw_parent}")
    parent = raw_parent.resolve(strict=True)
    destination_path = parent / raw_destination.name

    if _is_relative_to(destination_path, project):
        raise UnsafePathError(f"目标目录必须位于项目仓库之外: {destination_path}")
    if destination_path == source:
        raise UnsafePathError("目标目录不得与源目录相同")
    if _is_relative_to(destination_path, source):
        raise UnsafePathError("目标目录不得位于源目录内部")
    if _is_relative_to(source, destination_path):
        raise UnsafePathError("目标目录不得是源目录的父目录")
    if not os.access(parent, os.W_OK):
        raise UnsafePathError(f"目标父目录不可写: {parent}")
    return destination_path


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise UnsafePathError(f"清单包含越界相对路径: {relative}")
    return tuple(part for part in path.parts if part not in {"", "."})


def _copy_regular_file(source: Path, destination: Path) -> None:
    source_stat = source.lstat()
    _validate_node(source, source_stat)
    if not stat.S_ISREG(source_stat.st_mode):
        raise UnsafePathError(f"复制源不是普通文件: {source}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    try:
        opened = os.fstat(descriptor)
        _validate_node(source, opened)
        if not stat.S_ISREG(opened.st_mode) or not _same_opened_file(source_stat, opened):
            raise UnsafePathError(f"复制源在打开时发生类型或标识变化: {source}")
        with os.fdopen(descriptor, "rb", closefd=False) as source_stream:
            with destination.open("xb") as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if any(
        getattr(opened, name, None) != getattr(after, name, None)
        for name in ("st_size", "st_mtime_ns", "st_ctime_ns")
    ):
        raise SourceChangedError(f"文件在复制期间发生变化: {source}")


def _copy_manifest_content(
    source: Path, temporary: Path, manifest: DirectoryManifest
) -> None:
    for relative in manifest.directories:
        parts = _relative_parts(relative)
        if parts:
            (temporary.joinpath(*parts)).mkdir(parents=True, exist_ok=False)
    for entry in manifest.files:
        parts = _relative_parts(entry.path)
        if not parts:
            raise UnsafePathError("文件相对路径不得为空")
        _copy_regular_file(source.joinpath(*parts), temporary.joinpath(*parts))


def _remove_temporary_directory(temporary: Path, expected_parent: Path) -> None:
    if not os.path.lexists(temporary):
        return
    parent = temporary.parent.resolve(strict=True)
    if parent != expected_parent or not temporary.name.startswith(".snapshot-"):
        raise UnsafePathError(f"拒绝清理非本次临时目录: {temporary}")
    node_stat = temporary.lstat()
    if stat.S_ISLNK(node_stat.st_mode) or _is_reparse_point(node_stat):
        temporary.unlink()
        return
    if not stat.S_ISDIR(node_stat.st_mode):
        raise UnsafePathError(f"临时路径不是目录: {temporary}")
    shutil.rmtree(temporary)


def _publish_directory_no_replace(temporary: Path, destination: Path) -> None:
    """使用平台原子原语发布目录；不支持时失败关闭。"""

    temporary_parent = temporary.parent.resolve(strict=True)
    destination_parent = destination.parent.resolve(strict=True)
    if temporary_parent != destination_parent:
        raise UnsafePathError("临时目录与最终目标必须位于同一父目录")
    temporary_stat = temporary.lstat()
    _validate_node(temporary, temporary_stat)
    if not stat.S_ISDIR(temporary_stat.st_mode):
        raise UnsafePathError(f"待发布路径不是普通目录: {temporary}")

    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file = kernel32.MoveFileExW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move_file.restype = ctypes.c_int
        if move_file(str(temporary), str(destination), 0):
            return
        error_code = ctypes.get_last_error()
        if error_code in {5, 80, 183} and os.path.lexists(destination):
            raise PublishConflictError(f"原子发布时目标已经存在: {destination}")
        raise OSError(error_code, ctypes.FormatError(error_code), str(destination))

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename_no_replace = getattr(libc, "renameat2", None)
        if rename_no_replace is None:
            raise UnsupportedPlatformError("当前 Linux C 库不提供 renameat2")
        rename_no_replace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        at_fdcwd = -100
        rename_noreplace = 1
        if (
            rename_no_replace(
                at_fdcwd,
                os.fsencode(temporary),
                at_fdcwd,
                os.fsencode(destination),
                rename_noreplace,
            )
            == 0
        ):
            return
        error_code = ctypes.get_errno()
        if error_code in {errno.EEXIST, errno.ENOTEMPTY}:
            raise PublishConflictError(f"原子发布时目标已经存在: {destination}")
        if error_code in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise UnsupportedPlatformError(
                "当前 Linux 系统或文件系统不支持 renameat2 RENAME_NOREPLACE"
            )
        raise OSError(error_code, os.strerror(error_code), str(destination))

    raise UnsupportedPlatformError(
        f"平台 {sys.platform!r} 缺少已实现的原子 no-replace 目录发布原语"
    )


def _prepare_verified_copy(
    source: str | Path,
    destination: str | Path,
    project_root: str | Path,
    *,
    after_copy_hook: Callable[[Path], None] | None = None,
) -> _PreparedCopy:
    source_path = _validate_directory_root(source)
    destination_path = _validate_destination(source_path, destination, project_root)
    destination_parent = destination_path.parent
    source_before = build_directory_manifest(source_path)
    temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=destination_parent))
    try:
        _copy_manifest_content(source_path, temporary, source_before)
        if after_copy_hook is not None:
            after_copy_hook(source_path)
        source_after = build_directory_manifest(source_path)
        temporary_manifest = build_directory_manifest(temporary)
        if _manifest_differences(source_before, source_after):
            raise SourceChangedError("源目录在复制期间发生变化")
        if _manifest_differences(source_before, temporary_manifest):
            raise VerificationError("临时副本与源目录清单不一致")
        return _PreparedCopy(
            source_path=source_path,
            destination_path=destination_path,
            temporary_path=temporary,
            source_before=source_before,
            source_after=source_after,
            temporary_manifest=temporary_manifest,
        )
    except Exception:
        _remove_temporary_directory(temporary, destination_parent)
        raise


def create_verified_copy(
    source: str | Path,
    destination: str | Path,
    project_root: str | Path,
    *,
    _after_copy_hook: Callable[[Path], None] | None = None,
    _before_publish_hook: Callable[[Path], None] | None = None,
) -> VerifiedCopyResult:
    """复制到临时目录，经三方清单验证后发布全新目标目录。"""

    prepared = _prepare_verified_copy(
        source,
        destination,
        project_root,
        after_copy_hook=_after_copy_hook,
    )
    try:
        if _before_publish_hook is not None:
            _before_publish_hook(prepared.destination_path)
        _publish_directory_no_replace(
            prepared.temporary_path, prepared.destination_path
        )
        final_manifest = build_directory_manifest(prepared.destination_path)
        if _manifest_differences(prepared.source_before, final_manifest):
            raise VerificationError("最终目标与源目录清单不一致")
        return VerifiedCopyResult(
            source_before=prepared.source_before,
            source_after=prepared.source_after,
            destination=final_manifest,
        )
    except Exception:
        _remove_temporary_directory(
            prepared.temporary_path, prepared.destination_path.parent
        )
        raise


def create_restore_rehearsal(
    source: str | Path,
    backup: str | Path,
    restored_destination: str | Path,
    project_root: str | Path,
    *,
    _before_final_validation_hook: Callable[[Path, Path], None] | None = None,
    _before_publish_hook: Callable[[Path], None] | None = None,
) -> RestoreRehearsalResult:
    """三方验证临时恢复副本后，以 no-replace 原子发布最终目录。"""

    source_backup = verify_directory_equivalence(source, backup)
    if not source_backup.equivalent:
        raise VerificationError(
            "源目录与备份不一致: " + ", ".join(source_backup.differences)
        )
    source_path = _validate_directory_root(source)
    backup_path = _validate_directory_root(backup)
    prepared = _prepare_verified_copy(
        backup_path, restored_destination, project_root
    )
    try:
        if _before_final_validation_hook is not None:
            _before_final_validation_hook(source_path, backup_path)

        backup_final = build_directory_manifest(backup_path)
        source_final = build_directory_manifest(source_path)
        temporary_final = build_directory_manifest(prepared.temporary_path)
        if _manifest_differences(source_backup.left, source_final):
            raise SourceChangedError("原始源目录在恢复演练期间发生变化")
        if _manifest_differences(source_backup.right, prepared.source_before):
            raise SourceChangedError("备份在首次验证后、复制前发生变化")
        if _manifest_differences(source_backup.right, backup_final):
            raise SourceChangedError("备份在恢复演练期间发生变化")
        if _manifest_differences(prepared.temporary_manifest, temporary_final):
            raise SourceChangedError("临时恢复目录在三方验证前发生变化")
        if _manifest_differences(source_final, backup_final):
            raise VerificationError("最终源目录与备份清单不一致")
        if _manifest_differences(source_final, temporary_final):
            raise VerificationError("临时恢复目录与源目录清单不一致")

        if _before_publish_hook is not None:
            _before_publish_hook(prepared.destination_path)
        _publish_directory_no_replace(
            prepared.temporary_path, prepared.destination_path
        )
        restored_final = build_directory_manifest(prepared.destination_path)
        if _manifest_differences(source_final, restored_final):
            raise VerificationError("发布后的恢复目录与源目录清单不一致")
        return RestoreRehearsalResult(
            source=source_final,
            backup=backup_final,
            restored=restored_final,
        )
    except Exception:
        _remove_temporary_directory(
            prepared.temporary_path, prepared.destination_path.parent
        )
        raise
