"""小助手应用注册表 CLI 脱敏与退出码离线测试。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "tools" / "assistant_app_admin.py"


def run_cli(
    db_path: Path,
    *arguments: str,
    environ: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = dict(os.environ if environ is None else environ)
    process_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--db-path",
            str(db_path),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assistant-cli-") as temp_name:
        db_path = Path(temp_name) / "assistant-apps.sqlite3"
        assert run_cli(db_path, "init").returncode == 0

        created = run_cli(
            db_path,
            "create",
            "--app-id",
            "cli-app",
            "--name",
            "CLI assistant",
            "--origin",
            "http://127.0.0.1:5174",
            "--source-id",
            "postgresql-main",
        )
        assert created.returncode == 0
        # No secret in output
        assert "secret" not in created.stdout.lower()

        listed = run_cli(db_path, "list")
        shown = run_cli(db_path, "show", "--app-id", "cli-app")
        assert listed.returncode == 0 and shown.returncode == 0
        # No secret in any output
        assert "secret" not in listed.stdout.lower()
        assert "secret" not in shown.stdout.lower()

        updated = run_cli(
            db_path,
            "update",
            "--app-id",
            "cli-app",
            "--name",
            "Updated CLI assistant",
            "--hide-history",
        )
        assert updated.returncode == 0

        assert run_cli(
            db_path,
            "disable",
            "--app-id",
            "cli-app",
        ).returncode == 0

        assert run_cli(
            db_path,
            "enable",
            "--app-id",
            "cli-app",
        ).returncode == 0

        # Delete test
        assert run_cli(
            db_path,
            "delete",
            "--app-id",
            "cli-app",
        ).returncode == 0

        # Rotate and bootstrap-env commands removed
        rotate_result = run_cli(
            db_path,
            "rotate-secret",
            "--app-id",
            "cli-app",
        )
        assert rotate_result.returncode != 0

        bootstrap_result = run_cli(
            db_path,
            "bootstrap-env",
        )
        assert bootstrap_result.returncode != 0

        invalid = run_cli(
            db_path,
            "create",
            "--app-id",
            "bad app id",
            "--name",
            "Invalid",
        )
        assert invalid.returncode != 0
        assert "Traceback" not in invalid.stderr

    print("assistant application admin CLI: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
