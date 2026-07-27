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


def read_secret(output: str) -> str:
    lines = [
        line.removeprefix("app_secret: ").strip()
        for line in output.splitlines()
        if line.startswith("app_secret: ")
    ]
    assert len(lines) == 1 and len(lines[0]) >= 32
    return lines[0]


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
        created_secret = read_secret(created.stdout)
        assert created.stdout.count(created_secret) == 1

        listed = run_cli(db_path, "list")
        shown = run_cli(db_path, "show", "--app-id", "cli-app")
        assert listed.returncode == 0 and shown.returncode == 0
        assert created_secret not in listed.stdout
        assert created_secret not in shown.stdout
        assert "secret_mask:" in shown.stdout

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

        rotated = run_cli(
            db_path,
            "rotate-secret",
            "--app-id",
            "cli-app",
        )
        assert rotated.returncode == 0
        rotated_secret = read_secret(rotated.stdout)
        assert rotated_secret != created_secret
        assert created_secret not in rotated.stdout
        assert rotated.stdout.count(rotated_secret) == 1

        legacy_secret = "legacy-environment-secret-longer-than-32-characters"
        bootstrap_env = dict(os.environ)
        bootstrap_env.update(
            {
                "WATER_AGENT_EMBED_APP_ID": "legacy-app",
                "WATER_AGENT_EMBED_APP_SECRET": legacy_secret,
                "WATER_AGENT_EMBED_ENABLED": "true",
                "WATER_AGENT_EMBED_ALLOWED_ORIGINS":
                    "http://127.0.0.1:5174",
                "WATER_AGENT_EMBED_ALLOWED_SOURCE_IDS": "postgresql-main",
                "WATER_AGENT_EMBED_TOKEN_TTL_SECONDS": "300",
            }
        )
        bootstrapped = run_cli(
            db_path,
            "bootstrap-env",
            environ=bootstrap_env,
        )
        assert bootstrapped.returncode == 0
        assert legacy_secret not in bootstrapped.stdout
        duplicate_bootstrap = run_cli(
            db_path,
            "bootstrap-env",
            environ=bootstrap_env,
        )
        assert duplicate_bootstrap.returncode != 0
        assert legacy_secret not in (
            duplicate_bootstrap.stdout + duplicate_bootstrap.stderr
        )

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
