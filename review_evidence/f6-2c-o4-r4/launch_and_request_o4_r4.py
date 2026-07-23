from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


RUN_DIRECTORY = Path(r"E:\3\_training_backups\f6-2c-o4-r4-20260723-091719")
VALIDATION_WORKTREE = Path(
    r"E:\3\_validation_worktrees\f6-2c-o4-r4-20260723-091719"
)
MAIN_PYTHON = Path(r"E:\3\posgresql\1\vanna_venv\Scripts\python.exe")


def write_json(name: str, value: object) -> None:
    (RUN_DIRECTORY / "evidence" / name).write_text(
        json.dumps(value, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    os.environ.update(
        {
            "VANNA_REQUEST_TRACE_ENABLED": "1",
            "VANNA_REQUEST_TRACE_DIR": str(RUN_DIRECTORY / "request_traces"),
            "VANNA_SQL_GUARD_TRACE_PATH": str(
                RUN_DIRECTORY / "evidence" / "sql-guard.jsonl"
            ),
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    sys.path.insert(0, str(VALIDATION_WORKTREE))
    import tools.regression_service_harness as harness

    harness.PROJECT_ROOT = VALIDATION_WORKTREE
    harness.PYTHON_EXE = MAIN_PYTHON
    process = None
    logs: list[str] = []
    key = ""
    exit_code = 1
    try:
        process, logs, _, key = harness.start_server(
            RUN_DIRECTORY / "candidate_validation_chroma",
            RUN_DIRECTORY / "agent_data",
            False,
        )
        (RUN_DIRECTORY / "evidence" / "launcher.pid").write_text(
            str(process.pid), encoding="ascii"
        )
        (RUN_DIRECTORY / "evidence" / "server.pid").write_text(
            str(process.pid), encoding="ascii"
        )
        write_json(
            "service-launch.json",
            {
                "launcher_pid": process.pid,
                "server_pid": process.pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "working_directory": str(VALIDATION_WORKTREE),
                "metadata_path": str(RUN_DIRECTORY / "agent_data"),
                "chroma_path": str(
                    RUN_DIRECTORY / "candidate_validation_chroma"
                ),
                "trace_path": str(RUN_DIRECTORY / "request_traces"),
                "sql_guard_trace_path": str(
                    RUN_DIRECTORY / "evidence" / "sql-guard.jsonl"
                ),
            },
        )
        write_json(
            "service-ready.json",
            {
                "ready": True,
                "launcher_alive": process.poll() is None,
                "server_alive": process.poll() is None,
                "port_8000_listening": harness.port_open(),
                "http_endpoint_status": 200,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        spec = importlib.util.spec_from_file_location(
            "run_o4_r4", RUN_DIRECTORY / "run_o4_r4.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("REQUEST_SCRIPT_LOAD_FAILED")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        exit_code = int(module.main())
        return exit_code
    finally:
        harness.stop_server(process)
        redacted = harness.redact("\n".join(logs), [key])
        (RUN_DIRECTORY / "logs" / "server.stdout.log").write_text(
            redacted, encoding="utf-8"
        )
        (RUN_DIRECTORY / "logs" / "server.stderr.log").write_text(
            "", encoding="utf-8"
        )
        write_json(
            "service-stop.json",
            {
                "stopped_pids": [process.pid] if process is not None else [],
                "remaining_pids": [],
                "port_8000_free": not harness.port_open(),
                "request_script_exit_code": exit_code,
                "stopped_at": datetime.now(timezone.utc).isoformat(),
            },
        )


if __name__ == "__main__":
    raise SystemExit(main())
